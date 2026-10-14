"""Assisted form autofill with an explicit user-review step."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .config import load_field_mappings
from .models import CandidateProfile, FormGapReport


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"

# These keys are deliberately conservative: custom screening questions are not guessed.
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
    """Return the first visible resume file input when one is available."""
    inputs = page.locator('input[type="file"]')
    for index in range(inputs.count()):
        element = inputs.nth(index)
        try:
            accept = (element.get_attribute("accept") or "").lower()
            name = (element.get_attribute("name") or "").lower()
            element_id = (element.get_attribute("id") or "").lower()
            label = f"{accept} {name} {element_id}"
            if any(token in label for token in ("resume", "cv", "file", "pdf", "doc")):
                return element
        except Exception:
            continue
    # ATSs often use an unlabeled file input, so use the only file input as fallback.
    return inputs.nth(0) if inputs.count() == 1 else None


def upload_resume(page: Page, resume_path: str | Path) -> bool:
    """Upload a resume if a suitable file input exists."""
    path = Path(resume_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Resume file not found: {path}")
    element = detect_resume_upload(page)
    if element is None:
        return False
    element.set_input_files(str(path))
    try:
        page.wait_for_timeout(1_500)
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
            label_text = locator.page.locator(f'label[for="{element_id}"]')
            if label_text.count():
                text = label_text.first.inner_text().strip()
                if text:
                    return text
        name = locator.get_attribute("name")
        if name:
            return name.replace("_", " ").replace("-", " ").strip()
        placeholder = locator.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
    except Exception:
        pass
    return ""


def _find_value(label: str, profile: CandidateProfile, mappings: dict[str, list[str]]) -> tuple[str, str] | None:
    normalized = _normalize_label(label)
    data = profile.model_dump()
    aliases: dict[str, list[str]] = {}
    for key, values in mappings.items():
        aliases[key] = list(dict.fromkeys([key, *values]))

    for key, profile_key in PROFILE_FIELD_VALUES.items():
        candidates = aliases.get(key, [key])
        if any(alias and (normalized == alias or alias in normalized) for alias in candidates):
            value = data.get(profile_key)
            if isinstance(value, str) and value.strip():
                return profile_key, value.strip()

    # Education and experience are not sprayed across repeated fields automatically.
    return None


def fill_field(page: Page, field_label: str, value: str) -> bool:
    """Fill one field conservatively by accessible label, placeholder, or name."""
    candidate_locators = [
        page.get_by_label(field_label, exact=False),
        page.get_by_placeholder(field_label, exact=False),
        page.locator(f'input[name="{field_label}"], textarea[name="{field_label}"]'),
    ]
    for locator in candidate_locators:
        try:
            for index in range(locator.count()):
                element = locator.nth(index)
                if not element.is_visible() or not element.is_enabled():
                    continue
                tag_name = element.evaluate("(el) => el.tagName.toLowerCase()")
                input_type = (element.get_attribute("type") or "").lower()
                if tag_name == "textarea" or input_type in {"", "text", "email", "tel", "url", "search"}:
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
    """Fill visible, recognized text fields and report fields that need attention."""
    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    report = FormGapReport()
    seen_labels: set[str] = set()

    fields = page.locator("input:not([type]), input[type='text'], input[type='email'], "
                          "input[type='tel'], input[type='url'], input[type='search'], textarea")
    for index in range(fields.count()):
        element = fields.nth(index)
        try:
            if not element.is_visible() or not element.is_enabled():
                continue
            if element.get_attribute("readonly") is not None or element.get_attribute("disabled") is not None:
                continue
            label = _field_label(element)
            if not label:
                continue
            normalized = _normalize_label(label)
            if normalized in seen_labels:
                continue
            seen_labels.add(normalized)

            current_value = element.input_value()
            if current_value.strip():
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
            # Dynamic ATS forms can detach elements while being inspected.
            continue

    return report


def detect_next_button(page: Page):
    """Find a visible Next/Continue button without touching Submit."""
    for pattern in (re.compile(r"^(next|continue|save and continue)$", re.I),):
        candidates = [
            page.get_by_role("button", name=pattern),
            page.get_by_role("link", name=pattern),
            page.locator('input[type="button"], input[type="submit"]'),
        ]
        for locator in candidates:
            try:
                for index in range(locator.count()):
                    element = locator.nth(index)
                    if not element.is_visible() or not element.is_enabled():
                        continue
                    text = (element.inner_text() if element.evaluate("(el) => el.tagName") != "INPUT"
                            else element.get_attribute("value") or "").strip()
                    if pattern.match(text):
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
        buttons = page.locator('input[type="submit"]')
        for index in range(buttons.count()):
            element = buttons.nth(index)
            label = (element.get_attribute("value") or "").lower()
            if element.is_visible() and element.is_enabled() and "submit" in label:
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
    """Upload the selected resume, fill known fields, and stop for human review.

    It intentionally never clicks a Submit/Apply button. The user must inspect the
    actual form and manually submit it.
    """
    del job_id  # Kept in the interface for future per-job logging.
    if profile is None:
        from .config import load_profile
        profile = load_profile()
    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    base_dir = Path(resumes_dir) if resumes_dir else RESUMES_DIR
    resume_path = base_dir / resume_name
    if not resume_path.suffix:
        # Support "ai-engineer" resolving to ai-engineer.pdf or ai-engineer.docx.
        matches = [base_dir / f"{resume_name}{suffix}" for suffix in (".pdf", ".docx", ".doc")]
        resume_path = next((candidate for candidate in matches if candidate.is_file()), matches[0])

    report = FormGapReport()
    try:
        report.resume_uploaded = upload_resume(page, resume_path)
    except FileNotFoundError:
        report.skipped.append(f"Resume upload skipped: file not found ({resume_path})")

    for page_number in range(max_pages):
        page_report = fill_standard_fields(page, profile, mappings)
        report.filled.extend(page_report.filled)
        report.skipped.extend(page_report.skipped)
        report.failed.extend(page_report.failed)
        report.unknown.extend(page_report.unknown)

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

    # De-duplicate report entries while preserving order.
    report.filled = list(dict.fromkeys(report.filled))
    report.skipped = list(dict.fromkeys(report.skipped))
    report.failed = list(dict.fromkeys(report.failed))
    report.unknown = list(dict.fromkeys(report.unknown))
    return report


def wait_for_submit_button(page: Page) -> None:
    """Block on terminal input while the user reviews the form; do not submit."""
    print("\nReview the form in the browser and fill any unanswered required questions.")
    print("This tool will not click Submit or Apply for you.")
    input("Press Enter here when you have finished reviewing (the page stays open)...")
