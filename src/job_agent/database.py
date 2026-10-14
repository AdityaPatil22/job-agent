"""SQLite database and CRUD helpers."""

from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .models import ApplicationStatus, Job


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "jobs.db"
VALID_STATUSES = {
    "discovered", "shortlisted", "ready", "applied",
    "interview", "rejected", "withdrawn",
}


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    raw_path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    if raw_path != ":memory:":
        Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(raw_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: str | Path | None = None) -> None:
    """Create the application database tables if needed."""
    with _connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL DEFAULT 'Unknown company',
                title TEXT NOT NULL DEFAULT 'Untitled role',
                url TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                date_found TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'discovered',
                resume_used TEXT NOT NULL DEFAULT '',
                applied_date TEXT,
                next_action TEXT,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT ''
            );
            """
        )


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def add_job(job_data: Job | dict[str, Any], db_path: str | Path | None = None) -> int:
    """Insert a job, deduplicating on its exact URL; return its ID."""
    init_db(db_path)
    job = job_data if isinstance(job_data, Job) else Job.model_validate(job_data)
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO jobs (company, title, url, description, location, date_found)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                company = CASE WHEN excluded.company != 'Unknown company'
                               THEN excluded.company ELSE jobs.company END,
                title = CASE WHEN excluded.title != 'Untitled role'
                             THEN excluded.title ELSE jobs.title END,
                description = CASE WHEN excluded.description != ''
                                    THEN excluded.description ELSE jobs.description END,
                location = CASE WHEN excluded.location != ''
                                 THEN excluded.location ELSE jobs.location END
            """,
            (
                job.company, job.title, job.url, job.description, job.location,
                job.date_found.isoformat(),
            ),
        )
        row = connection.execute("SELECT id FROM jobs WHERE url = ?", (job.url,)).fetchone()
        job_id = int(row["id"])
        timestamp = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT OR IGNORE INTO applications (job_id, status, updated_at)
               VALUES (?, 'discovered', ?)""",
            (job_id, timestamp),
        )
        connection.execute(
            "INSERT INTO activity_log (entity_id, event_type, timestamp, outcome) VALUES (?, ?, ?, ?)",
            (job_id, "job_added", timestamp, job.title),
        )
        return job_id


def get_job(job_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        return _row_dict(connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone())


def get_job_by_url(url: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        return _row_dict(connection.execute(
            "SELECT * FROM jobs WHERE url = ?", (url,)
        ).fetchone())


def list_jobs(
    status: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    init_db(db_path)
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Choose from: {', '.join(sorted(VALID_STATUSES))}")
    query = """
        SELECT jobs.*, applications.status, applications.resume_used,
               applications.applied_date, applications.next_action, applications.notes
        FROM jobs JOIN applications ON applications.job_id = jobs.id
    """
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE applications.status = ?"
        params = (status,)
    query += " ORDER BY jobs.date_found DESC, jobs.id DESC"
    with _connect(db_path) as connection:
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def get_application(job_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        return _row_dict(connection.execute(
            "SELECT * FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone())


def update_application_status(
    job_id: int,
    status: ApplicationStatus | str,
    *,
    resume_used: str | None = None,
    notes: str | None = None,
    applied_date: date | None = None,
    next_action: date | None = None,
    db_path: str | Path | None = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Choose from: {', '.join(sorted(VALID_STATUSES))}")
    init_db(db_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as connection:
        if connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
            raise ValueError(f"Job {job_id} does not exist")

        existing = connection.execute(
            "SELECT * FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        old = dict(existing) if existing is not None else {}
        final_resume = resume_used if resume_used is not None else old.get("resume_used", "")
        final_applied = applied_date.isoformat() if applied_date else old.get("applied_date")
        final_next = next_action.isoformat() if next_action else old.get("next_action")
        final_notes = notes if notes is not None else old.get("notes", "")
        connection.execute(
            """
            INSERT INTO applications
                (job_id, status, resume_used, applied_date, next_action, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                resume_used = excluded.resume_used,
                applied_date = excluded.applied_date,
                next_action = excluded.next_action,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (job_id, status, final_resume, final_applied, final_next, final_notes, timestamp),
        )
        connection.execute(
            "INSERT INTO activity_log (entity_id, event_type, timestamp, outcome) VALUES (?, ?, ?, ?)",
            (job_id, "status_changed", timestamp, status),
        )


def export_csv(
    output_path: str | Path = "data/applications.csv",
    db_path: str | Path | None = None,
) -> Path:
    """Export job and application tracker rows to CSV."""
    rows = list_jobs(db_path=db_path)
    target = Path(output_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id", "company", "title", "url", "location", "date_found", "status",
        "resume_used", "applied_date", "next_action", "notes",
    ]
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return target


def record_activity(
    entity_id: int,
    event_type: str,
    outcome: str = "",
    db_path: str | Path | None = None,
) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO activity_log (entity_id, event_type, timestamp, outcome) VALUES (?, ?, ?, ?)",
            (entity_id, event_type, datetime.now(timezone.utc).isoformat(), outcome),
        )
