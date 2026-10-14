# Load and validate configuration files
#
# Loads:
#   - config/profile.json: Candidate profile (name, email, phone, education, experience)
#   - config/field_mappings.json: Form field aliases (email -> [email, email_address, work_email])
#   - config/preferences.json (future): Job search filters
#
# Uses Pydantic for validation and type safety
# Provides singleton-like access to config via Config class or env vars
