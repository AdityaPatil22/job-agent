"""Command-line entry point for Job-Agent."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import click

from .autofill import autofill_form, prepare_application_page, wait_for_submit_button
from .browser import (
    CHROME_PROFILE_DIR,
    DEFAULT_CDP_PORT,
    DEFAULT_CDP_URL,
    BrowserManager,
    ensure_chrome_ready,
    start_chrome,
)
from .config import ConfigError, load_field_mappings, load_profile
from .database import (
    add_job,
    export_csv,
    get_application,
    get_job,
    init_db,
    list_jobs,
    update_application_status,
)
from .job_parser import parse_job_from_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"
VALID_STATUS_CHOICES = (
    "discovered", "shortlisted", "ready", "applied",
    "interview", "offer", "rejected", "withdrawn",
)


@click.group()
@click.version_option()
def cli() -> None:
    """Track job applications and assist with repetitive application forms."""
    init_db()


@cli.command("start")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Local interface to bind the dashboard to. Non-loopback hosts are not allowed.",
)
@click.option(
    "--port",
    type=click.IntRange(min=1, max=65535),
    default=8765,
    show_default=True,
    help="Port to serve the dashboard on.",
)
def start_command(host: str, port: int) -> None:
    """Start the local web dashboard."""
    from .dashboard import serve

    try:
        serve(host=host, port=port)
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Could not start dashboard: {exc}") from exc


@cli.command("browser")
@click.option(
    "--port",
    type=click.IntRange(min=1, max=65535),
    default=DEFAULT_CDP_PORT,
    show_default=True,
    help="Local Chrome DevTools Protocol port.",
)
@click.option(
    "--url",
    default="https://accounts.google.com/",
    show_default=True,
    help="Page to open in the new Job-Agent Chrome profile.",
)
def browser_command(port: int, url: str) -> None:
    """Launch a dedicated persistent Google Chrome profile for Job-Agent."""
    try:
        started = start_chrome(profile_dir=CHROME_PROFILE_DIR, port=port, url=url)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if started:
        click.secho("Started Google Chrome with the Job-Agent profile.", fg="green")
        click.echo(f"Profile: {CHROME_PROFILE_DIR}")
        click.echo(f"Local debugging endpoint: http://127.0.0.1:{port}")
        click.echo(
            "Sign in to Google and your career portals in this Chrome window once. "
            "Job-Agent will reuse this profile on later runs."
        )
    else:
        click.echo(f"Chrome is already listening on http://127.0.0.1:{port}.")
        click.echo("Use that Chrome window to sign in, then run apply with --browser chrome.")


@cli.command("email-connect")
def email_connect_command() -> None:
    """Connect Gmail using Google's local OAuth flow with read-only mailbox access."""
    from .gmail_sync import GmailIntegrationError, connect_gmail

    try:
        result = connect_gmail()
    except GmailIntegrationError as exc:
        raise click.ClickException(str(exc)) from exc
    account = result.get("account") or "Google account"
    click.secho(f"Gmail connected for {account}. {result.get('message', '')}", fg="green")


@cli.command("email-sync")
@click.option("--limit", type=click.IntRange(min=1, max=200), default=200, show_default=True)
def email_sync_command(limit: int) -> None:
    """Check recent Gmail messages and update tracked application statuses."""
    from .gmail_sync import GmailIntegrationError, sync_gmail

    try:
        result = sync_gmail(max_results=limit)
    except GmailIntegrationError as exc:
        raise click.ClickException(str(exc)) from exc
    click.secho(
        "Gmail sync complete: "
        f"{result['processed']} new messages processed, "
        f"{result['auto_updated']} statuses updated, "
        f"{result['needs_review']} need review, "
        f"{result['ignored']} unrelated messages skipped.",
        fg="green",
    )


@cli.command("email-status")
def email_status_command() -> None:
    """Show Gmail connection and automatic sync status."""
    from .gmail_sync import get_status

    status = get_status()
    click.echo(f"Gmail dependencies installed: {'yes' if status['available'] else 'no'}")
    click.echo(f"Connected: {'yes' if status['connected'] else 'no'}")
    click.echo(f"Account: {status['account'] or 'Not connected'}")
    click.echo(f"Automatic sync: {'on' if status['auto_sync'] else 'off'}")
    click.echo(f"Last sync: {status['last_sync_at'] or 'Never'}")
    click.echo(f"Emails awaiting review: {status['pending_review']}")


@cli.command("email-disconnect")
def email_disconnect_command() -> None:
    """Remove the local Gmail token and turn off automatic sync."""
    from .gmail_sync import GmailIntegrationError, disconnect_gmail

    try:
        disconnect_gmail()
    except GmailIntegrationError as exc:
        raise click.ClickException(str(exc)) from exc
    click.secho(
        "Local Gmail credentials removed and automatic sync disabled. "
        "To revoke the Google grant as well, remove Job-Agent from your Google Account's third-party connections.",
        fg="yellow",
    )


@cli.command("add-job")
@click.argument("url")
def add_job_command(url: str) -> None:
    """Parse a career-page URL and add the job to the local tracker."""
    try:
        click.echo(f"Fetching job page: {url}")
        job = parse_job_from_url(url)
        job_id = add_job(job)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.secho(f"Saved job #{job_id}: {job.title} — {job.company}", fg="green")
    if job.title == "Untitled role" or job.company == "Unknown company":
        click.echo("Some details could not be detected. You can still track the job.")


@cli.command("jobs")
@click.option("--status", type=click.Choice(VALID_STATUS_CHOICES, case_sensitive=False))
def jobs_command(status: str | None) -> None:
    """List tracked jobs, optionally filtered by application status."""
    rows = list_jobs(status=status.lower() if status else None)
    if not rows:
        click.echo("No jobs found.")
        return
    for row in rows:
        click.echo(f"#{row['id']} [{row['status']}] {row['title']} — {row['company']}\n   {row['url']}")


@cli.command("apply")
@click.option("--job-id", type=click.IntRange(min=1), required=True)
@click.option("--resume", "resume_name", required=True, help="Resume filename or basename in resumes/.")
@click.option("--headless", is_flag=True, help="Run managed Chromium without a visible window (not recommended).")
@click.option(
    "--browser",
    "browser_mode",
    type=click.Choice(["managed", "chrome"], case_sensitive=False),
    default="managed",
    show_default=True,
    help="Use Job-Agent's Playwright browser or attach to Chrome started with 'job-agent browser'.",
)
@click.option(
    "--cdp-url",
    default=DEFAULT_CDP_URL,
    show_default=True,
    help="Local Chrome DevTools endpoint for --browser chrome.",
)
def apply_command(
    job_id: int,
    resume_name: str,
    headless: bool,
    browser_mode: str,
    cdp_url: str,
) -> None:
    """Open a tracked job, upload a resume, and assist with form filling."""
    job = get_job(job_id)
    if job is None:
        raise click.ClickException(f"Job #{job_id} was not found. Run 'job-agent jobs' to list jobs.")
    try:
        # Validate both local config files before opening a browser so a missing
        # mapping file cannot look like a silent autofill failure.
        profile = load_profile()
        field_mappings = load_field_mappings()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    if browser_mode.lower() == "chrome" and headless:
        raise click.ClickException("--headless cannot be used with --browser chrome.")

    click.echo(f"Opening {job['title']} — {job['company']}")
    if browser_mode.lower() == "chrome":
        click.echo("Preparing the persistent Job-Agent Chrome profile. Existing Chrome tabs will remain open.")
    else:
        click.echo("Opening Job-Agent's managed Playwright browser. Login if needed and review every field manually.")

    manager: BrowserManager | None = None
    try:
        if browser_mode.lower() == "chrome":
            started = ensure_chrome_ready(cdp_url)
            if started:
                click.echo(
                    "Started Job-Agent Chrome. Sign in to the career portal in that window if prompted."
                )
        manager = BrowserManager(headless=headless, mode=browser_mode.lower(), cdp_url=cdp_url)
        page = manager.goto(job["url"])
        page = prepare_application_page(page, field_mappings)
        report = autofill_form(
            page,
            job_id,
            resume_name,
            profile=profile,
            resumes_dir=RESUMES_DIR,
            field_mappings=field_mappings,
        )
        click.echo(report.render())
        form_detected = not any(
            "No recognizable application form was found" in item
            for item in report.skipped
        )
        if browser_mode.lower() == "managed":
            # Managed mode closes its context on exit, so allow a human review
            # pause. Persistent Chrome mode stays open and must not block the
            # dashboard task waiting for terminal input.
            if form_detected:
                wait_for_submit_button(page)
            else:
                input(
                    "No application form was detected. Complete any manual login/navigation if needed, "
                    "then press Enter to close this managed browser; rerun autofill afterward. "
                )
        elif form_detected:
            click.echo(
                "\nAutofill finished. The Chrome tab will remain open for your review. "
                "Complete any unanswered questions and submit the application yourself."
            )
        else:
            click.echo(
                "\nNo application form was detected. The Chrome tab remains open. "
                "Open the application or sign in manually, then run autofill again."
            )

        if form_detected:
            update_application_status(
                job_id,
                "ready",
                resume_used=Path(resume_name).stem,
                notes="Form autofill completed; user review required. Not submitted by the tool.",
            )
            click.secho(
                "Saved status as 'ready'. Submit the application yourself after reviewing the page.",
                fg="yellow",
            )
    except KeyboardInterrupt:
        click.echo("\nInterrupted. The browser window will remain open in Chrome mode.")
    except Exception as exc:
        raise click.ClickException(f"Application assistance failed: {exc}") from exc
    finally:
        if manager is not None:
            manager.close()


@cli.command("mark-applied")
@click.option("--job-id", type=click.IntRange(min=1), required=True)
@click.option("--notes", default=None, help="Optional note about the submission.")
def mark_applied_command(job_id: int, notes: str | None) -> None:
    """Mark a job as applied after you submit it yourself."""
    if get_job(job_id) is None:
        raise click.ClickException(f"Job #{job_id} was not found.")
    update_application_status(job_id, "applied", applied_date=date.today(), notes=notes)
    click.secho(f"Job #{job_id} marked as applied.", fg="green")


@cli.command("status")
@click.option("--job-id", type=click.IntRange(min=1), required=True)
def status_command(job_id: int) -> None:
    """Show details for a job and its application status."""
    job = get_job(job_id)
    if job is None:
        raise click.ClickException(f"Job #{job_id} was not found.")
    application = get_application(job_id)
    click.echo(f"Job #{job_id}: {job['title']} — {job['company']}")
    click.echo(f"URL: {job['url']}")
    click.echo(f"Location: {job['location'] or 'Not detected'}")
    click.echo(f"Status: {application['status'] if application else 'discovered'}")
    if application:
        click.echo(f"Resume: {application['resume_used'] or 'Not recorded'}")
        click.echo(f"Applied date: {application['applied_date'] or 'Not recorded'}")
        click.echo(f"Next action: {application['next_action'] or 'Not set'}")
        click.echo(f"Notes: {application['notes'] or 'None'}")


@cli.command("export")
@click.option("--format", "output_format", type=click.Choice(["csv"]), default="csv")
@click.option("--output", type=click.Path(path_type=Path), default=Path("data/applications.csv"))
def export_command(output_format: str, output: Path) -> None:
    """Export the tracker as a CSV file."""
    del output_format
    result = export_csv(output)
    click.secho(f"Exported tracker to {result.resolve()}", fg="green")


def main() -> None:
    """Allow python -m job_agent.main."""
    cli()


if __name__ == "__main__":
    main()
