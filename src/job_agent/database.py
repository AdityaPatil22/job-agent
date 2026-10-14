# SQLite database initialization, schema, and CRUD operations
#
# Tables:
#   - jobs: id, company, title, url (unique), description, location, date_found
#   - applications: id, job_id, status, resume_used, applied_date, next_action, notes
#   - contacts: company, name, title, profile_url, outreach_status (future)
#   - activity_log: entity_id, event_type, timestamp, outcome (future)
#
# Functions:
#   - init_db(): Create tables if not exist
#   - add_job(job_data): Insert or update job (dedup by URL)
#   - get_job(job_id): Retrieve job by ID
#   - list_jobs(status): List jobs filtered by status
#   - update_application_status(job_id, status): Update application status
#   - get_application(job_id): Retrieve application record
#   - export_csv(): Export all applications to CSV
#
# Database file: data/jobs.db (in .gitignore)
