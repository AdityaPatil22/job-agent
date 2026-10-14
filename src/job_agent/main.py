# CLI entry point for job-agent
#
# Implements commands:
#   - add-job <url>: Scrape job title/company from URL, store in DB
#   - apply --job-id <id> --resume <name>: Open job, upload resume, autofill form, pause for review
#   - jobs --status <status>: List jobs filtered by status
#   - mark-applied --job-id <id>: Update job status to 'applied'
#   - export --format csv: Export application history
#   - status --job-id <id>: Show application status and details
#
# Uses Click or argparse for CLI parsing
# Orchestrates: database, browser, job_parser, autofill modules
