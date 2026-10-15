# Gmail integration setup

Job-Agent's Gmail integration is local-only and requests the restricted Gmail read-only OAuth scope:

\`https://www.googleapis.com/auth/gmail.readonly\`

It uses the Gmail API, rather than browser scraping or IMAP passwords. It does not send, delete, archive, or label messages. Full message bodies are processed in memory; the local database stores message/thread IDs, subject, sender, timestamp, a short preview, detected status, candidate job ID, confidence, and review disposition.

## 1. Install dependencies

From the repository root, activate the project's virtual environment and install the optional Gmail dependencies:

\`\`\`bash
python -m pip install -e ".[gmail]"
\`\`\`

## 2. Configure Google Cloud

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select a project.
2. Enable the [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
3. Configure Google Auth Platform's branding and audience.
4. Create an OAuth client with application type **Desktop app**.
5. Download the client JSON and save it as \`config/gmail-credentials.json\`.
6. If the consent screen audience is External and the app is in Testing, add the Gmail account to Test users.

Keep \`config/gmail-credentials.json\` private. It is ignored by Git. The app writes its OAuth token to \`data/gmail/token.json\` with restrictive file permissions; that directory is ignored by Git too.

## 3. Connect and sync

\`\`\`bash
job-agent email-connect
job-agent email-status
job-agent email-sync
\`\`\`

The first command opens Google's OAuth consent flow. The sync command checks a bounded set of recent messages (up to 200 per sync) and processes each message only once.

## Dashboard behavior

The dashboard includes a Gmail connection card and email review queue. Use **Connect Gmail** to run OAuth, **Sync now** for a manual sync, or enable automatic sync. When enabled, the local dashboard process checks Gmail every 15 minutes. Automatic syncing stops when the dashboard exits.

Explicit, high-confidence matches can update the job status automatically. Ambiguous or unmatched status emails go to the review queue. Select the correct tracked job and approve, or dismiss the message. No application is ever submitted by this feature.

Supported automatic status detections are Applied, Interview, Rejected, and Offer. Generic recruiter contact or messages without explicit lifecycle wording are ignored. Gmail messages themselves are never modified.

## OAuth testing caveat

Google documents that refresh tokens for an External OAuth app in Testing expire after seven days when Gmail scopes are requested. For personal use, reconnect if the token expires. A public app using Gmail restricted scopes may require additional verification and compliance work; this project is intended as a private local tool.

References:

- [Gmail API Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
- [List Gmail messages](https://developers.google.com/workspace/gmail/api/guides/list-messages)
- [Choose Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [OAuth 2.0 policies](https://developers.google.com/identity/protocols/oauth2/policies)

## Disconnect

Use Disconnect in the dashboard or run:

\`\`\`bash
job-agent email-disconnect
\`\`\`

This removes the locally saved token and turns off automatic sync. To revoke the Google grant as well, remove Job-Agent from [Google Account connections](https://myaccount.google.com/connections).
