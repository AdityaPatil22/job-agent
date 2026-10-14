"""ATS-agnostic assisted form autofill for Job-Agent.

The tool fills only fields for which it has a recognized, non-empty answer.
It does not click Submit or Apply.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .config import load_field_mappings, load_profile
from .models import CandidateProfile, FormGapReport


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"

# Mapping key -> profile field. Aliases are loaded from field_mappings.json.
PROFILE_KEYS: dict[str, tuple[str, ...]] = {
    "full_name": ("name",),
    "first_name": ("first_name",),
    "middle_name": ("middle_name",),
    "last_name": ("last_name",),
    "email": ("email",),
    "phone": ("phone",),
    "country_code": ("country_code",),
    "linkedin": ("linkedin_url",),
    "github": ("github_url",),
    "portfolio": ("portfolio_url",),
    "address": ("address",),
    "city": ("city",),
    "state": ("state",),
    "zip": ("zip",),
    "country": ("country",),
    "current_company": ("current_company",),
    "current_title": ("current_title",),
    "employment_type": ("employment_type",),
    "years_experience": ("years_experience",),
    "education_school": ("education.school",),
    "education_degree": ("education.degree",),
    "education_field": ("education.field",),
    "education_start_year": ("education.start_year",),
    "education_end_year": ("education.year",),
    "work_authorization": ("work_authorization",),
    "visa_sponsorship": ("visa_sponsorship",),
    "salary": ("expected_salary",),
    "notice_period": ("notice_period",),
    "relocation": ("relocation",),
    "referral_source": ("referral_source",),
}

RESUME_WORDS = ("resume", "résumé", "cv", "curriculum vitae")
UPLOAD_WORDS = ("upload", "attach", "browse", "choose", "select", "add file")
SUBMIT_PATTERN = re.compile(
    r"^(submit|submit application|apply|apply now|send application|finish application)$",
    re.I,
)
NEXT_PATTERN = re.compile(r"^(next|continue|save and continue)$", re.I)

PROFILE_PATHS = {
    "full_name": "name",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin_url",
    "current_company": "current_company",
    "current_title": "current_title",
    "location": "location",
    "education_school": "education.0.school",
    "education_degree": "education.0.degree",
    "education_field": "education.0.field",
    "education_year": "education.0.year",
    "experience_title": "experience.0.title",
    "experience_company": "experience.0.company",
    "experience_duration_years": "experience.0.duration_years",
    "experience_description": "experience.0.description",
    "skills": "skills",
}


def get_profile_value(profile: dict, path: str):
    """Read a nested value from a dictionary using a dot-separated path."""
    value = profile

    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            value = value[index] if index < len(value) else None
        else:
            return None

        if value is None:
            return None

    # Convert skills to text for ordinary text inputs.
    if isinstance(value, list) and path == "skills":
        return ", ".join(str(item) for item in value)

    return value


def get_candidate_field(profile: dict, field: str):
    """Resolve common ATS fields against the candidate profile."""
    if field == "first_name":
        if profile.get("first_name"):
            return profile["first_name"]
        name = str(profile.get("name", "")).strip()
        return name.split(maxsplit=1)[0] if name else None

    if field == "last_name":
        if profile.get("last_name"):
            return profile["last_name"]
        name = str(profile.get("name", "")).strip()
        parts = name.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else None

    if field in {"city", "state"}:
        location = str(profile.get("location", "")).strip()
        parts = [part.strip() for part in location.split(",", maxsplit=1)]

        if field == "city":
            return parts[0] if parts and parts[0] else None
        return parts[1] if len(parts) > 1 else None

    path = PROFILE_PATHS.get(field)
    return get_profile_value(profile, path) if path else None


def _normalize(value: str) -> str:
    """Normalize visible labels and HTML attributes for comparison."""
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    value = re.sub(r"[*:]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _profile_data(profile: CandidateProfile) -> dict[str, Any]:
    """Return profile data, deriving split names without overwriting supplied values."""
    data = profile.model_dump()

    name = str(data.get("name") or "").strip()
    parts = name.split()
    if parts:
        data.setdefault("first_name", parts[0])
        data.setdefault("middle_name", " ".join(parts[1:-1]) if len(parts) > 2 else "")
        data.setdefault("last_name", parts[-1] if len(parts) > 1 else "")

    return data


def _get_nested(data: dict[str, Any], path: str) -> Any:
    """Read a dotted path from profile data, including the first education record."""
    value: Any = data
    for part in path.split("."):
        if isinstance(value, list):
            value = value[0] if value else None
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _label_candidates(element: Any) -> list[str]:
    """Collect identifying text from labels and common HTML attributes."""
    candidates: list[str] = []

    for attribute in ("aria-label", "placeholder", "name", "id", "autocomplete", "title"):
        value = element.get_attribute(attribute)
        if value:
            candidates.append(_normalize(value))

    element_id = element.get_attribute("id")
    if element_id:
        try:
            label = element.page.locator("label[for]").filter(
                has=element.page.locator(f"#{element_id}")
            )
            if label.count():
                candidates.extend(
                    _normalize(label.nth(i).inner_text())
                    for i in range(label.count())
                )
        except Exception:
            pass

        try:
            explicit_label = element.page.locator(
                f'label[for="{element_id}"]'
            )
            if explicit_label.count():
                candidates.append(_normalize(explicit_label.first.inner_text()))
        except Exception:
            pass

    try:
        parent_label = element.locator("xpath=ancestor::label[1]")
        if parent_label.count():
            candidates.append(_normalize(parent_label.first.inner_text()))
    except Exception:
        pass

    try:
        labelled_by = element.get_attribute("aria-labelledby")
        if labelled_by:
            for label_id in labelled_by.split():
                label = element.page.locator(f"#{label_id}")
                if label.count():
                    candidates.append(_normalize(label.first.inner_text()))
    except Exception:
        pass

    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _get_aliases(key: str, mappings: dict[str, list[str]]) -> set[str]:
    aliases = mappings.get(key, [])
    if isinstance(aliases, str):
        aliases = [aliases]
    return {_normalize(key), *(_normalize(alias) for alias in aliases)}


def _resolve_field(
    candidates: list[str],
    profile_data: dict[str, Any],
    mappings: dict[str, list[str]],
) -> tuple[str, Any] | None:
    """Resolve exact aliases first, then conservative token matches."""

    field_aliases = {
        key: _get_aliases(key, mappings)
        for key in PROFILE_KEYS
    }

    # Pass 1: exact matches always take priority.
    for candidate in candidates:
        for key, aliases in field_aliases.items():
            if candidate not in aliases:
                continue

            for path in PROFILE_KEYS[key]:
                value = _get_nested(profile_data, path)
                if value is not None and str(value).strip():
                    return key, value

    # Pass 2: conservative partial matching.
    for candidate in candidates:
        for key, aliases in field_aliases.items():
            for alias in aliases:
                if not alias:
                    continue

                if candidate.startswith(alias + " ") or candidate.endswith(" " + alias):
                    for path in PROFILE_KEYS[key]:
                        value = _get_nested(profile_data, path)
                        if value is not None and str(value).strip():
                            return key, value

    return None


def _element_type(element: Any) -> tuple[str, str]:
    try:
        tag = element.evaluate("(el) => el.tagName.toLowerCase()")
    except Exception:
        tag = ""
    kind = (element.get_attribute("type") or "").lower()
    return tag, kind


def detect_resume_upload(page: Page) -> Any | None:
    """Find a file input likely to accept a resume or CV."""
    inputs = page.locator('input[type="file"]')
    ranked: list[tuple[int, Any]] = []

    for index in range(inputs.count()):
        element = inputs.nth(index)
        try:
            attributes = " ".join(
                element.get_attribute(attr) or ""
                for attr in ("id", "name", "accept", "aria-label", "title")
            )
            parent_text = ""
            try:
                parent = element.locator("xpath=ancestor::*[self::label or self::div or self::section][1]")
                if parent.count():
                    parent_text = parent.first.inner_text(timeout=500)
            except Exception:
                pass

            context = _normalize(attributes + " " + parent_text)
            score = 0
            if any(_normalize(word) in context for word in RESUME_WORDS):
                score += 10
            if any(word in context for word in ("attach", "upload", "browse", "choose file")):
                score += 3
            if "cover letter" in context and "resume" not in context:
                score -= 10
            ranked.append((score, element))
        except Exception:
            continue

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1] if ranked[0][0] > 0 else (
        ranked[0][1] if len(ranked) == 1 else None
    )


def upload_resume(page: Page, resume_path: str | Path) -> bool:
    """Upload a resume through a file input, including hidden ATS inputs."""
    path = Path(resume_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Resume file not found: {path}")

    element = detect_resume_upload(page)
    if element is None:
        return False

    # Playwright supports set_input_files on hidden inputs.
    element.set_input_files(str(path))
    try:
        page.wait_for_timeout(750)
    except Exception:
        pass
    return True


def _fill_text(element: Any, value: str) -> bool:
    tag, kind = _element_type(element)
    if tag not in {"input", "textarea"}:
        return False
    if kind in {"hidden", "file", "submit", "button", "reset", "checkbox", "radio"}:
        return False
    if element.get_attribute("readonly") is not None:
        return False
    if not element.is_visible() or not element.is_enabled():
        return False
    element.fill(value)
    return True


def _select_option(element: Any, value: Any) -> bool:
    """Select a matching option by label or value without guessing."""
    if not element.is_visible() or not element.is_enabled():
        return False
    wanted = _normalize(str(value))
    options = element.locator("option")
    for index in range(options.count()):
        option = options.nth(index)
        label = _normalize(option.inner_text())
        option_value = _normalize(option.get_attribute("value") or "")
        if wanted and (wanted == label or wanted == option_value):
            element.select_option(index=index)
            return True
    return False


def fill_standard_fields(
    page: Page,
    profile: CandidateProfile,
    field_mappings: dict[str, list[str]] | None = None,
) -> FormGapReport:
    """Fill recognized fields while preserving values the user already entered."""
    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    profile_data = _profile_data(profile)
    report = FormGapReport()

    fields = page.locator(
        "input, textarea, select"
    )

    for index in range(fields.count()):
        element = fields.nth(index)
        try:
            if not element.is_visible() or not element.is_enabled():
                continue

            tag, kind = _element_type(element)
            if kind in {"hidden", "submit", "button", "reset", "file", "password"}:
                continue
            if kind in {"checkbox", "radio"}:
                continue
            if element.get_attribute("readonly") is not None:
                continue

            candidates = _label_candidates(element)
            if not candidates:
                report.unknown.append(f"Unlabeled field #{index + 1}")
                continue

            current_value = ""
            try:
                current_value = element.input_value().strip()
            except Exception:
                pass

            if current_value:
                report.skipped.append(f"{candidates[0]} (already filled)")
                continue

            match = _resolve_field(candidates, profile_data, mappings)
            if match is None:
                report.unknown.append(candidates[0])
                continue

            key, value = match
            value_text = str(value).strip()
            if not value_text:
                report.skipped.append(f"{candidates[0]} (no profile value)")
                continue

            if tag == "select":
                success = _select_option(element, value_text)
            else:
                success = _fill_text(element, value_text)

            if success:
                report.filled.append(f"{candidates[0]} ← {key}")
            else:
                report.failed.append(candidates[0])

        except Exception:
            report.failed.append(f"Field #{index + 1}")

    return report


def _find_next_button(page: Page) -> Any | None:
    """Find Next/Continue only; never return Submit or Apply."""
    for role in ("button", "link"):
        locator = page.get_by_role(role, name=NEXT_PATTERN)
        try:
            for index in range(locator.count()):
                element = locator.nth(index)
                if element.is_visible() and element.is_enabled():
                    return element
        except Exception:
            continue
    return None


def _has_submit_button(page: Page) -> bool:
    """Detect a final-submit control so we stop advancing the form."""
    try:
        buttons = page.get_by_role("button", name=SUBMIT_PATTERN)
        for index in range(buttons.count()):
            if buttons.nth(index).is_visible() and buttons.nth(index).is_enabled():
                return True
    except Exception:
        pass
    return False


def autofill_form(
    page: Page,
    job_id: int,
    resume_name: str,
    *,
    profile: CandidateProfile | None = None,
    resumes_dir: str | Path | None = None,
    field_mappings: dict[str, list[str]] | None = None,
    max_pages: int = 8,
) -> FormGapReport:
    """Upload a resume and fill recognized fields across a multi-page form."""
    del job_id  # Reserved for future activity logging.

    if profile is None:
        profile = load_profile()

    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    base_dir = Path(resumes_dir) if resumes_dir else RESUMES_DIR
    resume_path = base_dir / resume_name

    if not resume_path.suffix:
        candidates = [
            base_dir / f"{resume_name}{suffix}"
            for suffix in (".pdf", ".docx", ".doc")
        ]
        resume_path = next(
            (candidate for candidate in candidates if candidate.is_file()),
            candidates[0],
        )

    report = FormGapReport()

    if resume_path.is_file():
        try:
            report.resume_uploaded = upload_resume(page, resume_path)
            if not report.resume_uploaded:
                report.skipped.append("No recognizable resume upload field found")
        except Exception as exc:
            report.failed.append(f"Resume upload failed: {exc}")
    else:
        report.skipped.append(f"Resume file not found: {resume_path}")

    for page_number in range(max(1, max_pages)):
        current = fill_standard_fields(page, profile, mappings)
        report.filled.extend(current.filled)
        report.skipped.extend(current.skipped)
        report.failed.extend(current.failed)
        report.unknown.extend(current.unknown)

        if _has_submit_button(page):
            break

        next_button = _find_next_button(page)
        if next_button is None:
            break

        try:
            next_button.click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(500)
        except Exception as exc:
            report.failed.append(f"Could not navigate to next page: {exc}")
            break

        if page_number == max_pages - 1:
            report.skipped.append(f"Stopped at safety limit of {max_pages} pages")

    report.filled = list(dict.fromkeys(report.filled))
    report.skipped = list(dict.fromkeys(report.skipped))
    report.failed = list(dict.fromkeys(report.failed))
    report.unknown = list(dict.fromkeys(report.unknown))
    return report


def wait_for_submit_button(page: Page) -> None:
    """Pause for manual review. Job-Agent never submits an application."""
    print("\nReview the form in the browser and complete any unanswered questions.")
    print("Job-Agent will not click Submit or Apply for you.")
    input("Press Enter here after you have reviewed the application...")
