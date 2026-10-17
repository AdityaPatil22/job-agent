# Job-Agent

A lightweight local job-application assistant built with Python, Playwright, and SQLite. Paste a job URL, choose a resume, autofill the supported fields, review unknown questions, and submit the application yourself.

## What it does

- **Job tracking:** Save jobs from career pages and track application status in SQLite.
- **Assisted autofill:** Upload a resume where supported, fill known candidate details, and report fields that need review.
- **Persistent sessions:** Use either Job-Agent's own persistent Playwright profile or attach to a dedicated Google Chrome profile.
- **Human checkpoints:** Pause on login pages and leave custom or ambiguous questions for review.
- **Application management:** Track status and export the tracker to CSV.
- **Local-first:** Candidate profile, browser state, database, and resume files stay on your machine.

## Tech stack

- Python 3.12+
- Playwright
- SQLite
- Pydantic
- Requests and BeautifulSoup

## Getting started

### 1. Install dependencies

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

### 2. Set up your profile and field mappings

```bash
cp config/profile.example.json config/profile.json
cp config/field_mappings.example.json config/field_mappings.json
```

Edit both files with your real candidate information. Do not commit personal details.

### 3. Add your resumes

Put role-specific resumes in the `resumes/` directory, for example:

```text
resumes/
├── ai-engineer.pdf
└── infrastructure.pdf
```

### 4. Start the dashboard (optional)

```bash
job-agent start
```

Open `http://127.0.0.1:8765`. The dashboard server binds to loopback by default.

### 5. Add a job and autofill it

```bash
job-agent add-job "https://careers.example.com/jobs/123"
job-agent jobs
job-agent apply --job-id 1 --resume ai-engineer.pdf
```

By default, `apply` uses Job-Agent's managed Playwright Chromium profile. That profile persists between runs, but it is separate from your everyday Chrome profile.

## Use Google Chrome with persistent login sessions

Use this mode when you want Job-Agent to work inside a visible Google Chrome window and preserve the login state between application runs.

### 1. Start the dedicated Job-Agent Chrome profile

```bash
job-agent browser
```

This launches Google Chrome with a dedicated profile stored at `data/chrome_profile/` and Chrome DevTools Protocol (CDP) bound to `127.0.0.1:9222`. Sign in to your Google account and career portals in this window. You only need to sign in again if the relevant site expires its session.

To use a different local CDP port or open a different starting page:

```bash
job-agent browser --port 9223 --url "https://accounts.google.com/"
```

### 2. Apply using the Chrome session

```bash
job-agent apply --job-id 1 --resume ai-engineer.pdf --browser chrome
```

For a non-default CDP port, pass the matching local endpoint:

```bash
job-agent apply --job-id 1 --resume ai-engineer.pdf \
  --browser chrome --cdp-url http://127.0.0.1:9223
```

Chrome mode opens a new tab for each job and leaves Chrome, its profile, and other tabs open when Job-Agent finishes. It does not click the final Submit button; review the completed application and submit it yourself.

### Important Chrome profile limitation

Chrome does not allow reliable CDP attachment to an ordinary Chrome session that was started without remote debugging. Recent Chrome versions also restrict remote debugging against the default Chrome user-data directory. For that reason, `job-agent browser` uses a separate persistent profile rather than reading cookies from your normal everyday Chrome profile. You sign in to Google once in the Job-Agent Chrome window and its sessions persist there.

The debugging port exposes powerful browser control. Job-Agent binds it to localhost only; do not change the debugging address to `0.0.0.0`, expose the port to your network, or use this mode on a shared machine. Do not manually launch another Chrome process against the same `data/chrome_profile/` directory.

If Chrome is not found automatically, install Google Chrome and ensure it is available in a standard installation location or on your system `PATH`.

## Login, account creation, and CAPTCHA handling

Job-Agent cannot guarantee that every career site will accept automated form filling. ATS platforms and employer-specific portals use different page structures, identity systems, validation, and custom questions.

When a site requires sign-in, account creation, MFA, CAPTCHA, or another user-only step, complete that step in the visible browser. Use a supported login page and continue after authentication. Do not attempt to bypass CAPTCHA or multi-factor authentication. If a form control or question is ambiguous, leave it for manual review.

## CLI commands

```bash
job-agent start
job-agent browser
job-agent add-job "https://careers.example.com/jobs/123"
job-agent jobs --status shortlisted
job-agent apply --job-id 1 --resume ai-engineer.pdf
job-agent apply --job-id 1 --resume ai-engineer.pdf --browser chrome
job-agent mark-applied --job-id 1
job-agent status --job-id 1
job-agent export --format csv
```

## Gmail integration

The dashboard can connect to Gmail using a local OAuth Desktop client with read-only mailbox access. It can detect application confirmations, interview requests, rejections, and offers. High-confidence matches update the tracker; ambiguous messages appear in the dashboard review queue. The integration does not send or modify mail.

See [the Gmail setup guide](docs/gmail-setup.md), then run:

```bash
python -m pip install -e ".[gmail]"
job-agent email-connect
job-agent email-sync
job-agent email-status
```

Automatic syncing can be enabled in the dashboard and runs every 15 minutes while the dashboard is open. OAuth credentials and tokens remain local and should not be committed.

## Data safety and recovery

- The dashboard binds to loopback by default.
- Candidate profile data, local browser state, SQLite database files, resume files, OAuth tokens, and exported application data should remain local.
- Do not commit real profile data or Chrome profile files.
- CSV import validates rows before writing, merges rows by normalized URL, and creates a SQLite backup before importing.
- CSV exports prefix potentially executable spreadsheet cells to reduce formula-injection risk.

To restore a database backup, stop the dashboard first and make a copy of the current database before replacing it with the selected file in `data/backups/`.

## Tests

```bash
python -m compileall -q src tests
python -m pytest -q
```

GitHub Actions runs compile and test steps for pushes and pull requests. Tests use local or mocked browser behavior and must not submit real applications.
