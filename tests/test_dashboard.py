"""Regression tests for the dashboard's local data-import/export safety."""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import pytest

from job_agent import dashboard


def make_database(path: Path) -> None:
    dashboard.init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO jobs (company, title, url, description, location, date_found)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "Old Company", "Platform Engineer",
                "https://jobs.example.com/roles/42/?utm_source=old#apply",
                "Original description", "Pune", "2026-10-01",
            ),
        )
        job_id = connection.execute("SELECT id FROM jobs").fetchone()[0]
        connection.execute(
            """INSERT INTO applications
               (job_id, status, resume_used, applied_date, next_action, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id, "applied", "platform.pdf", "2026-10-02",
                "2026-10-20", "Follow up with recruiter", "2026-10-02T12:00:00+00:00",
            ),
        )


def test_normalizes_job_url_without_dropping_job_identifiers() -> None:
    actual = dashboard._normalize_job_url(
        "HTTPS://Example.COM:443/roles/42/?utm_source=jobs&job_id=42&fbclid=abc#apply"
    )

    assert actual == "https://example.com/roles/42?job_id=42"


def test_rejects_job_urls_with_credentials_or_invalid_scheme() -> None:
    with pytest.raises(ValueError, match="complete http"):
        dashboard._normalize_job_url("javascript:alert(1)")
    with pytest.raises(ValueError, match="embedded credentials"):
        dashboard._normalize_job_url("https://user:password@example.com/jobs/1")


def test_csv_import_preserves_existing_application_data_when_columns_are_blank(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "data" / "jobs.db"
    database_path.parent.mkdir(parents=True)
    make_database(database_path)

    result = dashboard._import_csv_text(
        "company,title,url,location,status,notes\n"
        "New Company,Senior Platform Engineer,"
        "https://jobs.example.com/roles/42?utm_campaign=autumn,,,\n",
        db_path=database_path,
    )

    jobs = dashboard.list_jobs(db_path=database_path)
    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert len(jobs) == 1
    assert jobs[0]["company"] == "New Company"
    assert jobs[0]["title"] == "Senior Platform Engineer"
    assert jobs[0]["status"] == "applied"
    assert jobs[0]["resume_used"] == "platform.pdf"
    assert jobs[0]["applied_date"] == "2026-10-02"
    assert jobs[0]["next_action"] == "2026-10-20"
    assert jobs[0]["notes"] == "Follow up with recruiter"
    assert jobs[0]["location"] == "Pune"
    assert result["backup"] is not None
    assert Path(result["backup"]).exists()

    with sqlite3.connect(result["backup"]) as backup:
        snapshot = backup.execute("SELECT company FROM jobs").fetchone()
    assert snapshot == ("Old Company",)


def test_import_creates_distinct_recovery_points(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    make_database(database_path)
    first = dashboard._import_csv_text(
        "company,title,url\nA,Engineer,https://example.com/jobs/1\n",
        db_path=database_path,
    )
    second = dashboard._import_csv_text(
        "company,title,url\nB,Engineer,https://example.com/jobs/2\n",
        db_path=database_path,
    )

    assert first["backup"] != second["backup"]
    assert Path(first["backup"]).is_file()
    assert Path(second["backup"]).is_file()


def test_duplicate_canonical_urls_in_one_csv_are_rejected_before_writes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "jobs.db"
    dashboard.init_db(database_path)
    source = (
        "company,title,url\n"
        "A,Engineer,https://example.com/jobs/1?utm_source=one\n"
        "B,Engineer,https://EXAMPLE.com/jobs/1/#apply\n"
    )

    with pytest.raises(ValueError, match="more than once"):
        dashboard._import_csv_text(source, db_path=database_path)

    assert dashboard.list_jobs(db_path=database_path) == []


def test_invalid_status_or_date_is_rejected_before_database_changes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "jobs.db"
    make_database(database_path)

    with pytest.raises(ValueError, match="unsupported status"):
        dashboard._import_csv_text(
            "company,title,url,status\n"
            "Changed,Engineer,https://jobs.example.com/roles/42,unknown\n",
            db_path=database_path,
        )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        dashboard._import_csv_text(
            "company,title,url,date_found\n"
            "Changed,Engineer,https://jobs.example.com/roles/42,2026-1-2\n",
            db_path=database_path,
        )

    assert dashboard.list_jobs(db_path=database_path)[0]["company"] == "Old Company"


def test_csv_export_neutralizes_spreadsheet_formulas(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    dashboard.init_db(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO jobs (company, title, url, description, location, date_found)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("=HYPERLINK(\"https://bad.example\", \"open\")", "Engineer",
             "https://example.com/jobs/1", "", "", "2026-10-01"),
        )
        job_id = connection.execute("SELECT id FROM jobs").fetchone()[0]
        connection.execute(
            """INSERT INTO applications
               (job_id, status, resume_used, applied_date, next_action, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_id, "discovered", "", None, None, "  =1+1", "2026-10-01T00:00:00+00:00"),
        )

    reader = csv.DictReader(io.StringIO(dashboard._export_csv_text(database_path)))
    row = next(reader)
    assert row["company"].startswith("'=")
    assert row["notes"].startswith("'  =")


def test_csv_import_accepts_utf8_bom_and_role_alias(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.db"
    result = dashboard._import_csv_text(
        "\ufeffcompany,role,url\nAcme,AI Engineer,https://example.com/jobs/9\n",
        db_path=database_path,
    )

    assert result["inserted"] == 1
    assert dashboard.list_jobs(db_path=database_path)[0]["title"] == "AI Engineer"
