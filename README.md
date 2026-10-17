# Job-Agent

A local job-application assistant built with Python, Playwright, and SQLite. Add a job URL, choose a resume, autofill supported application fields, review unknown questions, and submit the application yourself.

## Features

- **Job tracking:** Save job URLs and track application status in a local SQLite database.
- **Assisted autofill:** Upload a resume when supported, fill known candidate details, and report fields that need review.
- **Persistent browser sessions:** Use Job-Agent's managed Playwright browser or attach to a dedicated Google Chrome profile.
- **Gmail integration:** Match application emails to tracked jobs and help keep statuses up to date.
- **Human checkpoints:** Complete sign-in, account creation, MFA and CAPTCHA manually; review ambiguous answers and submit applications yourself.
- **Local-first storage:** Keep candidate profile data, browser state, the database, OAuth tokens and resumes on your machine.

## Requirements

- Python 3.12+
- Google Chrome (only needed for Chrome mode)
- A Google account and Google Cloud project (only needed for Gmail integration)

## 1. Install Job-Agent

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

## 2. Configure your candidate profile

Copy the example configuration files:

```bash
cp config/profile.example.json config/profile.json
cp config/field_mappings.example.json config/field_mappings.json
```

On Windows PowerShell:

```powershell
Copy-Item config/profile.example.json config/profile.json
Copy-Item config/field_mappings.example.json config/field_mappings.json
```

Edit `config/profile.json` with your candidate details, education, work experience, links and preferences. Edit `config/field_mappings.json` to configure field-name aliases. Keep real personal data private; these files are ignored by Git.

Put one or more resumes in the `resumes/` directory, for example:

```text
resumes/
├── ai-engineer.pdf
└── infrastructure.pdf
```

## 3. Run the dashboard and add a job

Start the local dashboard:

```bash
job-agent start
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The server binds to loopback by default.

Add a job URL and list your tracked jobs:

```bash
job-agent add-job "https://careers.example.com/jobs/123"
job-agent jobs
```

Apply to a tracked job with the managed Playwright browser:

```bash
job-agent apply --job-id 1 --resume ai-engineer.pdf
```

By default, this browser uses Job-Agent's own persistent Playwright profile. It is separate from your everyday Chrome profile.

## 4. Connect Job-Agent to Google Chrome

Chrome mode lets Playwright control a visible Chrome window with a persistent profile. This is useful when you need to log in to career portals once and reuse those sessions across multiple applications.

### Step 1: Launch the Job-Agent Chrome profile

Make sure Google Chrome is installed, then run:

```bash
job-agent browser
```

Job-Agent launches Chrome using the dedicated profile directory `data/chrome_profile/` and starts Chrome DevTools Protocol (CDP) on `127.0.0.1:9222`. Sign in to your Google account and any career portals in this Chrome window. Cookies and local browser state in this profile are retained for later runs.

You can change the local debugging port or starting page:

```bash
job-agent browser --port 9223 --url "https://accounts.google.com/"
```

### Step 2: Autofill a job in Chrome

First, add the job to Job-Agent if you have not already done so. Then run:

```bash
job-agent apply --job-id 1 --resume ai-engineer.pdf --browser chrome
```

If you chose a custom port, use its matching endpoint:

```bash
job-agent apply --job-id 1 --resume ai-engineer.pdf \
  --browser chrome --cdp-url http://127.0.0.1:9223
```

Chrome mode opens a new tab for the application. Job-Agent disconnects when it finishes but leaves Chrome, your profile and other tabs open. Review all fields and click the final Submit button yourself.

### Important: this is a separate Chrome profile

For security and compatibility, `job-agent browser` uses a dedicated `data/chrome_profile/` profile. It does **not** import cookies from your ordinary everyday Chrome profile, and you must sign in to Google and career portals once in the Job-Agent Chrome window. The session is then reused within that profile.

Recent Chrome versions restrict remote debugging against the regular default user-data directory. Do not copy profile cookies manually or run two Chrome instances against the same `data/chrome_profile/` directory.

**Security:** CDP grants powerful control over the browser. Job-Agent connects only to localhost. Do not expose port 9222/9223 to your network or change the debugging address to `0.0.0.0`. Do not use remote debugging on a shared or untrusted machine.

### Login and application recovery

Employer portals use different authentication and application flows. Complete sign-in, employer account creation, email verification, MFA, CAPTCHA and other user-only steps manually in the visible browser. Job-Agent does not bypass MFA/CAPTCHA and does not automatically submit applications. If a field or custom question is ambiguous, leave it for manual review.

## 5. Connect Gmail for application status tracking

The optional Gmail integration reads email messages to identify application confirmations, interview requests, rejections and offers. High-confidence messages can update the corresponding tracked job; ambiguous matches go to a review queue. Gmail access is read-only: Job-Agent does not send, delete, archive or label messages.

### Step 1: Install Gmail dependencies

Activate the project virtual environment and run this from the repository root:

```bash
python -m pip install -e ".[gmail]"
```

If you also want the development/test dependencies, install both extras:

```bash
python -m pip install -e ".[dev,gmail]"
```

### Step 2: Create/configure a Google Cloud project

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project or select a project you control.
2. Open the [Gmail API page](https://console.cloud.google.com/apis/library/gmail.googleapis.com) and enable the **Gmail API** for that project.
3. Open **Google Auth Platform**. Configure the app's branding/app information and audience if prompted.
4. On the **Audience** page, choose **External** if you are connecting a personal Gmail account. If the app remains in **Testing**, add your Google account under **Test users** and save the changes.
5. Open **Clients** and select **Create client**.
6. Set the application type to **Desktop app**, give it a recognizable name such as `Job-Agent Local`, and create the OAuth client.
7. Download the client JSON file.

Google may update the labels and layout in Cloud Console; the key settings are an enabled Gmail API, a configured OAuth consent screen, and an OAuth client of type **Desktop app**.

### Step 3: Save the OAuth client JSON locally

Create the `config/` directory if needed and save the downloaded JSON exactly here:

```text
config/gmail-credentials.json
```

Do not paste the contents into source code, commit it, or share it publicly. The repository's `.gitignore` excludes this credentials file and the local Gmail token directory.

### Step 4: Connect Gmail

Run:

```bash
job-agent email-connect
```

A browser window opens for Google's OAuth flow. Sign in to the Gmail account you added as a test user and approve the requested read-only Gmail permissions. Return to the terminal after authorization finishes.

Check the connection and run an initial sync:

```bash
job-agent email-status
job-agent email-sync
```

The integration stores its OAuth token locally under `data/gmail/`. Gmail message IDs and the details needed for matching/review are stored locally so already-processed messages are not repeatedly imported.

### Step 5: Use Gmail syncing from the dashboard

Open the Job-Agent dashboard with `job-agent start`. Use the Gmail integration card to connect Gmail, manually run **Sync now**, review ambiguous email matches, or enable automatic syncing. When enabled, syncing runs every 15 minutes while the dashboard process remains open. Automatic syncing stops when the dashboard exits.

The CLI command `job-agent email-sync` checks recent messages as well. It processes a bounded set of messages per sync (up to 200 by default). Detected statuses include Applied, Interview, Rejected and Offer; generic messages without clear application-status wording may be ignored or sent for review.

### OAuth testing and expiry notes

For a personal, local setup, leaving the Google OAuth consent screen in **Testing** is often sufficient when your account is added as a test user. Google can expire refresh tokens for External apps in Testing after seven days when Gmail scopes are requested, so you may need to connect again. Public distribution of an app using Gmail restricted scopes can require Google verification and additional compliance steps.

Official references:
- [Gmail API Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
- [Enable the Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
- [Choose Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Google OAuth app publishing policies](https://developers.google.com/identity/protocols/oauth2/policies)

### Disconnect Gmail

Run:

```bash
job-agent email-disconnect
```

This removes Job-Agent's locally stored token and turns off automatic syncing. To revoke the Google authorization as well, visit [Google Account connections](https://myaccount.google.com/connections) and remove the app.

## CLI reference

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
job-agent email-connect
job-agent email-status
job-agent email-sync
job-agent email-disconnect
```

## Data safety and recovery

- Keep candidate profile data, local Chrome/Chromium profiles, resumes, OAuth credentials/tokens, the SQLite database, and exported application data on your own machine.
- Do not commit `config/profile.json`, `config/field_mappings.json`, `config/gmail-credentials.json`, `data/chrome_profile/`, `data/chromium_profile/`, or `data/gmail/`.
- The dashboard binds to loopback by default.
- CSV exports prefix potentially executable spreadsheet cells to reduce formula-injection risk.
- Back up `data/jobs.db` before changing or restoring local data.

## Tests

```bash
python -m compileall -q src tests
python -m pytest -q
```

GitHub Actions runs compile and test steps for pushes and pull requests. Tests use local or mocked browser behavior and do not submit real applications.
