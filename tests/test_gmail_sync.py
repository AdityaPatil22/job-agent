"""Local-only Gmail status classification, matching, and review tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from job_agent import gmail_sync, main as main_module
from job_agent.database import add_job, get_application, list_jobs


def seed_job(database_path: Path, company: str = "Acme", title: str = "Software Engineer") -> int:
    return add_job(
        {
            "company": company,
            "title": title,
            "url": f"https://careers.{company.casefold().replace(' ', '')}.example/jobs/{title.casefold().replace(' ', '-')}",
            "description": "",
            "location": "Pune",
            "date_found": "2026-10-01",
        },
        db_path=database_path,
    )


def test_classifies_explicit_application_status_messages() -> None:
    examples = [
        ("Application received", "Thank you for applying. We received your application.", "applied"),
        ("Interview request: Software Engineer", "We would like to schedule an interview.", "interview"),
        ("Update on your application", "We will not be moving forward with your application.", "rejected"),
        ("Offer letter", "We are pleased to offer you the position.", "offer"),
    ]

    for subject, body, expected in examples:
        status, reason = gmail_sync.classify_email(subject, body)
        assert status == expected
        assert expected in reason


def test_generic_recruiter_email_does_not_suggest_a_status() -> None:
    status, reason = gmail_sync.classify_email(
        "Nice to connect",
        "I saw your profile and wanted to discuss a potential opportunity.",
    )

    assert status is None
    assert "No explicit" in reason


def test_matches_when_company_and_job_title_are_both_present(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    seed_job(database_path)
    jobs = list_jobs(db_path=database_path)

    match = gmail_sync.match_email_to_job(
        "Interview request for Software Engineer at Acme",
        "Hello, the Acme team would like to schedule an interview for the Software Engineer role.",
        "Recruiting <recruiting@acme.example>",
        jobs,
    )

    assert match["job_id"] == jobs[0]["id"]
    assert match["confidence"] >= 0.90


def test_ambiguous_company_match_requires_review(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    seed_job(database_path, "Acme", "Software Engineer")
    seed_job(database_path, "Acme", "Platform Engineer")

    match = gmail_sync.match_email_to_job(
        "Update on your Acme application",
        "Thank you for applying to Acme. Your application has been received.",
        "Acme Recruiting <recruiting@acme.example>",
        list_jobs(db_path=database_path),
    )

    assert match["job_id"] is not None
    assert match["confidence"] < 0.90
    assert "Multiple" in match["reason"] or "Only part" in match["reason"]


def test_review_approval_updates_selected_job_and_records_activity(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    job_id = seed_job(database_path)
    gmail_sync._init_email_tables(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO email_events
               (message_id,thread_id,subject,sender,received_at,summary,detected_status,
                job_id,confidence,disposition,reason,processed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "gmail-message-1", "gmail-thread-1", "Interview request", "recruiter@acme.example",
                "2026-10-15T09:00:00+00:00", "Schedule an interview", "interview",
                job_id, 0.65, "needs_review", "Candidate match needs confirmation",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    result = gmail_sync.review_email_event("gmail-message-1", "approve", job_id, database_path)

    assert result["action"] == "approved"
    assert get_application(job_id, db_path=database_path)["status"] == "interview"
    with sqlite3.connect(database_path) as connection:
        event = connection.execute(
            "SELECT disposition, job_id FROM email_events WHERE message_id=?",
            ("gmail-message-1",),
        ).fetchone()
        activity_count = connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE entity_id=? AND event_type='gmail_status_update_approved'",
            (job_id,),
        ).fetchone()[0]
    assert event == ("approved", job_id)
    assert activity_count == 1


def test_review_dismiss_does_not_change_application(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    job_id = seed_job(database_path)
    gmail_sync._init_email_tables(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO email_events
               (message_id,subject,detected_status,job_id,confidence,disposition,reason,processed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("gmail-message-2", "Application update", "rejected", job_id, 0.4, "needs_review",
             "Ambiguous", datetime.now(timezone.utc).isoformat()),
        )

    result = gmail_sync.review_email_event("gmail-message-2", "dismiss", db_path=database_path)

    assert result["action"] == "dismissed"
    assert get_application(job_id, db_path=database_path)["status"] == "discovered"


def test_sync_lease_prevents_parallel_processes(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    gmail_sync._init_email_tables(database_path)
    owner = gmail_sync._acquire_sync_lease(database_path)

    try:
        try:
            gmail_sync._acquire_sync_lease(database_path)
            assert False, "A second sync must not acquire the active lease"
        except gmail_sync.GmailIntegrationError as exc:
            assert "already running" in str(exc)
    finally:
        gmail_sync._release_sync_lease(owner, database_path)

    next_owner = gmail_sync._acquire_sync_lease(database_path)
    gmail_sync._release_sync_lease(next_owner, database_path)


def test_disconnect_removes_local_token_and_disables_auto_sync(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "jobs.db"
    token_path = tmp_path / "gmail" / "token.json"
    token_path.parent.mkdir()
    token_path.write_text('{"token": "local-test-token"}', encoding="utf-8")
    monkeypatch.setattr(gmail_sync, "GMAIL_TOKEN_PATH", token_path)
    gmail_sync.set_auto_sync(True, db_path=database_path)

    gmail_sync.disconnect_gmail(db_path=database_path)

    assert not token_path.exists()
    assert gmail_sync.get_status(db_path=database_path)["auto_sync"] is False


def test_email_sync_command_reports_summary(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(
        gmail_sync,
        "sync_gmail",
        lambda max_results: {
            "processed": 4, "auto_updated": 2, "needs_review": 1, "ignored": 1,
        },
    )

    result = CliRunner().invoke(main_module.cli, ["email-sync", "--limit", "30"])

    assert result.exit_code == 0, result.output
    assert "4 new messages processed" in result.output
    assert "2 statuses updated" in result.output
    assert "1 need review" in result.output


def test_email_status_command_reports_optional_dependency_state(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(
        gmail_sync,
        "get_status",
        lambda: {
            "available": False, "connected": False, "account": "",
            "auto_sync": False, "last_sync_at": "", "pending_review": 0,
        },
    )

    result = CliRunner().invoke(main_module.cli, ["email-status"])

    assert result.exit_code == 0, result.output
    assert "Gmail dependencies installed: no" in result.output
    assert "Connected: no" in result.output
