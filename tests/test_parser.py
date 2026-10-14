# Unit tests for job_parser module
#
# Tests:
#    - parse_job_from_url(): Extract title, company, description from HTML
#    - normalize_job_data(): Validate and clean parsed data
#    - parse_job_from_url() with LinkedIn: Extract from LinkedIn job page
#    - parse_job_from_url() with Greenhouse: Extract from ATS form
#    - parse_job_from_url() with error: Handle timeout, 404, parse failure gracefully
#
# Setup: Use sample HTML fixtures for different job board types
