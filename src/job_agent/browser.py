"""Browser lifecycle management for Job-Agent.

Managed mode launches Playwright's Chromium with a dedicated local profile.
Chrome mode attaches to a user-launched Google Chrome instance via CDP and
never closes that browser or its context when Job-Agent finishes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BROWSER_PROFILE_DIR = PROJECT_ROOT / "data" / "chromium_profile"
CHROME_PROFILE_DIR = PROJECT_ROOT / "data" / "chrome_profile"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_CDP_PORT = 9222
LOGIN_HINTS = ("login", "signin", "sign-in", "authenticate", "session-expired")


def _validate_http_url(url: str, label: str = "URL") -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be a complete http:// or https:// URL")


def validate_cdp_url(cdp_url: str) -> str:
    """Accept only a localhost CDP endpoint to avoid remote browser access."""
    parsed = urlparse(cdp_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("CDP URL must use http:// and a loopback host")
    if parsed.hostname.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("For safety, Job-Agent only connects to a localhost CDP URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("CDP URL must contain only the local host and optional port")
    return cdp_url.rstrip("/")


def is_cdp_available(cdp_url: str = DEFAULT_CDP_URL) -> bool:
    """Return whether a local Chrome DevTools endpoint responds."""
    endpoint = validate_cdp_url(cdp_url) + "/json/version"
    try:
        with urlopen(endpoint, timeout=1.0) as response:
            return 200 <= response.status < 300
    except (OSError, URLError, ValueError):
        return False


def ensure_chrome_ready(
    cdp_url: str = DEFAULT_CDP_URL,
    *,
    start_url: str = "about:blank",
    timeout_seconds: float = 20.0,
) -> bool:
    """Connect to local Job-Agent Chrome or launch its persistent profile.

    Returns True when Chrome was launched by this call and False when an
    already-running endpoint was reused. Never connects to a non-local host.
    """
    endpoint = validate_cdp_url(cdp_url)
    if start_url != "about:blank":
        _validate_http_url(start_url, "Start URL")
    if is_cdp_available(endpoint):
        return False

    parsed = urlparse(endpoint)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    start_chrome(profile_dir=CHROME_PROFILE_DIR, port=port, url=start_url)

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        if is_cdp_available(endpoint):
            return True
        time.sleep(0.25)

    raise RuntimeError(
        f"Chrome did not expose its debugging endpoint at {endpoint}. "
        "Close any stale Job-Agent Chrome window and run 'job-agent browser' "
        "to start the persistent profile, then retry autofill."
    )


def find_chrome_executable() -> Path:
    """Find a Google Chrome executable on macOS, Windows, or Linux."""
    candidates: list[Path] = []
    if os.name == "nt":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif os.sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    executable_names = (
        ["chrome", "google-chrome", "google-chrome-stable"]
        if os.name == "nt"
        else ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]
    )
    for name in executable_names:
        executable = shutil.which(name)
        if executable:
            return Path(executable)

    raise RuntimeError(
        "Google Chrome was not found. Install Chrome or specify its executable "
        "using the system PATH before running 'job-agent browser'."
    )


def build_chrome_command(
    executable: str | Path,
    profile_dir: str | Path,
    port: int = DEFAULT_CDP_PORT,
    url: str = "https://accounts.google.com/",
) -> list[str]:
    """Build a safe Chrome launch command using a dedicated Job-Agent profile."""
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Chrome debugging port must be between 1 and 65535")
    if url != "about:blank":
        _validate_http_url(url, "Start URL")
    return [
        str(executable),
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={Path(profile_dir).expanduser().resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]


def start_chrome(
    profile_dir: str | Path | None = None,
    port: int = DEFAULT_CDP_PORT,
    url: str = "https://accounts.google.com/",
) -> bool:
    """Start Chrome with an isolated persistent profile and localhost CDP.

    Returns False if a CDP endpoint is already responding on the requested port.
    The default Chrome profile is intentionally not reused: recent Chrome
    versions restrict remote debugging of the normal user-data directory.
    """
    cdp_url = validate_cdp_url(f"http://127.0.0.1:{port}")
    if is_cdp_available(cdp_url):
        return False

    executable = find_chrome_executable()
    data_dir = Path(profile_dir).expanduser() if profile_dir else CHROME_PROFILE_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    command = build_chrome_command(executable, data_dir, port=port, url=url)
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(command, **kwargs)
    return True


class BrowserManager:
    """Manage either a private Playwright browser or an attached Chrome session.

    Chrome/CDP mode creates a new tab in the attached browser. Closing the
    manager disconnects Playwright but leaves Chrome, its profile, cookies, and
    other tabs open. Do not run two managed instances against the same profile.
    """

    def __init__(
        self,
        profile_dir: str | Path | None = None,
        headless: bool = False,
        mode: str = "managed",
        cdp_url: str = DEFAULT_CDP_URL,
    ):
        if mode not in {"managed", "chrome"}:
            raise ValueError("Browser mode must be 'managed' or 'chrome'")
        if mode == "chrome" and headless:
            raise ValueError("Chrome/CDP mode is visible; remove --headless")
        self.profile_dir = Path(profile_dir).expanduser() if profile_dir else BROWSER_PROFILE_DIR
        self.headless = headless
        self.mode = mode
        self.cdp_url = validate_cdp_url(cdp_url)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._owns_context = False

    def launch_browser(self) -> Page:
        self._playwright = sync_playwright().start()
        try:
            if self.mode == "chrome":
                if not is_cdp_available(self.cdp_url):
                    raise RuntimeError(
                        f"No Chrome debugging endpoint found at {self.cdp_url}. "
                        "Run 'job-agent browser' first, then sign in to your career portals."
                    )
                self._browser = self._playwright.chromium.connect_over_cdp(
                    self.cdp_url,
                    timeout=5_000,
                )
                if not self._browser.contexts:
                    raise RuntimeError(
                        "Chrome is connected but has no browser context available. "
                        "Close and restart it with 'job-agent browser'."
                    )
                self._context = self._browser.contexts[0]
                self._page = self._context.new_page()
                self._owns_context = False
                return self._page

            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1440, "height": 950},
                accept_downloads=False,
            )
            self._owns_context = True
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            return self._page
        except Exception:
            self.close()
            raise

    def get_page(self) -> Page:
        if self._page is None:
            return self.launch_browser()
        return self._page

    def goto(self, url: str) -> Page:
        _validate_http_url(url, "Application URL")
        page = self.get_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            page.wait_for_load_state("networkidle", timeout=3_000)
        except PlaywrightTimeoutError:
            # Some ATS pages keep network connections open; DOM is already ready.
            pass
        if self.is_login_page():
            # After SSO/MFA the portal may land on a home page instead of
            # resuming the application. Always revisit the requested job URL.
            self.handle_timeout()
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except PlaywrightTimeoutError:
                pass
            if self.is_login_page():
                raise RuntimeError(
                    "The career portal is still showing its login page. "
                    "Finish sign-in in Chrome, then rerun autofill."
                )
        return page

    def is_login_page(self) -> bool:
        if self._page is None:
            return False
        current_url = self._page.url.lower()
        if any(hint in current_url for hint in LOGIN_HINTS):
            return True
        try:
            password = self._page.locator('input[type="password"]')
            return password.count() > 0 and any(
                word in self._page.title().lower() for word in ("sign in", "log in", "login")
            )
        except Exception:
            return False

    def handle_timeout(self) -> None:
        """Pause for user re-authentication, then resume after Enter."""
        print("\nA login/session page was detected. Complete login in the browser.")
        input("Press Enter here after you are logged in to continue...")
        if self._page is not None and self.is_login_page():
            print("The page still appears to be a login page; check the browser before continuing.")

    def close(self) -> None:
        # Never close a browser/context that Job-Agent attached to over CDP.
        if self._owns_context and self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        self._page = None
        self._browser = None
        self._owns_context = False
        if self._playwright is not None:
            try:
                # Stopping the Playwright driver disconnects from CDP; it does
                # not terminate the external Chrome process.
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __enter__(self) -> "BrowserManager":
        self.launch_browser()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
