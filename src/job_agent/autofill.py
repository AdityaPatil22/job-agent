"""ATS-agnostic assisted form autofill for Job-Agent.

Only recognized profile fields are filled. Final Submit/Apply actions are
never clicked automatically; the user reviews and submits the application.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .config import load_field_mappings, load_profile
from .models import CandidateProfile, FormGapReport


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"

# Profile field key -> one or more paths inside config/profile.json.
PROFILE_KEYS: dict[str, tuple[str, ...]] = {
    "full_name": ("name", "full_name"),
    "first_name": ("first_name",),
    "middle_name": ("middle_name",),
    "last_name": ("last_name",),
    "email": ("email",),
    "phone": ("phone", "phone_number", "mobile"),
    "country_code": ("country_code",),
    "linkedin": ("linkedin_url", "linkedin"),
    "github": ("github_url", "github"),
    "portfolio": ("portfolio_url", "portfolio", "website"),
    "address": ("address", "address_line_1"),
    "city": ("city",),
    "state": ("state", "province"),
    "zip": ("postal_code", "zip", "zip_code"),
    "country": ("country",),
    "current_company": (
        "current_company",
        "experience.0.company",
    ),
    "current_title": (
        "current_title",
        "experience.0.title",
    ),
    "employment_type": ("employment_type",),
    "years_experience": (
        "years_experience",
        "experience.0.duration_years",
    ),
    "education_school": (
        "education.school",
        "education.0.school",
    ),
    "education_degree": (
        "education.degree",
        "education.0.degree",
    ),
    "education_field": (
        "education.field",
        "education.0.field",
    ),
    "education_start_year": (
        "education.start_year",
        "education.0.start_year",
    ),
    "education_end_year": (
        "education.year",
        "education.0.year",
    ),
    "work_authorization": ("work_authorization",),
    "visa_sponsorship": (
        "visa_sponsorship",
        "requires_visa_sponsorship",
    ),
    "salary": ("expected_salary",),
    "notice_period": ("notice_period",),
    "relocation": ("relocation", "willing_to_relocate"),
    "referral_source": ("referral_source",),
}

RESUME_WORDS = ("resume", "résumé", "cv", "curriculum vitae")
UPLOAD_WORDS = (
    "upload",
    "attach",
    "browse",
    "choose",
    "select",
    "add file",
)

SUBMIT_PATTERN = re.compile(
    r"^(submit|submit application|apply|apply now|send application|"
    r"finish application)$",
    re.I,
)

NEXT_PATTERN = re.compile(
    r"^(next|continue|save and continue)$",
    re.I,
)


def _normalize(value: str) -> str:
    """Normalize labels and attributes while retaining letters and digits."""
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(
        r"[^\w]+",
        " ",
        value.lower(),
        flags=re.UNICODE,
    )
    return re.sub(r"\s+", " ", value).strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)

    # False and zero are valid answers and must not be discarded.
    return True


def _get_nested(data: dict[str, Any], path: str) -> Any:
    """Read dotted paths, including paths into list-backed education data."""
    value: Any = data

    for part in path.split("."):
        if isinstance(value, list):
            if part.isdigit():
                index = int(part)
                value = value[index] if index < len(value) else None
            else:
                value = value[0] if value else None

                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return None

        elif isinstance(value, dict):
            value = value.get(part)

        else:
            return None

        if value is None:
            return None

    return value


def _profile_data(profile: CandidateProfile) -> dict[str, Any]:
    """Dump all profile fields and derive common values where safe."""
    data = profile.model_dump(mode="python")

    # Support both full-name and split-name profile formats.
    name = str(
        data.get("name") or data.get("full_name") or ""
    ).strip()

    if not name:
        name = " ".join(
            str(data.get(key) or "").strip()
            for key in ("first_name", "middle_name", "last_name")
            if str(data.get(key) or "").strip()
        )

    if name:
        data["name"] = name
        parts = name.split()

        if not data.get("first_name") and parts:
            data["first_name"] = parts[0]

        if not data.get("last_name") and len(parts) > 1:
            data["last_name"] = parts[-1]

        if not data.get("middle_name") and len(parts) > 2:
            data["middle_name"] = " ".join(parts[1:-1])

    # Derive city/state/country from "City, State, Country".
    location = str(data.get("location") or "").strip()

    if location and "," in location:
        parts = [
            part.strip()
            for part in location.split(",")
            if part.strip()
        ]

        if len(parts) >= 2:
            if not data.get("city"):
                data["city"] = parts[0]

            if not data.get("state"):
                data["state"] = parts[1]

            if len(parts) >= 3 and not data.get("country"):
                data["country"] = parts[-1]

    # Support both postal_code and zip profile keys.
    if not data.get("postal_code") and data.get("zip"):
        data["postal_code"] = data["zip"]

    if not data.get("zip") and data.get("postal_code"):
        data["zip"] = data["postal_code"]

    # Support boolean profile keys from profile.example.json.
    if not _has_value(data.get("visa_sponsorship")):
        if data.get("requires_visa_sponsorship") is not None:
            data["visa_sponsorship"] = data[
                "requires_visa_sponsorship"
            ]

    if not _has_value(data.get("relocation")):
        if data.get("willing_to_relocate") is not None:
            data["relocation"] = data["willing_to_relocate"]

    # Fall back to the first experience entry for the current role.
    experiences = data.get("experience") or []

    if experiences and isinstance(experiences[0], dict):
        first_experience = experiences[0]

        if not data.get("current_company"):
            data["current_company"] = first_experience.get(
                "company", ""
            )

        if not data.get("current_title"):
            data["current_title"] = first_experience.get(
                "title", ""
            )

    return data


def _get_field_value(
    profile_data: dict[str, Any],
    field: str,
) -> Any:
    """Return the first non-empty configured profile value."""
    for path in PROFILE_KEYS.get(field, ()):
        value = _get_nested(profile_data, path)

        if _has_value(value):
            if isinstance(value, list) and path == "skills":
                return ", ".join(str(item) for item in value)

            return value

    return None


def _label_candidates(element: Any) -> list[str]:
    """Collect form labels, accessible names, and standard HTML attributes."""
    candidates: list[str] = []

    # Explicit labels, fieldset legends, and ARIA labels provide the best
    # description of a control.
    try:
        labels = element.evaluate(
            """el => {
                const texts = [];
                const add = node => {
                    const text = node &&
                        (node.innerText || node.textContent || '').trim();
                    if (text) texts.push(text);
                };

                if (el.labels) {
                    Array.from(el.labels).forEach(add);
                }

                add(el.closest('label'));

                const fieldset = el.closest('fieldset');
                if (fieldset) {
                    add(fieldset.querySelector('legend'));
                }

                const group = el.closest(
                    '[role="group"], [role="radiogroup"]'
                );
                if (group) add(group);

                for (const id of (
                    el.getAttribute('aria-labelledby') || ''
                ).split(/\\s+/)) {
                    if (id) add(document.getElementById(id));
                }

                return texts;
            }"""
        )

        if isinstance(labels, list):
            candidates.extend(
                _normalize(str(label))
                for label in labels
                if label
            )

    except Exception:
        pass

    for attribute in (
        "aria-label",
        "placeholder",
        "name",
        "id",
        "autocomplete",
        "title",
        "data-testid",
        "data-qa",
        "data-field",
    ):
        try:
            value = element.get_attribute(attribute)
        except Exception:
            value = None

        if value:
            candidates.append(_normalize(value))

    # Explicit label association as a fallback for older markup.
    try:
        element_id = element.get_attribute("id")
    except Exception:
        element_id = None

    if element_id:
        try:
            label = element.page.locator("label[for]").filter(
                has=element.page.locator(f'[id="{element_id}"]')
            )

            for index in range(label.count()):
                candidates.append(
                    _normalize(label.nth(index).inner_text())
                )

        except Exception:
            pass

        try:
            label = element.page.locator(
                f'label[for="{element_id}"]'
            )

            if label.count():
                candidates.append(
                    _normalize(label.first.inner_text())
                )

        except Exception:
            pass

    return list(dict.fromkeys(item for item in candidates if item))


def _get_aliases(
    key: str,
    mappings: dict[str, list[str]],
) -> set[str]:
    aliases = mappings.get(key, [])

    if isinstance(aliases, str):
        aliases = [aliases]

    return {
        _normalize(key),
        *(_normalize(alias) for alias in aliases),
    }


def _resolve_field(
    candidates: list[str],
    profile_data: dict[str, Any],
    mappings: dict[str, list[str]],
) -> tuple[str, Any] | None:
    """Resolve exact aliases first, then conservative partial matches."""
    field_aliases = {
        key: _get_aliases(key, mappings)
        for key in PROFILE_KEYS
    }

    # Prefer exact matches.
    for candidate in candidates:
        for key, aliases in field_aliases.items():
            if candidate not in aliases:
                continue

            value = _get_field_value(profile_data, key)

            if _has_value(value):
                return key, value

    # Allow a label to have common extra words around an alias.
    for candidate in candidates:
        for key, aliases in field_aliases.items():
            for alias in aliases:
                if not alias or candidate == alias:
                    continue

                if (
                    candidate.startswith(alias + " ")
                    or candidate.endswith(" " + alias)
                ):
                    value = _get_field_value(profile_data, key)

                    if _has_value(value):
                        return key, value

    return None


def _element_type(element: Any) -> tuple[str, str]:
    try:
        tag = element.evaluate("el => el.tagName.toLowerCase()")
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
                for attr in (
                    "id",
                    "name",
                    "accept",
                    "aria-label",
                    "title",
                    "data-testid",
                )
            )

            parent_text = ""

            try:
                parent = element.locator(
                    "xpath=ancestor::*[self::label or self::div "
                    "or self::section][1]"
                )

                if parent.count():
                    parent_text = parent.first.inner_text(timeout=500)

            except Exception:
                pass

            context = _normalize(attributes + " " + parent_text)
            score = 0

            if any(_normalize(word) in context for word in RESUME_WORDS):
                score += 10

            if any(
                _normalize(word) in context
                for word in UPLOAD_WORDS
            ):
                score += 3

            if "cover letter" in context and "resume" not in context:
                score -= 10

            ranked.append((score, element))

        except Exception:
            continue

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)

    if ranked[0][0] > 0:
        return ranked[0][1]

    return ranked[0][1] if len(ranked) == 1 else None


def upload_resume(
    page: Page,
    resume_path: str | Path,
) -> bool:
    """Upload a resume through a file input, including hidden ATS inputs."""
    path = Path(resume_path).expanduser().resolve()

    if not path.is_file():
        raise FileNotFoundError(f"Resume file not found: {path}")

    element = detect_resume_upload(page)

    if element is None:
        return False

    element.set_input_files(str(path))

    # Allow the upload handlers to start. autofill_form subsequently waits
    # for the form values to settle before filling the remaining blanks.
    try:
        page.wait_for_timeout(500)
    except Exception:
        pass

    return True


def _visible_form_snapshot(
    page: Page,
) -> tuple[tuple[str, str], ...]:
    """Capture visible field values to detect asynchronous resume parsing."""
    snapshot: list[tuple[str, str]] = []
    fields = page.locator("input, textarea, select")

    try:
        count = fields.count()
    except Exception:
        return ()

    for index in range(count):
        element = fields.nth(index)

        try:
            if not element.is_visible():
                continue

            tag, kind = _element_type(element)

            if kind in {
                "password",
                "file",
                "submit",
                "button",
                "reset",
            }:
                continue

            value = element.input_value()

            if kind in {"checkbox", "radio"}:
                value = (
                    "checked"
                    if element.is_checked()
                    else "unchecked"
                )

            identifier = (
                element.get_attribute("name")
                or element.get_attribute("id")
                or str(index)
            )

            snapshot.append(
                (f"{tag}:{kind}:{identifier}", value)
            )

        except Exception:
            continue

    return tuple(snapshot)


def _wait_for_resume_processing(
    page: Page,
    timeout_ms: int = 8_000,
) -> None:
    """Wait for asynchronous resume parsing to settle, up to a timeout."""
    started = time.monotonic()
    deadline = started + timeout_ms / 1000

    previous = _visible_form_snapshot(page)
    stable_since = time.monotonic()

    while time.monotonic() < deadline:
        try:
            page.wait_for_timeout(400)
        except Exception:
            return

        current = _visible_form_snapshot(page)
        now = time.monotonic()

        if current != previous:
            previous = current
            stable_since = now

        elif now - started >= 1.5 and now - stable_since >= 1.5:
            break


def _fill_text(element: Any, value: str) -> bool:
    tag, kind = _element_type(element)

    if tag not in {"input", "textarea"}:
        return False

    if kind in {
        "hidden",
        "file",
        "submit",
        "button",
        "reset",
        "checkbox",
        "radio",
        "password",
    }:
        return False

    if element.get_attribute("readonly") is not None:
        return False

    if not element.is_visible() or not element.is_enabled():
        return False

    element.fill(value)

    return True


def _answer_text(value: Any) -> str:
    """Convert explicit boolean profile answers to standard Yes/No text."""
    if isinstance(value, bool):
        return "Yes" if value else "No"

    return str(value).strip()


def _choice_matches(
    wanted: str,
    option_text: str,
) -> bool:
    wanted_normalized = _normalize(wanted)
    option_normalized = _normalize(option_text)

    if not wanted_normalized or not option_normalized:
        return False

    if wanted_normalized == option_normalized:
        return True

    # Support options such as "Yes, I am authorized".
    return (
        wanted_normalized in {"yes", "no"}
        and option_normalized.startswith(wanted_normalized + " ")
    )


def _select_option(element: Any, value: Any) -> bool:
    """Select a native option only when the match is unambiguous."""
    if not element.is_visible() or not element.is_enabled():
        return False

    wanted = _answer_text(value)
    options = element.locator("option")
    matches: list[int] = []

    for index in range(options.count()):
        option = options.nth(index)
        label = option.inner_text()
        option_value = option.get_attribute("value") or ""

        if (
            _choice_matches(wanted, label)
            or _choice_matches(wanted, option_value)
        ):
            matches.append(index)

    if len(matches) != 1:
        return False

    element.select_option(index=matches[0])

    return True


def _radio_group_key(element: Any, index: int) -> str:
    name = element.get_attribute("name") or ""

    if name:
        return f"name:{name}"

    try:
        group_label = element.evaluate(
            """el => {
                const fieldset = el.closest('fieldset');
                return fieldset
                    ? ((fieldset.querySelector('legend') || {})
                        .textContent || '').trim()
                    : '';
            }"""
        )

        if group_label:
            return f"legend:{_normalize(group_label)}"

    except Exception:
        pass

    return f"radio:{element.get_attribute('id') or index}"


def _radio_group_elements(
    element: Any,
    index: int,
) -> list[Any]:
    name = element.get_attribute("name") or ""
    radios = element.page.locator('input[type="radio"]')

    if name:
        found = []

        for position in range(radios.count()):
            option = radios.nth(position)

            if (option.get_attribute("name") or "") == name:
                found.append(option)

        return found

    try:
        fieldset = element.locator("xpath=ancestor::fieldset[1]")

        if fieldset.count():
            group = fieldset.locator('input[type="radio"]')

            return [
                group.nth(position)
                for position in range(group.count())
            ]

    except Exception:
        pass

    return [element]


def _radio_option_labels(element: Any) -> list[str]:
    values: list[str] = []

    try:
        labels = element.evaluate(
            """el => el.labels
                ? Array.from(el.labels)
                    .map(label =>
                        (label.innerText || label.textContent || '').trim()
                    )
                    .filter(Boolean)
                : []"""
        )

        if isinstance(labels, list):
            values.extend(str(label) for label in labels if label)

    except Exception:
        pass

    value = element.get_attribute("value") or ""

    if value:
        values.append(value)

    return values


def _radio_group_selected(options: list[Any]) -> bool:
    for option in options:
        try:
            if option.is_checked():
                return True
        except Exception:
            continue

    return False


def _select_radio_option(
    element: Any,
    value: Any,
    options: list[Any],
) -> bool:
    """Choose a radio option only when the profile answer matches one option."""
    del element

    wanted = _answer_text(value)
    matches: list[Any] = []

    for option in options:
        try:
            if not option.is_visible() or not option.is_enabled():
                continue

            labels = _radio_option_labels(option)

            if any(
                _choice_matches(wanted, label)
                for label in labels
            ):
                matches.append(option)

        except Exception:
            continue

    if len(matches) != 1:
        return False

    matches[0].check()

    return True


def _is_placeholder_select(element: Any) -> bool:
    try:
        selected = element.locator("option:checked")

        if not selected.count():
            return True

        label = _normalize(selected.first.inner_text())
        value = _normalize(
            selected.first.get_attribute("value") or ""
        )

        placeholders = {
            "",
            "select",
            "select one",
            "please select",
            "choose",
            "choose one",
            "please choose",
        }

        return label in placeholders or value in placeholders

    except Exception:
        return False


def _display_field(
    candidates: list[str],
    fallback: str,
) -> str:
    """Return a usable field label for the gap report."""
    for candidate in candidates:
        if candidate and not candidate.startswith(
            ("application", "input", "field")
        ):
            return candidate

    return fallback


def fill_standard_fields(
    page: Page,
    profile: CandidateProfile,
    field_mappings: dict[str, list[str]] | None = None,
) -> FormGapReport:
    """Fill recognized empty fields while preserving existing answers."""
    mappings = (
        field_mappings
        if field_mappings is not None
        else load_field_mappings()
    )

    profile_data = _profile_data(profile)
    report = FormGapReport()

    fields = page.locator("input, textarea, select")
    seen_radio_groups: set[str] = set()

    for index in range(fields.count()):
        element = fields.nth(index)

        try:
            if not element.is_visible() or not element.is_enabled():
                continue

            tag, kind = _element_type(element)

            if kind in {
                "hidden",
                "submit",
                "button",
                "reset",
                "file",
                "password",
            }:
                continue

            if element.get_attribute("readonly") is not None:
                continue

            candidates = _label_candidates(element)
            label = _display_field(
                candidates,
                f"Field #{index + 1}",
            )

            # Radio values exist even when their options are not selected.
            if kind == "radio":
                group_key = _radio_group_key(element, index)

                if group_key in seen_radio_groups:
                    continue

                seen_radio_groups.add(group_key)
                options = _radio_group_elements(element, index)

                if _radio_group_selected(options):
                    report.skipped.append(
                        f"{label} (already answered)"
                    )
                    continue

                match = _resolve_field(
                    candidates,
                    profile_data,
                    mappings,
                )

                if match is None:
                    report.unknown.append(label)
                    continue

                key, value = match

                if not _has_value(value):
                    report.skipped.append(
                        f"{label} (no profile value)"
                    )
                    continue

                if _select_radio_option(element, value, options):
                    report.filled.append(f"{label} <- {key}")
                else:
                    report.failed.append(
                        f"{label} (no unambiguous radio option "
                        "for profile answer)"
                    )

                continue

            if kind == "checkbox":
                # Consent/legal acknowledgements should not be checked
                # automatically. Only handle explicitly mapped booleans.
                match = _resolve_field(
                    candidates,
                    profile_data,
                    mappings,
                )

                if match is None:
                    report.unknown.append(label)
                    continue

                key, value = match

                if not isinstance(value, bool):
                    report.skipped.append(
                        f"{label} (checkbox answer is not "
                        "an explicit boolean)"
                    )
                    continue

                if value:
                    element.check()
                    report.filled.append(f"{label} <- {key}")
                else:
                    report.skipped.append(
                        f"{label} (left unchecked per profile)"
                    )

                continue

            current_value = ""

            try:
                current_value = element.input_value().strip()
            except Exception:
                pass

            if current_value and not (
                tag == "select" and _is_placeholder_select(element)
            ):
                report.skipped.append(
                    f"{label} (already filled)"
                )
                continue

            match = _resolve_field(
                candidates,
                profile_data,
                mappings,
            )

            if match is None:
                report.unknown.append(label)
                continue

            key, value = match

            if not _has_value(value):
                report.skipped.append(
                    f"{label} (no profile value)"
                )
                continue

            value_text = _answer_text(value)

            if tag == "select":
                success = _select_option(element, value)
            else:
                success = _fill_text(element, value_text)

            if success:
                report.filled.append(f"{label} <- {key}")
            else:
                report.failed.append(label)

        except Exception as exc:
            report.failed.append(
                f"Field #{index + 1} ({type(exc).__name__})"
            )

    return report


def _find_next_button(page: Page) -> Any | None:
    """Find Next/Continue only; never return Submit or Apply."""
    for role in ("button", "link"):
        locator = page.get_by_role(
            role,
            name=NEXT_PATTERN,
        )

        try:
            for index in range(locator.count()):
                element = locator.nth(index)

                if element.is_visible() and element.is_enabled():
                    return element

        except Exception:
            continue

    return None


def _has_submit_button(page: Page) -> bool:
    """Detect a final-submit control so the autofiller stops advancing."""
    try:
        buttons = page.get_by_role(
            "button",
            name=SUBMIT_PATTERN,
        )

        for index in range(buttons.count()):
            button = buttons.nth(index)

            if button.is_visible() and button.is_enabled():
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

    mappings = (
        field_mappings
        if field_mappings is not None
        else load_field_mappings()
    )

    base_dir = Path(resumes_dir) if resumes_dir else RESUMES_DIR
    resume_path = base_dir / resume_name

    if not resume_path.suffix:
        candidates = [
            base_dir / f"{resume_name}{suffix}"
            for suffix in (".pdf", ".docx", ".doc")
        ]

        resume_path = next(
            (
                candidate
                for candidate in candidates
                if candidate.is_file()
            ),
            candidates[0],
        )

    report = FormGapReport()

    if resume_path.is_file():
        try:
            report.resume_uploaded = upload_resume(
                page,
                resume_path,
            )

            if not report.resume_uploaded:
                report.skipped.append(
                    "No recognizable resume upload field found"
                )
            else:
                # Give Greenhouse time to finish asynchronous parsing
                # before filling any fields it left blank.
                _wait_for_resume_processing(page)

        except Exception as exc:
            report.failed.append(
                f"Resume upload failed: {exc}"
            )

    else:
        report.skipped.append(
            f"Resume file not found: {resume_path}"
        )

    for page_number in range(max(1, max_pages)):
        current = fill_standard_fields(
            page,
            profile,
            mappings,
        )

        report.filled.extend(current.filled)
        report.skipped.extend(current.skipped)
        report.failed.extend(current.failed)
        report.unknown.extend(current.unknown)

        # Never advance beyond a detected final submission page.
        if _has_submit_button(page):
            break

        next_button = _find_next_button(page)

        if next_button is None:
            break

        try:
            next_button.click()

            try:
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=5_000,
                )
            except PlaywrightTimeoutError:
                pass

            page.wait_for_timeout(500)

        except Exception as exc:
            report.failed.append(
                f"Could not navigate to next page: {exc}"
            )
            break

        if page_number == max_pages - 1:
            report.skipped.append(
                f"Stopped at safety limit of {max_pages} pages"
            )

    # Deduplicate field messages accumulated across multiple pages.
    report.filled = list(dict.fromkeys(report.filled))
    report.skipped = list(dict.fromkeys(report.skipped))
    report.failed = list(dict.fromkeys(report.failed))
    report.unknown = list(dict.fromkeys(report.unknown))

    return report


def wait_for_submit_button(page: Page) -> None:
    """Pause for manual review; Job-Agent never submits an application."""
    del page

    print(
        "\nReview the form in the browser and complete any unanswered questions."
    )
    print(
        "Job-Agent will not click Submit or Apply for you."
    )

    input(
        "Press Enter here after you have reviewed the application..."
    )