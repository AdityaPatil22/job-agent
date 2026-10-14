"""Persistent Playwright Chromium session management."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BROWSER_PROFILE_DIR = PROJECT_ROOT / "data" / "chromium_profile"
LOGIN_HINTS = ("login", "signin", "sign-in", "authenticate", "session-expired")


class BrowserManager:
    """Own a persistent Chromium profile and the active page.

    The profile is local to this project. Do not run two instances against it at
    the same time because Chromium locks its profile directory.
    """

    def __init__(self, profile_dir: str | Path | None = None, headless: bool = False):
        self.profile_dir = Path(profile_dir).expanduser() if profile_dir else BROWSER_PROFILE_DIR
        self.headless = headless
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def launch_browser(self) -> Page:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1440, "height": 950},
                accept_downloads=False,
            )
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
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Please provide a complete http:// or https:// URL")
        page = self.get_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            page.wait_for_load_state("networkidle", timeout=3_000)
        except PlaywrightTimeoutError:
            # Some ATS pages keep network connections open; DOM is already ready.
            pass
        if self.is_login_page():
            self.handle_timeout()
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
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._page = None

    def __enter__(self) -> "BrowserManager":
        self.launch_browser()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
