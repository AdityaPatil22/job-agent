"""Read-only Gmail sync for Job-Agent's local application tracker.

OAuth tokens and minimal message metadata stay on disk locally. Full email bodies
are processed in memory and are never stored. Gmail is never modified.
"""

from __future__ import annotations

import base64
import email.header
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any

from .database import DEFAULT_DB_PATH, list_jobs, record_activity, update_application_status
from .urls import normalize_job_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GMAIL_CONFIG_PATH = PROJECT_ROOT / "config" / "gmail-credentials.json"
GMAIL_TOKEN_PATH = PROJECT_ROOT / "data" / "gmail" / "token.json"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_QUERY = (
    'newer_than:90d -in:sent -in:drafts {application applied interview offer recruiter '
    '"thank you for applying" "next steps" rejection}'
)
MAX_MESSAGES_PER_SYNC = 200
MAX_DECODED_BODY_CHARS = 200_000
AUTO_SYNC_INTERVAL_SECONDS = 15 * 60
SYNC_LOCK = threading.Lock()

STATUS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rejected", (
        r"\bwe regret to inform\b", r"\bnot moving forward\b",
        r"\bwon't be moving forward\b", r"\bwill not be moving forward\b",
        r"\bdecided not to move forward\b", r"\bother candidates\b",
        r"\bnot be proceeding\b", r"\bnot selected\b",
        r"\bunsuccessful in your application\b",
    )),
    ("offer", (
        r"\bpleased to offer\b", r"\b(?:excited|delighted|happy) to offer\b",
        r"\boffer you (?:the|a)\b", r"\bjob offer\b", r"\boffer letter\b",
        r"\bemployment offer\b", r"\bformal offer\b",
    )),
    ("interview", (
        r"\binterview\b", r"\bphone screen\b", r"\btechnical screen\b",
        r"\bschedule (?:a |an )?(?:call|screen|meeting)\b",
        r"\bavailability for (?:a |an )?(?:call|interview)\b",
        r"\bmeet the team\b",
    )),
    ("applied", (
        r"\bapplication received\b", r"\bthank you for applying\b",
        r"\breceived your application\b", r"\bapplication has been submitted\b",
        r"\bsuccessfully submitted your application\b",
        r"\bwe have received your application\b", r"\bwe've received your application\b",
    )),
)

COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "company", "co", "technologies", "technology", "india", "pvt", "private",
    "plc", "group",
}
GENERIC_MAIL_HOSTS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com",
    "yahoo.co.in", "icloud.com", "live.com", "greenhouse.io", "lever.co",
    "myworkday.com", "workday.com", "ashbyhq.com", "smartrecruiters.com",
}


class GmailIntegrationError(RuntimeError):
    """Expected Gmail setup, authorization, or API failure."""


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _init_email_tables(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS email_events (
                message_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                detected_status TEXT,
                job_id INTEGER,
                confidence REAL NOT NULL DEFAULT 0,
                disposition TEXT NOT NULL DEFAULT 'needs_review',
                reason TEXT NOT NULL DEFAULT '',
                processed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_email_events_disposition
                ON email_events(disposition, received_at DESC);
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gmail_sync_lock (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                owner TEXT NOT NULL,
                acquired_at TEXT NOT NULL
            );
            """
        )


def _acquire_sync_lease(db_path: str | Path | None = None) -> str:
    """Prevent the dashboard scheduler and a CLI sync process from racing each other."""
    owner = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT owner, acquired_at FROM gmail_sync_lock WHERE singleton=1"
        ).fetchone()
        if current:
            try:
                acquired_at = datetime.fromisoformat(str(current["acquired_at"]))
                stale = now - acquired_at > timedelta(minutes=30)
            except (TypeError, ValueError):
                stale = True
            if not stale:
                raise GmailIntegrationError("A Gmail sync is already running in this Job-Agent database.")
        connection.execute(
            """INSERT INTO gmail_sync_lock(singleton,owner,acquired_at) VALUES(1,?,?)
               ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner,acquired_at=excluded.acquired_at""",
            (owner, now.isoformat()),
        )
    return owner


def _release_sync_lease(owner: str, db_path: str | Path | None = None) -> None:
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM gmail_sync_lock WHERE singleton=1 AND owner=?", (owner,))


def _setting(key: str, default: str = "0", db_path: str | Path | None = None) -> str:
    _init_email_tables(db_path)
    with _connect(db_path) as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default


def set_auto_sync(enabled: bool, db_path: str | Path | None = None) -> None:
    _init_email_tables(db_path)
    with _connect(db_path) as connection:
        connection.execute(
            """INSERT INTO app_settings(key,value) VALUES('gmail_auto_sync',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            ("1" if enabled else "0",),
        )


def _google_modules() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise GmailIntegrationError(
            'Gmail dependencies are not installed. Run: python -m pip install -e ".[gmail]"'
        ) from exc
    return Request, Credentials, InstalledAppFlow, build, HttpError


def _save_token(credentials: Any) -> None:
    GMAIL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = GMAIL_TOKEN_PATH.with_suffix(".tmp")
    temp_path.write_text(credentials.to_json(), encoding="utf-8")
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    temp_path.replace(GMAIL_TOKEN_PATH)
    try:
        os.chmod(GMAIL_TOKEN_PATH, 0o600)
    except OSError:
        pass


def _load_credentials() -> tuple[Any, Any, Any]:
    Request, Credentials, _, build, HttpError = _google_modules()
    if not GMAIL_TOKEN_PATH.is_file():
        raise GmailIntegrationError("Gmail is not connected yet. Run job-agent email-connect first.")
    try:
        credentials = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), [GMAIL_SCOPE])
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise GmailIntegrationError(
            "The saved Gmail token is invalid. Run job-agent email-disconnect and reconnect."
        ) from exc
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                _save_token(credentials)
            except Exception as exc:
                raise GmailIntegrationError(
                    "Gmail authorization expired or was revoked. Run job-agent email-connect again."
                ) from exc
        else:
            raise GmailIntegrationError("Gmail authorization is no longer valid. Reconnect with job-agent email-connect.")
    return credentials, build, HttpError


def is_connected() -> bool:
    return GMAIL_TOKEN_PATH.is_file()


def get_status(db_path: str | Path | None = None) -> dict[str, Any]:
    _init_email_tables(db_path)
    with _connect(db_path) as connection:
        pending = int(connection.execute(
            "SELECT COUNT(*) FROM email_events WHERE disposition='needs_review'"
        ).fetchone()[0])
        last_row = connection.execute(
            "SELECT value FROM app_settings WHERE key='gmail_last_sync_at'"
        ).fetchone()
        email_row = connection.execute(
            "SELECT value FROM app_settings WHERE key='gmail_account'"
        ).fetchone()
    try:
        _google_modules()
        available = True
    except GmailIntegrationError:
        available = False
    return {
        "available": available,
        "connected": is_connected(),
        "credentials_configured": GMAIL_CONFIG_PATH.is_file(),
        "account": str(email_row["value"]) if email_row else "",
        "auto_sync": _setting("gmail_auto_sync", db_path=db_path) == "1",
        "last_sync_at": str(last_row["value"]) if last_row else "",
        "pending_review": pending,
        "sync_interval_minutes": AUTO_SYNC_INTERVAL_SECONDS // 60,
    }


def connect_gmail() -> dict[str, str]:
    """Run the Google OAuth desktop flow and persist a restricted-scope token."""
    _, _, InstalledAppFlow, build, _ = _google_modules()
    if not GMAIL_CONFIG_PATH.is_file():
        raise GmailIntegrationError(
            "OAuth client file not found. Create a Google Cloud Desktop app OAuth client, "
            f"download its JSON, and save it as {GMAIL_CONFIG_PATH}."
        )
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CONFIG_PATH), [GMAIL_SCOPE])
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            access_type="offline",
            prompt="consent",
            authorization_prompt_message=(
                "Open this URL in your browser to connect Gmail:\n{url}\n"
            ),
            success_message="Gmail connected. You can close this browser tab and return to Job-Agent.",
        )
        _save_token(credentials)
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        address = str(profile.get("emailAddress") or "")
        _init_email_tables()
        with _connect() as connection:
            connection.execute(
                """INSERT INTO app_settings(key,value) VALUES('gmail_account',?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (address,),
            )
        return {"account": address, "message": "Gmail is connected with read-only access."}
    except GmailIntegrationError:
        raise
    except Exception as exc:
        raise GmailIntegrationError(
            f"Gmail connection failed: {exc}. Confirm the Gmail API is enabled and the OAuth client is configured."
        ) from exc


def disconnect_gmail(db_path: str | Path | None = None) -> None:
    try:
        GMAIL_TOKEN_PATH.unlink(missing_ok=True)
    except OSError as exc:
        raise GmailIntegrationError(f"Could not remove the local Gmail token: {exc}") from exc
    _init_email_tables(db_path)
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM app_settings WHERE key IN ('gmail_account','gmail_last_sync_at')")
        connection.execute(
            """INSERT INTO app_settings(key,value) VALUES('gmail_auto_sync','0')
               ON CONFLICT(key) DO UPDATE SET value='0'"""
        )


def _decode_header(value: str) -> str:
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except (LookupError, UnicodeDecodeError, ValueError):
        return value


def _decode_part(data: str) -> str:
    try:
        # Keep unexpectedly large messages from consuming excessive memory during sync.
        bounded = data[:1_400_000]
        bounded = bounded[:len(bounded) - (len(bounded) % 4)]
        raw = base64.urlsafe_b64decode(bounded + "=" * (-len(bounded) % 4))
        return raw[:MAX_DECODED_BODY_CHARS].decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _plain_text(payload: dict[str, Any]) -> str:
    """Decode text bodies in memory; never persist attachment data or the full body."""
    mime = str(payload.get("mimeType", ""))
    if mime.startswith("multipart/"):
        chunks = []
        for part in payload.get("parts", []) or []:
            chunks.append(_plain_text(part))
        return "\n".join(chunk for chunk in chunks if chunk)
    if mime == "text/plain":
        body = payload.get("body", {})
        return _decode_part(str(body.get("data", ""))) if isinstance(body, dict) else ""
    if mime == "text/html":
        body = payload.get("body", {})
        html = _decode_part(str(body.get("data", ""))) if isinstance(body, dict) else ""
        html = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", html)
        return unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    return ""


def classify_email(subject: str, body: str, snippet: str = "") -> tuple[str | None, str]:
    """Infer only explicit application lifecycle messages, not generic recruiter contact."""
    combined = re.sub(r"\s+", " ", f"{subject}\n{body}\n{snippet}").casefold()
    for status, patterns in STATUS_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, combined, re.I):
                return status, f"Matched explicit {status} wording."
    return None, "No explicit application status phrase found."


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in COMPANY_SUFFIXES
    }


def _text_normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _sender_domain(sender: str) -> str:
    _, address = parseaddr(sender)
    return address.rsplit("@", 1)[-1].casefold() if "@" in address else ""


def _domain_matches_company(sender: str, company: str) -> bool:
    domain = _sender_domain(sender)
    if not domain or domain in GENERIC_MAIL_HOSTS:
        return False
    company_tokens = _tokens(company)
    domain_tokens = _tokens(domain.split(".")[0].replace("-", " "))
    return bool(company_tokens and domain_tokens and company_tokens.intersection(domain_tokens))


def match_email_to_job(
    subject: str,
    body: str,
    sender: str,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the best job candidate and a conservative confidence score."""
    content = f"{subject}\n{body}"
    normalized_content = _text_normalized(content)
    content_tokens = _tokens(content)
    company_counts: dict[str, int] = {}
    for item in jobs:
        normalized_company = _text_normalized(str(item.get("company") or ""))
        company_counts[normalized_company] = company_counts.get(normalized_company, 0) + 1
    scored: list[tuple[float, dict[str, Any], str]] = []
    for job in jobs:
        company = str(job.get("company") or "")
        title = str(job.get("title") or "")
        company_norm = _text_normalized(company)
        company_tokens = _tokens(company)
        company_match = (
            bool(company_norm and re.search(rf"\\b{re.escape(company_norm)}\\b", normalized_content))
            or bool(company_tokens and company_tokens.issubset(content_tokens))
        )
        domain_match = _domain_matches_company(sender, company)
        title_tokens = _tokens(title)
        title_match = (
            bool(title_tokens) and len(title_tokens) >= 2
            and title_tokens.issubset(content_tokens)
        )
        try:
            canonical_url = normalize_job_url(str(job.get("url") or ""))
        except ValueError:
            canonical_url = ""
        url_match = bool(canonical_url and canonical_url in content)

        if url_match:
            score, reason = 0.99, "The email references this job URL."
        elif (company_match or domain_match) and title_match:
            score, reason = 0.96, "The company and role both match the email."
        elif company_match and company_counts.get(company_norm, 0) == 1:
            score, reason = 0.76, "Company matches, but the email does not identify the role."
        elif domain_match and company_counts.get(company_norm, 0) == 1:
            score, reason = 0.70, "Sender domain matches the company, but the role is not explicit."
        elif company_match or domain_match or title_match:
            score, reason = 0.52, "Only part of the job details match; confirmation is required."
        else:
            continue
        scored.append((score, job, reason))

    if not scored:
        return {"job_id": None, "confidence": 0.0, "reason": "No tracked job matched the sender or email content."}
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_job, best_reason = scored[0]
    if len(scored) > 1 and best_score - scored[1][0] < 0.08:
        return {
            "job_id": int(best_job["id"]),
            "confidence": min(best_score, 0.65),
            "reason": "Multiple tracked jobs are plausible matches; choose the correct one.",
        }
    return {
        "job_id": int(best_job["id"]),
        "confidence": best_score,
        "reason": best_reason,
    }


def _received_at(message: dict[str, Any], headers: dict[str, str]) -> str:
    internal_ms = message.get("internalDate")
    try:
        return datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    try:
        parsed = parsedate_to_datetime(headers.get("date", ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _received_date(received_at: str) -> date | None:
    try:
        parsed = datetime.fromisoformat(received_at)
        return parsed.astimezone().date()
    except (TypeError, ValueError, OverflowError):
        return None


def _message_headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    wanted = {"from", "subject", "date"}
    result: dict[str, str] = {}
    for item in headers:
        name = str(item.get("name", "")).casefold()
        if name in wanted:
            result[name] = _decode_header(str(item.get("value", "")))
    return result


def _recent_message_ids(service: Any, max_results: int) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    page_token = None
    while len(messages) < max_results:
        page_size = min(100, max_results - len(messages))
        request = service.users().messages().list(
            userId="me", q=GMAIL_QUERY, maxResults=page_size, **({"pageToken": page_token} if page_token else {})
        )
        response = request.execute()
        messages.extend(
            {"id": str(item["id"]), "threadId": str(item.get("threadId", ""))}
            for item in response.get("messages", []) or []
            if item.get("id")
        )
        page_token = response.get("nextPageToken")
        if not page_token or not response.get("messages"):
            break
    return messages[:max_results]


def sync_gmail(
    db_path: str | Path | None = None,
    max_results: int = MAX_MESSAGES_PER_SYNC,
) -> dict[str, Any]:
    """Sync recent candidate emails; automatically update only high-confidence job matches."""
    if not 1 <= max_results <= MAX_MESSAGES_PER_SYNC:
        raise GmailIntegrationError(f"max_results must be between 1 and {MAX_MESSAGES_PER_SYNC}.")
    if not SYNC_LOCK.acquire(blocking=False):
        raise GmailIntegrationError("A Gmail sync is already running.")
    lease_owner: str | None = None
    try:
        credentials, build, HttpError = _load_credentials()
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        _init_email_tables(db_path)
        lease_owner = _acquire_sync_lease(db_path)
        jobs = list_jobs(db_path=db_path)
        with _connect(db_path) as connection:
            existing_ids = {
                str(row[0]) for row in connection.execute("SELECT message_id FROM email_events").fetchall()
            }
        inserted = auto_updated = review_count = ignored = 0
        try:
            message_refs = _recent_message_ids(service, max_results)
            # Gmail returns newest-first; process oldest-first so an older confirmation cannot
            # overwrite a newer outcome during a first sync.
            for message_ref in reversed(message_refs):
                message_id = message_ref["id"]
                if message_id in existing_ids:
                    continue
                try:
                    message = service.users().messages().get(
                        userId="me", id=message_id, format="full"
                    ).execute()
                except HttpError:
                    continue

                headers = _message_headers(message)
                subject = headers.get("subject", "")
                sender = headers.get("from", "")
                payload = message.get("payload", {}) or {}
                body = _plain_text(payload)
                snippet_raw = str(message.get("snippet", ""))
                status, status_reason = classify_email(subject, body, snippet_raw)
                received_at = _received_at(message, headers)
                received_date = _received_date(received_at)
                snippet = re.sub(r"\s+", " ", snippet_raw).strip()[:360]
                match = match_email_to_job(subject, body + "\n" + snippet_raw, sender, jobs) if status else {
                    "job_id": None, "confidence": 0.0, "reason": status_reason
                }
                will_auto_update = bool(
                    status is not None
                    and match["job_id"] is not None
                    and match["confidence"] >= 0.90
                )
                reason = f"{status_reason} {match['reason']}" if status else status_reason
                initial_disposition = "needs_review" if status is not None else "ignored"

                # Persist message identity before updating a job. If the process stops after this
                # point, the message remains visible for review instead of being auto-applied twice.
                with _connect(db_path) as connection:
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO email_events
                           (message_id,thread_id,subject,sender,received_at,summary,detected_status,
                            job_id,confidence,disposition,reason,processed_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            message_id, message_ref.get("threadId", ""),
                            subject[:500] if status else "", sender[:500] if status else "",
                            received_at, snippet if status else "",
                            status, match.get("job_id"), float(match.get("confidence", 0)),
                            initial_disposition, reason[:1000], datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    if cursor.rowcount != 1:
                        existing_ids.add(message_id)
                        continue
                existing_ids.add(message_id)
                inserted += 1

                if status is None:
                    ignored += 1
                    continue
                if will_auto_update:
                    try:
                        update_application_status(
                            int(match["job_id"]), status,
                            applied_date=received_date if status == "applied" else None,
                            notes=None,
                            db_path=db_path,
                        )
                        record_activity(
                            int(match["job_id"]), "gmail_status_update",
                            f"Email detected status={status}; sender={sender[:180]}; subject={subject[:180]}",
                            db_path=db_path,
                        )
                        final_disposition = "auto_applied"
                        auto_updated += 1
                    except Exception as exc:
                        final_disposition = "needs_review"
                        reason = f"Automatic update failed: {exc}. {reason}"
                        review_count += 1
                else:
                    final_disposition = "needs_review"
                    review_count += 1

                with _connect(db_path) as connection:
                    connection.execute(
                        "UPDATE email_events SET disposition=?, reason=? WHERE message_id=?",
                        (final_disposition, reason[:1000], message_id),
                    )
        except HttpError as exc:
            raise GmailIntegrationError(
                f"Gmail API request failed: {exc}. Check that the Gmail API is enabled and reconnect if needed."
            ) from exc

        now = datetime.now(timezone.utc).isoformat()
        try:
            profile = service.users().getProfile(userId="me").execute()
        except HttpError as exc:
            raise GmailIntegrationError(
                f"Gmail profile lookup failed: {exc}. Check the API and reconnect if needed."
            ) from exc
        address = str(profile.get("emailAddress") or "")
        with _connect(db_path) as connection:
            connection.execute(
                """INSERT INTO app_settings(key,value) VALUES('gmail_last_sync_at',?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (now,),
            )
            if address:
                connection.execute(
                    """INSERT INTO app_settings(key,value) VALUES('gmail_account',?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (address,),
                )
        return {
            "scanned": len(message_refs),
            "processed": inserted,
            "auto_updated": auto_updated,
            "needs_review": review_count,
            "ignored": ignored,
            "last_sync_at": now,
        }
    finally:
        if lease_owner is not None:
            try:
                _release_sync_lease(lease_owner, db_path)
            except Exception as exc:
                print(f"[gmail] Could not release sync lock: {exc}", flush=True)
        SYNC_LOCK.release()


def list_review_events(
    db_path: str | Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    _init_email_tables(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(
            """SELECT e.*, j.company AS job_company, j.title AS job_title, j.url AS job_url,
                      j.status AS current_status
               FROM email_events e
               LEFT JOIN jobs j ON j.id=e.job_id
               WHERE e.disposition='needs_review'
               ORDER BY e.received_at DESC LIMIT ?""",
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [dict(row) for row in rows]


def review_email_event(
    message_id: str,
    action: str,
    job_id: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    if action not in {"approve", "dismiss"}:
        raise GmailIntegrationError("Action must be approve or dismiss.")
    _init_email_tables(db_path)
    with _connect(db_path) as connection:
        event = connection.execute(
            "SELECT * FROM email_events WHERE message_id=?", (message_id,)
        ).fetchone()
    if event is None:
        raise GmailIntegrationError("This email review item no longer exists.")
    if event["disposition"] != "needs_review":
        raise GmailIntegrationError("This email has already been reviewed.")
    if action == "dismiss":
        with _connect(db_path) as connection:
            connection.execute(
                "UPDATE email_events SET disposition='dismissed' WHERE message_id=?",
                (message_id,),
            )
        return {"action": "dismissed", "message_id": message_id}

    chosen_job_id = int(job_id or event["job_id"] or 0)
    if chosen_job_id < 1:
        raise GmailIntegrationError("Choose a tracked job before approving this email.")
    job = next((item for item in list_jobs(db_path=db_path) if int(item["id"]) == chosen_job_id), None)
    if job is None:
        raise GmailIntegrationError("The selected tracked job was not found.")
    status = str(event["detected_status"] or "")
    allowed_statuses = {"applied", "interview", "rejected", "offer"}
    if status not in allowed_statuses:
        raise GmailIntegrationError("This email does not contain a supported application status.")
    received_at = str(event["received_at"] or "")
    applied_date = None
    if status == "applied" and received_at:
        try:
            applied_date = date.fromisoformat(received_at[:10])
        except ValueError:
            applied_date = None
    update_application_status(chosen_job_id, status, applied_date=applied_date, db_path=db_path)
    record_activity(
        chosen_job_id, "gmail_status_update_approved",
        f"Manually approved email status={status}; subject={str(event['subject'])[:180]}",
        db_path=db_path,
    )
    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE email_events SET job_id=?, disposition='approved' WHERE message_id=?",
            (chosen_job_id, message_id),
        )
    return {"action": "approved", "message_id": message_id, "job_id": chosen_job_id, "status": status}


def run_auto_sync_scheduler(stop_event: threading.Event) -> None:
    """Periodically sync Gmail only when the user explicitly enabled the option."""
    while not stop_event.wait(AUTO_SYNC_INTERVAL_SECONDS):
        try:
            if _setting("gmail_auto_sync") == "1" and is_connected():
                sync_gmail()
        except Exception as exc:
            print(f"[gmail] Automatic sync failed: {exc}", flush=True)
