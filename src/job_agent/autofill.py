# Form detection and autofill logic
#
# Two filling strategies:
#
# 1. Resume-first (50-60% of sites):
#    - Detect file input for resume upload
#    - Locate resume file from resumes/ folder
#    - Upload resume
#    - Wait for ATS to parse (1-2 sec)
#    - Return list of fields detected as filled (user eyeballs)
#
# 2. Manual field filling (40-50% of sites):
#    - Use Playwright's get_by_label(), get_by_role() to find form fields
#    - Consult field_mappings.json for field aliases
#    - Fill text inputs (name, email, phone) from profile.json
#    - Fill selects/dropdowns (education, experience) from profile
#    - Skip custom questions (user fills manually)
#    - Generate gap report
#
# Multi-page handling:
#    - Detect and click "Next" button
#    - Repeat until "Submit" button appears
#    - Pause for user review before submit
#
# Functions:
#    - autofill_form(page, job_id, resume_name): Main entry point
#    - detect_resume_upload(page): Check if site has resume file input
#    - upload_resume(page, resume_path): Upload resume file
#    - fill_standard_fields(page, profile): Fill known fields
#    - fill_field(page, field_label, value): Fill single field with error handling
#    - detect_next_button(page): Find and click Next button
#    - generate_gap_report(): Return FormGapReport
#    - wait_for_submit_button(page): Pause until Submit ready
