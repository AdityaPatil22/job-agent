# Job-Agent

A lightweight Python automation tool to streamline job applications. Automate form filling with Playwright, track applications in SQLite, and manage referrals—all locally on your Mac.

## What it does

- **Job tracking**: Store jobs from career pages and LinkedIn in a local SQLite database
- **Form autofill**: Upload your resume → let the ATS auto-fill fields → review gaps → submit
- **Application management**: Track status (discovered → shortlisted → applied → interview/rejected)
- **Batch processing**: Apply to 20-30 jobs per day with 70% automation, manual fill for custom questions
- **Persistent sessions**: Stay logged in to portals across multiple applications

## Tech stack

- **Python 3.12+** — scripting and automation
- **Playwright** — browser automation for form filling
- **SQLite** — local job and application database
- **Pydantic** — configuration validation
- **Requests/BeautifulSoup** — page parsing (later)

## Project structure

```
job-agent/
├── README.md                 # This file
├── PLAN.md                   # Architecture and implementation plan
├── pyproject.toml            # Python dependencies
├── config/
│   ├── profile.json          # Your candidate profile (copy from .example)
│   ├── field_mappings.json   # Form field aliases (copy from .example)
│   └── preferences.json      # Job search filters (future)
├── data/
│   └── jobs.db              # SQLite database (jobs, applications)
├── resumes/
│   ├── ai-engineer.pdf      # Role-specific resumes
│   └── infrastructure.pdf
├── src/
│   └── job_agent/
│       ├── __init__.py
│       ├── main.py          # CLI entry point
│       ├── config.py        # Load and validate profile/config
│       ├── database.py      # SQLite schema and operations
│       ├── browser.py       # Playwright browser session management
│       ├── autofill.py      # Form detection and field filling
│       ├── job_parser.py    # Parse job details from URLs
│       ├── urls.py          # Canonical job URL normalization
│       └── models.py        # Pydantic data models
├── tests/
│   ├── test_database.py     # SQLite operations
│   ├── test_autofill.py     # Form field detection
│   ├── test_dashboard.py    # CSV, backup and URL regressions
│   └── test_parser.py       # Job page parsing
├── .gitignore
└── .env.example             # Template for local secrets
```

## Getting started

1. **Install dependencies**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -e ".[dev]"
   python -m playwright install chromium
   ```

2. **Set up configuration**
   ```bash
   cp config/profile.example.json config/profile.json
   cp config/field_mappings.example.json config/field_mappings.json
   # Edit with your details
   ```

3. **Add resumes**
   ```bash
   cp your_resume.pdf resumes/ai-engineer.pdf
   cp your_other_resume.pdf resumes/infrastructure.pdf
   ```

4. **Add a job**
   ```bash
   python -m job_agent add-job https://career-page.com/job/123
   ```

5. **Apply to a job**
   ```bash
   python -m job_agent apply --job-id 42 --resume ai-engineer
   ```

6. **Review applications**
   ```bash
   python -m job_agent jobs --status applied
   ```

## MVP scope

See `PLAN.md` for full architecture and implementation details.

**In scope (Phase 1-4):**
- Job intake, browser autofill, application tracking
- Form gap reporting with pause before submit
- Persistent browser session for 30 apps/day
- Multi-page form handling

**Out of scope (future):**
- Job discovery automation and intelligent filtering
- Referral workflow and cold outreach generation
- Scheduled execution and notifications

## Implementation milestones

1. **Foundation**: Database schema, profile config, CLI entry point
2. **Job intake**: `add-job` command with parsing and deduplication
3. **Browser & autofill** (critical path): Resume upload, field detection, gap reporting
4. **Application tracking**: `apply` command, status management, export
5. **Testing & refinement**: Unit tests, integration tests, real applications

See `PLAN.md` for detailed implementation plan, success criteria, and risk mitigations.


## Data safety and recovery

- The dashboard binds to loopback by default and is intended for local use only.
- Candidate profile data, local browser state, the SQLite database, and resume files should remain local. Do not commit personal data or exported application CSVs.
- Job URLs are normalized across common differences such as host casing, trailing slashes, fragments, and tracking parameters. Job-identifying query parameters are retained.
- CSV import validates all rows before writing, merges rows by normalized URL, and treats blank optional fields as "keep the existing value." Rows absent from an import are never deleted.
- Before importing into an existing database, Job-Agent creates a SQLite-native snapshot in \`data/backups/\`. CSV exports prefix potentially executable spreadsheet cells to reduce formula-injection risk.

To restore a snapshot, first stop the dashboard and make a copy of the current database. Then replace it with the chosen backup file:

\`\`\`bash
cp data/jobs.db data/jobs.db.before-restore
cp data/backups/<backup-file>.db data/jobs.db
\`\`\`

Keep the pre-restore copy until the dashboard opens and your records look correct. CSV exports are also useful for portability, but they are not a substitute for a database backup.

## Tests

Install the development dependencies and Chromium as shown above, then run:

\`\`\`bash
python -m compileall -q src tests
python -m pytest -q
\`\`\`

GitHub Actions runs the compile and test steps for pushes and pull requests. Browser tests use a local test page and do not submit real applications.
