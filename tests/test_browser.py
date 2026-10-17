"""Unit tests for Chrome/CDP browser configuration and lifecycle."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from job_agent.browser import (
    DEFAULT_CDP_URL,
    BrowserManager,
    build_chrome_command,
    is_cdp_available,
    validate_cdp_url,
)


def test_cdp_url_defaults_to_localhost() -> None:
    assert validate_cdp_url(DEFAULT_CDP_URL) == DEFAULT_CDP_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.20:9222",
        "http://example.com:9222",
        "file:///tmp/chrome",
    ],
)
def test_cdp_url_rejects_remote_or_invalid_hosts(url: str) -> None:
    with pytest.raises(ValueError):
        validate_cdp_url(url)


def test_build_chrome_command_uses_dedicated_profile_and_localhost(tmp_path: Path) -> None:
    command = build_chrome_command(
        "/usr/bin/google-chrome",
        tmp_path / "chrome-profile",
        port=9223,
        url="https://accounts.google.com/",
    )

    assert command[0] == "/usr/bin/google-chrome"
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=9223" in command
    assert f"--user-data-dir={(tmp_path / 'chrome-profile').resolve()}" in command
    assert command[-1] == "https://accounts.google.com/"


@pytest.mark.parametrize("port", [0, -1, 65536, "9222"])
def test_build_chrome_command_rejects_invalid_ports(tmp_path: Path, port: object) -> None:
    with pytest.raises(ValueError):
        build_chrome_command("/usr/bin/google-chrome", tmp_path / "profile", port=port)  # type: ignore[arg-type]


def test_build_chrome_command_rejects_non_http_start_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_chrome_command("/usr/bin/google-chrome", tmp_path / "profile", url="file:///tmp/page")


def test_cdp_probe_returns_false_when_endpoint_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_url_error(*args: object, **kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("job_agent.browser.urlopen", raise_url_error)
    assert is_cdp_available(DEFAULT_CDP_URL) is False


def test_chrome_mode_disconnect_does_not_close_user_browser() -> None:
    manager = BrowserManager(mode="chrome")
    context = Mock()
    playwright = Mock()
    manager._context = context
    manager._playwright = playwright
    manager._owns_context = False

    manager.close()

    context.close.assert_not_called()
    playwright.stop.assert_called_once_with()


def test_managed_mode_closes_its_owned_context() -> None:
    manager = BrowserManager(mode="managed")
    context = Mock()
    playwright = Mock()
    manager._context = context
    manager._playwright = playwright
    manager._owns_context = True

    manager.close()

    context.close.assert_called_once_with()
    playwright.stop.assert_called_once_with()
