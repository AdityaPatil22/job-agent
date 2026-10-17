"""Unit tests for Chrome/CDP browser configuration and lifecycle."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from job_agent.browser import (
    DEFAULT_CDP_URL,
    BrowserManager,
    build_chrome_command,
    ensure_chrome_ready,
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


def test_ensure_chrome_ready_starts_persistent_profile_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = iter([False, False, True])
    start = Mock(return_value=True)
    monkeypatch.setattr("job_agent.browser.is_cdp_available", lambda _url: next(probes))
    monkeypatch.setattr("job_agent.browser.start_chrome", start)
    monkeypatch.setattr("job_agent.browser.time.sleep", lambda _seconds: None)

    assert ensure_chrome_ready(DEFAULT_CDP_URL, start_url="https://jobs.example.com/role") is True

    start.assert_called_once()
    assert start.call_args.kwargs["port"] == 9222
    assert start.call_args.kwargs["url"] == "https://jobs.example.com/role"


def test_ensure_chrome_ready_reuses_existing_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = Mock()
    monkeypatch.setattr("job_agent.browser.is_cdp_available", lambda _url: True)
    monkeypatch.setattr("job_agent.browser.start_chrome", start)

    assert ensure_chrome_ready(DEFAULT_CDP_URL) is False
    start.assert_not_called()


def test_goto_retries_job_url_after_login_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BrowserManager(mode="managed")
    page = Mock()
    manager._page = page
    monkeypatch.setattr(manager, "get_page", lambda: page)
    monkeypatch.setattr(manager, "handle_timeout", Mock())
    states = iter([True, True, False])
    monkeypatch.setattr(manager, "is_login_page", lambda: next(states))

    result = manager.goto("https://jobs.example.com/application")

    assert result is page
    assert page.goto.call_count == 2
    assert page.goto.call_args_list[-1].args[0] == "https://jobs.example.com/application"
