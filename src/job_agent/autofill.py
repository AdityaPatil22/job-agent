"""Assisted form autofill with an explicit user-review step."""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .config import load_field_mappings
from .models import CandidateProfile, FormGapReport


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"

PROFILE_FIELD_VALUES = {
    "name": "name",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin_url",
    "company": "current_company",
    "title": "current_title",
    "address": "address",
    "city": "city",
    "state": "state",
    "zip": "zip",
}


def detect_resume_upload(page: Page):
    """Return a resume-like file input, or the sole file input as a fallback."""
    inputs = page.locator('input[type="file"]')
    count = inputs.count()
    for index in range(count):
        element = inputs.nth(index)
        try:
            attrs = " ".join([
                element.get_attribute("accept") or "",
                element.get_attribute("name") or "",
                element.get_attribute("id") or "",
            ]).lower()
            if any(token in attrs for token in ("resume", "cv", "file", "pdf", "doc")):
                return element
        except Exception:
            continue
    return inputs.nth(0) if count == 1 else None


def upload_resume(page: Page, resume_path: str | Path) -> bool:
    """Upload a resume if the page exposes a file input."""
    path = Path(resume_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Resume file not found: {path}")
    element = detect_resume_upload(page)
    if element is None:
        return False
    element.set_input_files(str(path))
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass
    return True


def _normalize_label(value: str) -> str:
    value = re.sub(r"[*:]", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _field_label(locator) -> str:
    try:
        label = locator.get_attribute("aria-label")
        if label:
            return label.strip()
        element_id = locator.get_attribute("id")
        if element_id:
            label_locator = locator.page.locator(f'label[for="{element_id}"]')
            if label_locator.count():
                label = label_locator.first.inner_text().strip()
                if label:
                    return label
        placeholder = locator.get_attribute("placeholder")
        name = locator.get_attribute("name")
        if placeholder:
            return placeholder.strip()
        if name:
            return name.replace("_", " ").replace("-", " ").strip()
    except Exception:
        pass
    return ""


def _find_value(label: str, profile: CandidateProfile, mappings: dict[str, list[str]]) -> tuple[str, str] | None:
    normalized = _normalize_label(label)
    profile_data = profile.model_dump()
    for field_key, profile_key in PROFILE_FIELD_VALUES.items():
        aliases = mappings.get(field_key, [])
        candidates = list(dict.fromkeys([field_key, *aliases]))
        if any(alias and (normalized == alias or alias in normalized) for alias in candidates):
            value = profile_data.get(profile_key)
            if isinstance(value, str) and value.strip():
                return profile_key, value.strip()
    return None


def fill_field(page: Page, field_label: str, value: str) -> bool:
    """Fill a single matching text field using accessible locators."""
    candidates = [
        page.get_by_label(field_label, exact=False),
        page.get_by_placeholder(field_label, exact=False),
    ]
    for locator in candidates:
        try:
            for index in range(locator.count()):
                element = locator.nth(index)
                if not element.is_visible() or not element.is_enabled():
                    continue
                tag = element.evaluate("(el) => el.tagName.toLowerCase()")
                kind = (element.get_attribute("type") or "").lower()
                if tag == "textarea" or kind in {"", "text", "email", "tel", "url", "search"}:
                    element.fill(value)
                    return True
        except Exception:
            continue
    return False


def fill_standard_fields(
    page: Page,
    profile: CandidateProfile,
    field_mappings: dict[str, list[str]] | None = None,
) -> FormGapReport:
    """Fill only empty, visible, recognized text fields."""
    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    report = FormGapReport()
    seen_labels: set[str] = set()
    fields = page.locator(
        "input:not([type]), input[type='text'], input[type='email'], input[type='tel'], "
        "input[type='url'], input[type='search'], textarea"
    )
    for index in range(fields.count()):
        element = fields.nth(index)
        try:
            if not element.is_visible() or not element.is_enabled():
                continue
            if element.get_attribute("readonly") is not None:
                continue
            label = _field_label(element)
            if not label:
                report.unknown.append(f"Unlabeled field #{index + 1}")
                continue
            normalized = _normalize_label(label)
            if normalized in seen_labels:
                continue
            seen_labels.add(normalized)
            if element.input_value().strip():
                report.skipped.append(f"{label} (already filled)")
                continue
            match = _find_value(label, profile, mappings)
            if match is None:
                report.unknown.append(label)
                continue
            profile_key, value = match
            try:
                element.fill(value)
                report.filled.append(f"{label} ← {profile_key}")
            except Exception:
                report.failed.append(label)
        except Exception:
            # ATS pages often update DOM nodes asynchronously.
            continue
    return report


def detect_next_button(page: Page):
    """Find a visible Next/Continue action; never select a Submit control."""
    pattern = re.compile(r"^(next|continue|save and continue)$", re.I)
    for locator in (
        page.get_by_role("button", name=pattern),
        page.get_by_role("link", name=pattern),
        page.locator('input[type="button"]'),
    ):
        try:
            for index in range(locator.count()):
                element = locator.nth(index)
                if not element.is_visible() or not element.is_enabled():
                    continue
                tag = element.evaluate("(el) => el.tagName.toLowerCase()")
                label = (element.inner_text() if tag != "input" else element.get_attribute("value") or "").strip()
                if pattern.match(label):
                    return element
        except Exception:
            continue
    return None


def _submit_visible(page: Page) -> bool:
    try:
        buttons = page.get_by_role("button", name=re.compile(r"submit|send application|apply now", re.I))
        for index in range(buttons.count()):
            if buttons.nth(index).is_visible() and buttons.nth(index).is_enabled():
                return True
    except Exception:
        pass
    try:
        for index in range(page.locator('input[type="submit"]').count()):
            element = page.locator('input[type="submit"]').nth(index)
            label = (element.get_attribute("value") or "").lower()
            if element.is_visible() and element.is_enabled() and any(
                word in label for word in ("submit", "apply")
            ):
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
    """Upload a resume, fill known fields, then leave the final submission to the user."""
    del job_id  # Reserved for future per-job activity logging.
    if profile is None:
        from .config import load_profile
        profile = load_profile()
    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    base_dir = Path(resumes_dir) if resumes_dir else RESUMES_DIR
    resume_path = base_dir / resume_name
    if not resume_path.suffix:
        choices = [base_dir / f"{resume_name}{suffix}" for suffix in (".pdf", ".docx", ".doc")]
        resume_path = next((candidate for candidate in choices if candidate.is_file()), choices[0])

    report = FormGapReport()
    if resume_path.is_file():
        report.resume_uploaded = upload_resume(page, resume_path)
        if not report.resume_uploaded:
            report.skipped.append("No recognizable resume upload field found")
    else:
        report.skipped.append(f"Resume file not found: {resume_path}")

    for page_number in range(max(1, max_pages)):
        one_page = fill_standard_fields(page, profile, mappings)
        report.filled.extend(one_page.filled)
        report.skipped.extend(one_page.skipped)
        report.failed.extend(one_page.failed)
        report.unknown.extend(one_page.unknown)

        if _submit_visible(page):
            break
        next_button = detect_next_button(page)
        if next_button is None:
            break
        try:
            next_button.click()
            page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        except Exception as exc:
            report.failed.append(f"Could not navigate to the next page: {exc}")
            break
        if page_number == max_pages - 1:
            report.skipped.append(f"Stopped after safety limit of {max_pages} form pages")

    report.filled = list(dict.fromkeys(report.filled))
    report.skipped = list(dict.fromkeys(report.skipped))
    report.failed = list(dict.fromkeys(report.failed))
    report.unknown = list(dict.fromkeys(report.unknown))
    return report


def wait_for_submit_button(page: Page) -> None:
    """Pause in the terminal so the user can review the form; never submit automatically."""
    print("\nReview the form in the browser and answer any required questions.")
    print("Job-Agent will not click Submit or Apply for you.")
    input("Press Enter here when you have finished reviewing (the page stays open)...")
