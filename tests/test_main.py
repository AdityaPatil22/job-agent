"""CLI-level regression tests for the assisted autofill workflow."""

from __future__ import annotations

from unittest.mock import Mock

from click.testing import CliRunner

from job_agent import main


class FakeReport:
    resume_uploaded = True
    filled = ["Email address <- email"]
    skipped = []
    failed = []
    unknown = []

    def render(self) -> str:
        return "Form review report: one field filled"


class FakeBrowserManager:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.instances.append(self)

    def goto(self, url):
        self.url = url
        return object()

    def close(self):
        self.closed = True


def _patch_apply_dependencies(monkeypatch):
    FakeBrowserManager.instances = []
    monkeypatch.setattr(main, "init_db", Mock())
    monkeypatch.setattr(
        main,
        "get_job",
        Mock(return_value={"id": 7, "url": "https://jobs.example.com/role", "title": "Engineer", "company": "Example"}),
    )
    monkeypatch.setattr(main, "load_profile", Mock(return_value=object()))
    monkeypatch.setattr(main, "load_field_mappings", Mock(return_value={}))
    monkeypatch.setattr(main, "BrowserManager", FakeBrowserManager)
    monkeypatch.setattr(main, "ensure_chrome_ready", Mock(return_value=False))
    monkeypatch.setattr(main, "autofill_form", Mock(return_value=FakeReport()))
    monkeypatch.setattr(main, "wait_for_submit_button", Mock())
    monkeypatch.setattr(main, "update_application_status", Mock())


def test_chrome_autofill_finishes_without_blocking_for_enter(monkeypatch) -> None:
    _patch_apply_dependencies(monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        main.cli,
        ["apply", "--job-id", "7", "--resume", "candidate.pdf", "--browser", "chrome"],
    )

    assert result.exit_code == 0, result.output
    assert "Chrome tab will remain open" in result.output
    main.ensure_chrome_ready.assert_called_once()
    main.wait_for_submit_button.assert_not_called()
    main.update_application_status.assert_called_once()
    assert FakeBrowserManager.instances[0].closed is True


def test_managed_autofill_keeps_review_pause(monkeypatch) -> None:
    _patch_apply_dependencies(monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        main.cli,
        ["apply", "--job-id", "7", "--resume", "candidate.pdf", "--browser", "managed"],
    )

    assert result.exit_code == 0, result.output
    main.ensure_chrome_ready.assert_not_called()
    main.wait_for_submit_button.assert_called_once()
    main.update_application_status.assert_called_once()
