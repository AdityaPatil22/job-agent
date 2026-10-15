"""Local-only web dashboard for Job-Agent.

The dashboard serves static HTML/CSS/JS and exposes a small, allow-listed API.
All application actions use the existing CLI in a subprocess. The subprocess
inherits the terminal's stdin so interactive prompts (for login/form review)
can still be answered in the terminal.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .database import DEFAULT_DB_PATH, get_job, init_db, list_jobs
from .urls import normalize_job_url as _normalize_job_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
RESUMES_DIR = PROJECT_ROOT / "resumes"
VALID_STATUSES = {
    "discovered", "shortlisted", "ready", "applied",
    "interview", "rejected", "withdrawn",
}
MAX_REQUEST_BYTES = 64 * 1024
MAX_OUTPUT_CHARS = 80_000
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _safe_csv_value(value: Any) -> Any:
    """Prevent spreadsheet formula execution when exported text is opened in a spreadsheet."""
    if not isinstance(value, str):
        return value
    stripped = value.lstrip(" \t\r\n")
    if value.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail_join(parts: deque[str]) -> str:
    return "".join(parts)


class TaskManager:
    """Run allow-listed CLI actions one at a time and retain their output."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._active_id: str | None = None

    def start(self, label: str, args: list[str]) -> dict[str, str]:
        with self._lock:
            if self._active_id is not None:
                active = self._tasks.get(self._active_id, {})
                raise RuntimeError(
                    f"Another command is already {active.get('status', 'running')}. "
                    "Wait for it to finish before starting another."
                )

            task_id = uuid.uuid4().hex[:12]
            command = [sys.executable, "-m", "job_agent.main", *args]
            display_command = shlex.join(command)
            task: dict[str, Any] = {
                "id": task_id,
                "label": label,
                "command": display_command,
                "status": "running",
                "return_code": None,
                "created_at": _now(),
                "finished_at": None,
                "output_parts": deque(),
                "output_size": 0,
            }
            self._tasks[task_id] = task
            self._active_id = task_id
            self._prune_locked()

        thread = threading.Thread(
            target=self._run, args=(task_id, command), daemon=True,
            name=f"job-agent-task-{task_id}",
        )
        thread.start()
        return {"task_id": task_id, "status": "running", "label": label}

    def _append_output(self, task_id: str, chunk: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            parts: deque[str] = task["output_parts"]
            parts.append(chunk)
            task["output_size"] += len(chunk)
            if task["output_size"] > MAX_OUTPUT_CHARS + 1024:
                combined = _tail_join(parts)[-MAX_OUTPUT_CHARS:]
                task["output_parts"] = deque(["[Earlier output trimmed]\n", combined])
                task["output_size"] = len(task["output_parts"][0]) + len(combined)

    def _run(self, task_id: str, command: list[str]) -> None:
        task = self._tasks[task_id]
        prefix = f"\n[Job-Agent dashboard task {task_id}]"
        print(f"{prefix} {task['label']}", flush=True)
        print(f"$ {shlex.join(command)}", flush=True)

        env = os.environ.copy()
        source_path = str(PROJECT_ROOT / "src")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = source_path + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
        try:
            # stdin is deliberately inherited: commands may ask the user to
            # finish login or press Enter after reviewing a form in Chromium.
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
            )
            assert process.stdout is not None
            # Read character-by-character so prompts without a newline become
            # visible in the dashboard while the child waits for terminal input.
            while True:
                char = process.stdout.read(1)
                if char == "":
                    break
                self._append_output(task_id, char)
                sys.stdout.write(char)
                sys.stdout.flush()
            return_code = process.wait()
            final_status = "succeeded" if return_code == 0 else "failed"
            self._append_output(task_id, f"\n[Command exited with code {return_code}]\n")
        except Exception as exc:
            return_code = -1
            final_status = "failed"
            message = f"\n[Could not run command: {exc}]\n"
            self._append_output(task_id, message)
            print(message, end="", flush=True)
        finally:
            with self._lock:
                current = self._tasks.get(task_id)
                if current is not None:
                    current["return_code"] = return_code
                    current["status"] = final_status
                    current["finished_at"] = _now()
                if self._active_id == task_id:
                    self._active_id = None

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return self._serialize_task(task) if task else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ordered = sorted(self._tasks.values(), key=lambda item: item["created_at"], reverse=True)
            return [self._serialize_task(task, include_output=False) for task in ordered[:20]]

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._tasks:
                return None
            task = max(self._tasks.values(), key=lambda item: item["created_at"])
            return self._serialize_task(task)

    def _serialize_task(self, task: dict[str, Any], include_output: bool = True) -> dict[str, Any]:
        result = {key: value for key, value in task.items() if key not in {"output_parts", "output_size"}}
        if include_output:
            result["output"] = _tail_join(task["output_parts"])
        return result

    def _prune_locked(self) -> None:
        if len(self._tasks) <= 25:
            return
        removable = sorted(
            (task for task in self._tasks.values() if task["id"] != self._active_id and task["status"] != "running"),
            key=lambda item: item["created_at"],
        )
        while len(self._tasks) > 20 and removable:
            self._tasks.pop(removable.pop(0)["id"], None)


TASKS = TaskManager()


def _get_resumes() -> list[str]:
    if not RESUMES_DIR.is_dir():
        return []
    allowed_suffixes = {".pdf", ".doc", ".docx"}
    return sorted(
        path.name for path in RESUMES_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in allowed_suffixes
    )


def _delete_job(job_id: int) -> bool:
    """Delete one job, its application, and its activity history atomically."""
    with sqlite3.connect(DEFAULT_DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if exists is None:
            return False
        connection.execute("DELETE FROM activity_log WHERE entity_id = ?", (job_id,))
        connection.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
        connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return True



def _update_job(job_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Update job fields and application metadata without changing the storage model."""
    job_fields = {key: payload[key] for key in ("company", "title", "url", "location", "description") if key in payload}
    app_fields = {key: payload[key] for key in ("status", "resume_used", "applied_date", "next_action", "notes") if key in payload}
    if not job_fields and not app_fields:
        raise ValueError("Provide at least one field to update.")
    for key, value in job_fields.items():
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text.")
        job_fields[key] = value.strip()
    if ("company" in job_fields and not job_fields["company"]) or ("title" in job_fields and not job_fields["title"]):
        raise ValueError("Company and role cannot be empty.")
    if "url" in job_fields:
        job_fields["url"] = _normalize_job_url(job_fields["url"])
        for candidate in list_jobs():
            if int(candidate["id"]) != job_id:
                try:
                    if _normalize_job_url(str(candidate["url"])) == job_fields["url"]:
                        raise sqlite3.IntegrityError("That job URL is already tracked.")
                except ValueError:
                    continue
    if "status" in app_fields:
        status = app_fields["status"]
        if not isinstance(status, str) or status.lower() not in VALID_STATUSES:
            raise ValueError(f"Invalid status. Choose from: {', '.join(sorted(VALID_STATUSES))}.")
        app_fields["status"] = status.lower()
    for key in ("resume_used", "notes"):
        if key in app_fields and not isinstance(app_fields[key], str):
            raise ValueError(f"{key} must be text.")
    for key in ("applied_date", "next_action"):
        if key in app_fields:
            value = app_fields[key]
            if value in ("", None):
                app_fields[key] = None
            elif not isinstance(value, str):
                raise ValueError(f"{key} must use YYYY-MM-DD format.")
            else:
                try:
                    parsed_date = datetime.strptime(value, "%Y-%m-%d")
                    if parsed_date.strftime("%Y-%m-%d") != value:
                        raise ValueError
                except ValueError as exc:
                    raise ValueError(f"{key} must use YYYY-MM-DD format.") from exc

    init_db()
    with sqlite3.connect(DEFAULT_DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if current is None:
            return None
        if job_fields:
            assignments = ", ".join(f"{key} = ?" for key in job_fields)
            connection.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", [*job_fields.values(), job_id])
        row = connection.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        existing = dict(row) if row else {}
        status = app_fields.get("status", existing.get("status", "discovered"))
        resume = app_fields.get("resume_used", existing.get("resume_used", ""))
        applied = app_fields["applied_date"] if "applied_date" in app_fields else existing.get("applied_date")
        followup = app_fields["next_action"] if "next_action" in app_fields else existing.get("next_action")
        notes = app_fields.get("notes", existing.get("notes", ""))
        timestamp = _now()
        connection.execute(
            """INSERT INTO applications (job_id,status,resume_used,applied_date,next_action,notes,updated_at)
               VALUES (?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
               status=excluded.status,resume_used=excluded.resume_used,applied_date=excluded.applied_date,
               next_action=excluded.next_action,notes=excluded.notes,updated_at=excluded.updated_at""",
            (job_id, status, resume, applied, followup, notes, timestamp),
        )
        if "status" in app_fields and status != existing.get("status"):
            connection.execute(
                "INSERT INTO activity_log (entity_id,event_type,timestamp,outcome) VALUES (?,?,?,?)",
                (job_id, "status_changed", timestamp, status),
            )
    return next((item for item in list_jobs() if int(item["id"]) == job_id), None)


CSV_FIELDS = ["id", "company", "title", "url", "location", "date_found", "status", "resume_used", "applied_date", "next_action", "notes"]
CSV_ALIASES = {"role": "title", "applied_on": "applied_date", "follow_up": "next_action", "follow_up_date": "next_action"}


def _export_csv_text(db_path: str | Path | None = None) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in list_jobs(db_path=db_path):
        # Apply to every exported cell so user-entered notes cannot run as a spreadsheet formula.
        writer.writerow({key: _safe_csv_value(value) for key, value in row.items()})
    return output.getvalue()


def _import_csv_text(csv_text: str, db_path: str | Path | None = None) -> dict[str, Any]:
    """Validate the whole CSV, snapshot an existing database, then merge without resetting blanks."""
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ValueError("Choose a non-empty CSV file.")
    if len(csv_text.encode("utf-8")) > 5 * 1024 * 1024:
        raise ValueError("CSV file is too large (maximum 5 MB).")
    # Spreadsheet tools commonly export a UTF-8 BOM. Remove it before parsing the first header.
    csv_text = csv_text.lstrip("\ufeff")
    seen_urls: set[str] = set()
    try:
        reader = csv.DictReader(io.StringIO(csv_text, newline=""), strict=True)
        if not reader.fieldnames:
            raise ValueError("CSV must contain a header row.")
        normalized_headers = []
        for name in reader.fieldnames:
            if name is None or not name.strip():
                raise ValueError("CSV contains an empty column name.")
            normalized = CSV_ALIASES.get(name.strip().lower(), name.strip().lower())
            normalized_headers.append(normalized)
        if len(normalized_headers) != len(set(normalized_headers)):
            raise ValueError("CSV contains duplicate columns after normalizing aliases.")
        headers = set(normalized_headers)
        if not {"company", "url"}.issubset(headers) or "title" not in headers:
            raise ValueError("CSV needs company, title (or role), and url columns. Use Export CSV for a template.")

        rows: list[dict[str, Any]] = []
        header_map = dict(zip(reader.fieldnames, normalized_headers))
        for line_no, raw in enumerate(reader, start=2):
            if None in raw and any(str(value or "").strip() for value in raw[None]):
                raise ValueError(f"Row {line_no}: contains more values than the header.")
            if not any(str(value or "").strip() for key, value in raw.items() if key is not None):
                continue
            row = {
                header_map[key]: (value or "").strip()
                for key, value in raw.items()
                if key is not None
            }
            company, title, raw_url = row.get("company", ""), row.get("title", ""), row.get("url", "")
            if not company or not title:
                raise ValueError(f"Row {line_no}: company and role are required.")
            try:
                url = _normalize_job_url(raw_url)
            except ValueError as exc:
                raise ValueError(f"Row {line_no}: {exc}") from exc
            if url in seen_urls:
                raise ValueError(f"Row {line_no}: this job URL appears more than once in the CSV.")
            seen_urls.add(url)

            status = (row.get("status") or "").lower().replace(" ", "_")
            status_aliases = {"pending": "discovered", "interviewed": "interview", "ready_to_review": "ready"}
            status = status_aliases.get(status, status) if status else None
            if status and status not in VALID_STATUSES:
                raise ValueError(f"Row {line_no}: unsupported status '{status}'.")

            parsed_dates: dict[str, str | None] = {}
            for date_field in ("date_found", "applied_date", "next_action"):
                value = row.get(date_field, "")
                if not value:
                    parsed_dates[date_field] = None
                    continue
                try:
                    parsed = datetime.strptime(value, "%Y-%m-%d")
                    if parsed.strftime("%Y-%m-%d") != value:
                        raise ValueError
                except ValueError as exc:
                    raise ValueError(f"Row {line_no}: {date_field} must use YYYY-MM-DD.") from exc
                parsed_dates[date_field] = value

            rows.append({
                "company": company,
                "title": title,
                "url": url,
                "location": row.get("location") or None,
                "description": row.get("description") or None,
                "date_found": parsed_dates["date_found"],
                "status": status,
                "resume_used": row.get("resume_used") or None,
                "applied_date": parsed_dates["applied_date"],
                "next_action": parsed_dates["next_action"],
                "notes": row.get("notes") or None,
            })
    except csv.Error as exc:
        raise ValueError(f"Could not parse CSV: {exc}") from exc
    if not rows:
        raise ValueError("CSV contains no application rows.")

    database_path = Path(db_path or DEFAULT_DB_PATH)
    database_existed = database_path.exists()
    init_db(database_path)

    # SQLite's backup API includes committed WAL data. Microseconds prevent same-second
    # imports from reusing a backup filename and replacing the previous recovery point.
    backup_path = None
    if database_existed:
        backup_dir = database_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_dir / f"jobs-before-csv-import-{backup_stamp}.db"
        with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as backup:
            source.backup(backup)

    existing_by_url: dict[str, dict[str, Any]] = {}
    for existing_job in list_jobs(db_path=database_path):
        try:
            canonical = _normalize_job_url(str(existing_job["url"]))
        except ValueError:
            continue
        existing_by_url.setdefault(canonical, existing_job)

    inserted = updated = 0
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            existing_job = existing_by_url.get(row["url"])
            if existing_job:
                job_id = int(existing_job["id"])
                connection.execute(
                    """UPDATE jobs SET company=?, title=?, location=?, description=?, date_found=?
                       WHERE id=?""",
                    (
                        row["company"],
                        row["title"],
                        row["location"] if row["location"] is not None else existing_job.get("location", ""),
                        row["description"] if row["description"] is not None else existing_job.get("description", ""),
                        row["date_found"] or existing_job.get("date_found") or datetime.now().date().isoformat(),
                        job_id,
                    ),
                )
                old_app_row = connection.execute(
                    "SELECT * FROM applications WHERE job_id=?", (job_id,)
                ).fetchone()
                old_app = dict(old_app_row) if old_app_row else {}
                updated += 1
            else:
                cursor = connection.execute(
                    """INSERT INTO jobs (company,title,url,location,description,date_found)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        row["company"], row["title"], row["url"], row["location"] or "",
                        row["description"] or "", row["date_found"] or datetime.now().date().isoformat(),
                    ),
                )
                job_id = int(cursor.lastrowid)
                old_app = {}
                inserted += 1

            merged_status = row["status"] or old_app.get("status", "discovered")
            merged_resume = row["resume_used"] if row["resume_used"] is not None else old_app.get("resume_used", "")
            merged_applied_date = row["applied_date"] if row["applied_date"] is not None else old_app.get("applied_date")
            merged_next_action = row["next_action"] if row["next_action"] is not None else old_app.get("next_action")
            merged_notes = row["notes"] if row["notes"] is not None else old_app.get("notes", "")
            connection.execute(
                """INSERT INTO applications (job_id,status,resume_used,applied_date,next_action,notes,updated_at)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
                   status=excluded.status, resume_used=excluded.resume_used, applied_date=excluded.applied_date,
                   next_action=excluded.next_action, notes=excluded.notes, updated_at=excluded.updated_at""",
                (job_id, merged_status, merged_resume, merged_applied_date, merged_next_action, merged_notes, _now()),
            )
            existing_by_url[row["url"]] = {
                "id": job_id, "company": row["company"], "title": row["title"], "url": row["url"],
                "location": row["location"] or "", "description": row["description"] or "",
                "date_found": row["date_found"] or datetime.now().date().isoformat(),
            }
        connection.execute(
            "INSERT INTO activity_log (entity_id,event_type,timestamp,outcome) VALUES (0,?,?,?)",
            ("csv_import", _now(), f"inserted={inserted}, updated={updated}"),
        )
    return {"inserted": inserted, "updated": updated, "total": len(rows), "backup": str(backup_path) if backup_path else None}

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "JobAgentDashboard/0.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[dashboard] {self.address_string()} - {format_string % args}", flush=True)

    def _local_request_allowed(self) -> bool:
        """Reject non-loopback hosts and cross-origin browser actions."""
        host_header = self.headers.get("Host", "")
        parsed_host = urlsplit("//" + host_header)
        if parsed_host.hostname not in LOCAL_HOSTS:
            return False
        if parsed_host.port not in (None, self.server.server_port):
            return False

        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urlsplit(origin)
            try:
                origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
            except ValueError:
                return False
            if parsed_origin.hostname not in LOCAL_HOSTS or origin_port != self.server.server_port:
                return False
            if parsed_origin.scheme not in {"http", "https"}:
                return False
        return True

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _read_json(self, max_bytes: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0 or length > max_bytes:
            raise ValueError(f"Request body must be between 1 byte and {max_bytes // (1024 * 1024) or 1} MB")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def do_GET(self) -> None:
        if not self._local_request_allowed():
            self._json({"error": "Local requests only."}, 403)
            return
        path = urlsplit(self.path).path
        if path == "/api/jobs":
            self._json({"jobs": list_jobs()})
            return
        if path == "/api/export.csv":
            body = _export_csv_text().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="job-applications.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/resumes":
            self._json({"resumes": _get_resumes()})
            return
        if path == "/api/tasks":
            self._json({"tasks": TASKS.list(), "latest": TASKS.latest()})
            return
        if path.startswith("/api/tasks/"):
            task_id = path.removeprefix("/api/tasks/")
            if not task_id or "/" in task_id:
                self._json({"error": "Task not found."}, 404)
                return
            task = TASKS.get(task_id)
            self._json(task if task else {"error": "Task not found."}, 200 if task else 404)
            return

        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        static_entry = static_files.get(path)
        if static_entry:
            filename, content_type = static_entry
            file_path = STATIC_DIR / filename
            try:
                self._send_bytes(file_path.read_bytes(), content_type)
            except OSError:
                self._json({"error": f"Dashboard asset missing: {filename}"}, 500)
            return
        self._json({"error": "Not found."}, 404)

    def do_DELETE(self) -> None:
        if not self._local_request_allowed():
            self._json({"error": "Local requests only."}, 403)
            return
        path = urlsplit(self.path).path
        prefix = "/api/jobs/"
        if not path.startswith(prefix):
            self._json({"error": "Not found."}, 404)
            return
        raw_job_id = path.removeprefix(prefix)
        if not raw_job_id.isdigit() or int(raw_job_id) < 1:
            self._json({"error": "A valid job ID is required."}, 400)
            return
        try:
            job_id = int(raw_job_id)
            if not _delete_job(job_id):
                self._json({"error": f"Job #{job_id} was not found."}, 404)
                return
            self._json({"deleted": True, "job_id": job_id})
        except Exception as exc:
            self._json({"error": f"Could not delete job #{job_id}: {exc}"}, 500)


    def do_PATCH(self) -> None:
        if not self._local_request_allowed():
            self._json({"error": "Local requests only."}, 403)
            return
        path = urlsplit(self.path).path
        prefix = "/api/jobs/"
        raw_job_id = path.removeprefix(prefix) if path.startswith(prefix) else ""
        if not raw_job_id.isdigit() or int(raw_job_id) < 1:
            self._json({"error": "A valid job ID is required."}, 400)
            return
        try:
            payload = self._read_json()
            updated = _update_job(int(raw_job_id), payload)
            if updated is None:
                self._json({"error": f"Job #{raw_job_id} was not found."}, 404)
                return
            self._json({"job": updated})
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except sqlite3.IntegrityError:
            self._json({"error": "That job URL is already tracked."}, 409)
        except Exception as exc:
            self._json({"error": f"Could not update application: {exc}"}, 500)

    def do_POST(self) -> None:
        if not self._local_request_allowed():
            self._json({"error": "Local requests only."}, 403)
            return
        path = urlsplit(self.path).path
        try:
            payload = self._read_json(max_bytes=6 * 1024 * 1024 if path == "/api/import.csv" else MAX_REQUEST_BYTES)
            if path == "/api/import.csv":
                result = _import_csv_text(payload.get("csv_text"))
                self._json({"import": result, "jobs": list_jobs()})
                return
            if path == "/api/actions/add-manual-job":
                company = payload.get("company")
                title = payload.get("title")
                url = payload.get("url")
                if not isinstance(company, str) or not company.strip():
                    raise ValueError("Company name is required.")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("Role is required.")
                if not isinstance(url, str) or not url.strip():
                    raise ValueError("Job URL is required.")
                parsed = urlsplit(url.strip())
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("Use a complete http:// or https:// job URL.")
                from datetime import date
                from .database import add_job
                clean_url = _normalize_job_url(url.strip())
                existing_job = next(
                    (item for item in list_jobs() if _normalize_job_url(str(item["url"])) == clean_url),
                    None,
                )
                if existing_job:
                    job_id = int(existing_job["id"])
                    # Preserve the current URL and update details through the same validated CRUD helper.
                    changes = {
                        "company": company.strip(), "title": title.strip(),
                        "location": str(payload.get("location") or "").strip(),
                    }
                    changes.update({key: payload[key] for key in ("status", "resume_used", "applied_date", "next_action", "notes") if key in payload})
                    updated = _update_job(job_id, changes)
                else:
                    job_id = add_job({
                        "company": company.strip(), "title": title.strip(), "url": clean_url,
                        "location": str(payload.get("location") or "").strip(),
                        "description": "", "date_found": date.today().isoformat(),
                    })
                    fields = {key: payload[key] for key in ("status", "resume_used", "applied_date", "next_action", "notes") if key in payload}
                    updated = _update_job(job_id, fields) if fields else next((item for item in list_jobs() if int(item["id"]) == job_id), None)
                self._json({"job": updated}, 201)
                return

            if path == "/api/actions/add-job":
                url = payload.get("url")
                if not isinstance(url, str) or not url.strip():
                    raise ValueError("Enter a job URL.")
                parsed = urlsplit(url.strip())
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("Use a complete http:// or https:// job URL.")
                result = TASKS.start("Add job", ["add-job", url.strip()])
                self._json(result, 202)
                return

            if path == "/api/actions/apply":
                job_id = self._positive_int(payload.get("job_id"), "job_id")
                job = get_job(job_id)
                if job is None:
                    raise ValueError(f"Job #{job_id} was not found.")
                resume = payload.get("resume")
                if not isinstance(resume, str) or Path(resume).name != resume or Path(resume).suffix.lower() not in {".pdf", ".doc", ".docx"}:
                    raise ValueError("Select a resume file from the resumes/ directory.")
                if resume not in _get_resumes():
                    raise ValueError(f"Resume file {resume!r} was not found in resumes/.")
                result = TASKS.start("Assist with application", ["apply", "--job-id", str(job_id), "--resume", resume])
                self._json(result, 202)
                return

            if path == "/api/actions/mark-applied":
                job_id = self._positive_int(payload.get("job_id"), "job_id")
                if get_job(job_id) is None:
                    raise ValueError(f"Job #{job_id} was not found.")
                args = ["mark-applied", "--job-id", str(job_id)]
                notes = payload.get("notes", "")
                if notes is not None and not isinstance(notes, str):
                    raise ValueError("Notes must be text.")
                if notes and notes.strip():
                    args.extend(["--notes", notes.strip()[:2000]])
                result = TASKS.start("Mark application as applied", args)
                self._json(result, 202)
                return

            if path == "/api/actions/export":
                result = TASKS.start("Export applications to CSV", ["export", "--format", "csv", "--output", "data/applications.csv"])
                self._json(result, 202)
                return

            self._json({"error": "Unknown action."}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except RuntimeError as exc:
            self._json({"error": str(exc)}, 409)
        except Exception as exc:
            self._json({"error": f"Request failed: {exc}"}, 500)

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a positive integer.") from exc
        if parsed < 1:
            raise ValueError(f"{field} must be a positive integer.")
        return parsed


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the dashboard on loopback only."""
    if host not in LOCAL_HOSTS:
        raise ValueError("For safety, the dashboard can only bind to localhost, 127.0.0.1, or ::1.")
    init_db()
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print("\nJob-Agent dashboard is running.", flush=True)
    print(f"Open http://127.0.0.1:{server.server_port} in your browser.", flush=True)
    print("Keep this terminal open. CLI output and any interactive prompts will appear here.", flush=True)
    print("Press Ctrl+C to stop the dashboard.\n", flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nStopping Job-Agent dashboard...", flush=True)
    finally:
        server.server_close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the local Job-Agent web dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback host only (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default: 8765)")
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
