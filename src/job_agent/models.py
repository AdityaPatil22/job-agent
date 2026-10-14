# Pydantic data models for type safety and validation
#
# Models:
#   - Job: company, title, url, description, location, date_found, score
#   - Application: job_id, status, resume_used, applied_date, next_action, notes
#   - CandidateProfile: name, email, phone, education, experience (loaded from profile.json)
#   - FormGapReport: filled fields, skipped fields, failed fields, unknown fields
#
# Used by: database.py, autofill.py, main.py
