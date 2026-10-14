"""Command-line entry point for Job-Agent."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import click

from .autofill import autofill_form, wait_for_submit_button
from .browser import BrowserManager
from .config import ConfigError, load_profile
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
    "interview", "rejected", "withdrawn",
)


@click.group()
@click.version_option()
def cli() -> None:
    """Track job applications and assist with repetitive application forms."""
    init_db()


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
@click.option("--headless", is_flag=True, help="Run Chromium without a visible window (not recommended).")
def apply_command(job_id: int, resume_name: str, headless: bool) -> None:
    """Open a tracked job, upload a resume, and assist with form filling."""
    job = get_job(job_id)
    if job is None:
        raise click.ClickException(f"Job #{job_id} was not found. Run 'job-agent jobs' to list jobs.")
    try:
        profile = load_profile()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Opening {job['title']} — {job['company']}")
    click.echo("A browser will open. Login if needed and review every field manually.")
    manager = BrowserManager(headless=headless)
    try:
        page = manager.goto(job["url"])
        report = autofill_form(page, job_id, resume_name, profile=profile, resumes_dir=RESUMES_DIR)
        click.echo(report.render())
        wait_for_submit_button(page)
        update_application_status(job_id, "ready", resume_used=Path(resume_name).stem,
                                  notes="Form autofill completed; user review required. Not submitted by the tool.")
        click.secho("Saved status as 'ready'. Submit the application yourself after reviewing the page.", fg="yellow")
    except KeyboardInterrupt:
        click.echo("\nInterrupted. The browser session will close.")
    except Exception as exc:
        raise click.ClickException(f"Application assistance failed: {exc}") from exc
    finally:
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
    """Show details and tracking status for a job."""
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
