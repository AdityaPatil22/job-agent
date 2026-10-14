# Job-Agent Implementation Plan

Based on grilling session with focus on 20-30 job applications per day.

## Problem Statement

- **Bottleneck**: Repetitive form filling (20-25 min per application) + form variability across portals
- **Goal**: Save 50% of time (10 min per app) through automation while maintaining control and quality
- **Scope**: Manual job discovery on LinkedIn/career pages + automated form filling + tracking

## Architecture

### 1. Core Workflow

```
Manual discovery → Add job to DB → Fill form (auto + manual) → Track status
     (LinkedIn)                      (autofill, pause, review)    (SQLite)
```

**Daily operation:**
1. Find jobs on LinkedIn or career pages (manual, continuous)
2. Run `job-agent add-job <url>` → scrape title/company → store in SQLite
3. Run `job-agent apply --job-id 42 --resume ai-engineer`:
   - Open job URL in persistent Chromium profile
   - Detect if site has "upload resume to auto-fill" (50-60% of sites)
   - Upload appropriate resume file
   - Let ATS auto-populate fields
   - Show form gap report (filled/skipped/failed)
   - Pause for your review before submit
   - Fill custom questions and gaps manually
   - You click Submit (tool does NOT auto-submit)
4. Mark job as applied in DB

**Target**: 30 applications per day × 10 min saved per app = 5 hours/day saved

### 2. Resume Strategy

- **Multiple resumes**: ai-engineer.pdf, infrastructure.pdf, etc. in `resumes/` folder
- **Manual selection**: `--resume ai-engineer` flag selects which resume to upload
- **Resume-first**: Always prioritize site's "upload to auto-fill" feature over manual field filling

### 3. ATS Support

**Primary targets (high automation priority):**
- Greenhouse: Resume parsing widely supported
- Lever: Resume parsing widely supported  
- Workday: Resume parsing varies by employer config (NOT tested yet — risk)

**Fallback**: Generic Playwright locators for unknown ATSs (assumed to work 70% of time)

**Strategy**: Build Greenhouse/Lever adapters first. If Workday fails during MVP, fallback to manual.

### 4. Form Filling Logic

**Resume upload path (50-60% of sites):**
1. Detect file input for resume
2. Upload resume file via Playwright
3. Wait for ATS to parse (1-2 sec)
4. Let user eyeball results (tool doesn't detect what was filled)
5. User fills custom questions manually

**Manual field filling path (40-50% of sites):**
1. Use `field_mappings.json` to map form field labels → profile fields
2. Fallback to Playwright's `get_by_label()` and `get_by_role()` for unknown fields
3. Match: name, email, phone, address, education, past titles
4. Skip: custom questions (user fills from templates)
5. Report gaps: ✅ filled, ⚠️ skipped (no template), ❌ failed to locate

**Multi-page forms:**
- Fill visible fields → click "Next" → repeat until "Submit" button appears
- Pause for user review before Submit

### 5. Browser Session Management

- **Persistent Chromium profile** on disk (reuse across 30 apps/day)
- First login to Workday/Lever/Greenhouse → subsequent runs stay authenticated
- **Session timeout detection**: Tool detects logout page → pauses → asks user to re-login
- One browser session per CLI invocation (opens, fills form, pauses for review)

### 6. Database Schema

**Jobs table:**
- id (primary key)
- company
- title
- url (unique, normalized)
- description
- location
- date_found

**Applications table:**
- id (primary key)
- job_id (foreign key)
- status (discovered → shortlisted → ready → applied → interview/rejected/withdrawn)
- resume_used (ai-engineer, infrastructure, etc.)
- applied_date
- next_action (follow-up date)
- notes

**Contacts table (future):**
- company
- name
- title
- profile_url
- outreach_status

**Activity log (future):**
- entity_id
- event_type
- timestamp
- outcome

### 7. Form Gap Report Example

```
Job: Senior AI Engineer at Anthropic (Job ID: 42)
Resume uploaded: ai-engineer.pdf

Form Status:
  ✅ Filled (from resume): Name, Email, Phone, LinkedIn URL, Current Company, Job Title
  ⚠️ Skipped (no template): Why join Anthropic?, Salary expectation
  ❌ Failed to locate: employer_size (custom field name not recognized)
  ⓘ Unknown (user needs to fill): Authorization to work, Visa sponsorship

Ready to submit? Review the form above and fill gaps manually. [y/n]
```

### 8. Configuration

**profile.json** (your candidate data):
```json
{
  "name": "Your Name",
  "email": "you@email.com",
  "phone": "+1-XXX-XXX-XXXX",
  "linkedin_url": "https://linkedin.com/in/yourprofile",
  "current_company": "Company Name",
  "education": [
    {"school": "University", "degree": "BS", "field": "Computer Science"}
  ],
  "experience": [
    {"title": "Software Engineer", "company": "Acme", "duration": "3 years"}
  ]
}
```

**field_mappings.json** (form field aliases):
```json
{
  "email": ["email", "email_address", "work_email", "personal_email"],
  "phone": ["phone", "phone_number", "mobile", "cell"],
  "name": ["name", "full_name", "first_name"],
  ...
}
```

**preferences.json** (future):
```json
{
  "target_roles": ["AI Engineer", "AI Platform Engineer"],
  "excluded_keywords": ["blockchain", "crypto"]
}
```

### 9. CLI Commands (MVP)

```bash
# Add a job from URL
job-agent add-job https://careers.company.com/job/123

# Apply to a job (pauses before submit)
job-agent apply --job-id 42 --resume ai-engineer

# List jobs by status
job-agent jobs --status shortlisted
job-agent jobs --status applied

# Mark job as applied
job-agent mark-applied --job-id 42

# Export tracker
job-agent export --format csv

# View application status
job-agent status --job-id 42
```

## MVP Scope (Build First)

**In Scope:**
- ✅ SQLite database (jobs, applications, activity log)
- ✅ Profile config (profile.json, field_mappings.json)
- ✅ Job intake: `add-job <url>` → scrape title/company
- ✅ Browser autofill: `apply --job-id` → upload resume → pause before submit
- ✅ Resume file selection: `--resume ai-engineer`
- ✅ Form gap reporting: filled/skipped/failed
- ✅ Multi-page form handling: fill → Next → repeat
- ✅ Session timeout detection
- ✅ Persistent Chromium profile (stay logged in)
- ✅ CLI interface for job and application management

**Out of Scope (Future):**
- ❌ Job discovery automation (feeds, APIs, LinkedIn scraping)
- ❌ Intelligent job filtering/scoring
- ❌ Referral workflow (contact tracking, message templates)
- ❌ Cold DM generation for LinkedIn
- ❌ Scheduled execution (launchd cron jobs)
- ❌ Web dashboard (CLI only)
- ❌ Workday-specific adapters (relying on generic fallback)

## Implementation Milestones

### Phase 1: Foundation
- [ ] Project setup (Python 3.12, Playwright, SQLite, Pydantic)
- [ ] Database schema and operations (create/read/update jobs and applications)
- [ ] Profile config loading and validation
- [ ] CLI entry point (main.py)

### Phase 2: Job Intake
- [ ] Implement `add-job <url>` command
- [ ] Parse job title, company from URL (BeautifulSoup)
- [ ] Store in SQLite with deduplication
- [ ] List jobs by status (`jobs --status`)

### Phase 3: Browser & Autofill (Critical Path)
- [ ] Playwright setup: persistent Chromium profile, session management
- [ ] Resume file upload and form detection
- [ ] Field mapping and filling (profile fields → form inputs)
- [ ] Multi-page form navigation (click Next until Submit)
- [ ] Gap report generation (filled/skipped/failed)
- [ ] Pause before submit (user review)

### Phase 4: Application Tracking
- [ ] Implement `apply --job-id --resume` command
- [ ] Track application status transitions
- [ ] Mark applied, record resume used
- [ ] Export tracker (CSV)

### Phase 5: Testing & Refinement
- [ ] Unit tests for database, parsing, field mapping
- [ ] Integration test: add job → apply → verify DB state
- [ ] Manual testing on 5-10 real job applications
- [ ] Measure: autofill percentage and time saved per application

## Success Criteria for MVP

Before expanding to discovery/referrals/scheduling:

- [ ] Store and retrieve jobs reliably in SQLite
- [ ] Autofill 70%+ of standard fields (name, email, phone, education, titles)
- [ ] Handle multi-page forms correctly
- [ ] Detect and report form gaps accurately
- [ ] Require user approval before submitting
- [ ] Persist browser session across multiple applications per day
- [ ] Recover gracefully from session timeouts
- [ ] Complete 5-10 real applications with tool, measure time saved
- [ ] Export application history (CSV) for tracking

## Known Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Workday automation fails** | 30-40% of jobs unapproachable | Fallback to manual; test early if possible |
| **Form variability high** | 70% autofill target not achieved | Adjust expectations; build adapters as needed |
| **Session timeouts frequent** | Tool hangs or loses progress | Detect logout, pause, ask re-login |
| **Resume parsing inconsistent** | Fields don't populate as expected | Manual gap-fill workflow handles it |
| **Multi-page form logic breaks** | Submit on wrong page | Test on real Greenhouse/Lever forms early |

## Tech Decisions

- **Python 3.12+**: Mature, large ecosystem, great for automation
- **Playwright over Selenium**: Better locators, page context isolation, modern
- **SQLite over PostgreSQL**: Local file, no server dependency, sufficient for 1000+ records
- **Pydantic over JSON validation**: Type safety, better error messages
- **CLI over web UI**: Faster to build, easier to script, matches your workflow
- **Persistent Chromium profile**: Avoids re-login, huge time saver at 30 apps/day
- **Resume-first strategy**: Leverage ATS's own parsing (50-60% of sites), fallback to manual

## Not Building

- No AI/LLM for job matching (you're doing curation)
- No auto-submit (you review first)
- No auto-DM sending (user controls outreach)
- No backend server, API, or cloud infrastructure
- No Workday-specific logic (hoping generic works)
