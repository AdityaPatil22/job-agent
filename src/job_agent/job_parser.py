# Parse job details from URL (title, company, description)
#
# Used by: main.py add-job command
#
# Responsibilities:
#    - Accept job URL
#    - Fetch page using Playwright or requests
#    - Parse title, company, description from common HTML patterns
#    - Support career pages, LinkedIn, ATS forms
#    - Return normalized Job object (company, title, url, description)
#    - Handle errors gracefully (timeout, 404, parser failure)
#
# Functions:
#    - parse_job_from_url(url, playwright_page): Extract job data
#    - normalize_job_data(raw_data): Validate and clean
#    - detect_page_type(html): Identify if LinkedIn, Greenhouse, ATS, etc. (helper)
