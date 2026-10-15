"""Tests for the Job-Agent CLI."""

from __future__ import annotations

from click.testing import CliRunner

from job_agent import dashboard
from job_agent import main as main_module


def test_start_command_uses_default_dashboard_options(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    captured: dict[str, object] = {}

    def fake_serve(*, host: str, port: int) -> None:
        captured.update(host=host, port=port)

    monkeypatch.setattr(dashboard, "serve", fake_serve)
    result = CliRunner().invoke(main_module.cli, ["start"])

    assert result.exit_code == 0, result.output
    assert captured == {"host": "127.0.0.1", "port": 8765}


def test_start_command_accepts_custom_port(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        dashboard,
        "serve",
        lambda *, host, port: captured.update(host=host, port=port),
    )

    result = CliRunner().invoke(
        main_module.cli,
        ["start", "--host", "localhost", "--port", "8877"],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"host": "localhost", "port": 8877}


def test_start_command_rejects_non_loopback_host(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    result = CliRunner().invoke(
        main_module.cli,
        ["start", "--host", "0.0.0.0"],
    )

    assert result.exit_code != 0
    assert "Could not start dashboard" in result.output
    assert "only bind to localhost" in result.output


def test_start_command_reports_port_conflicts(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "init_db", lambda: None)

    def fail_to_bind(*, host: str, port: int) -> None:
        raise OSError("Address already in use")

    monkeypatch.setattr(dashboard, "serve", fail_to_bind)
    result = CliRunner().invoke(main_module.cli, ["start"])

    assert result.exit_code != 0
    assert "Could not start dashboard" in result.output
    assert "Address already in use" in result.output
