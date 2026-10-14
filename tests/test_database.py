# Unit tests for database module
#
# Tests:
#    - init_db(): Verify tables are created
#    - add_job(): Insert new job, verify stored in DB
#    - add_job() dedup: Verify duplicate URLs are handled (update vs insert)
#    - get_job(): Retrieve job by ID
#    - list_jobs(status): Filter by status
#    - update_application_status(): Change status (applied, rejected, etc)
#    - get_application(): Retrieve application record
#    - export_csv(): Verify CSV format and data integrity
#
# Setup: Use in-memory SQLite (":memory:") for fast tests
