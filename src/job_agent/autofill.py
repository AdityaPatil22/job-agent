"""ATS-agnostic assisted form autofill for Job-Agent.

The autofiller only fills fields that can be mapped confidently to a known
candidate-profile value. It supports native controls and common accessible
custom comboboxes. It never clicks Submit or Apply; the user reviews and
submits the application manually.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeoutError

from .config import load_field_mappings, load_profile
from .models import CandidateProfile, FormGapReport

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"

# Canonical form field -> possible paths inside profile.json.
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
    "current_company": ("current_company", "experience.0.company"),
    "current_title": ("current_title", "experience.0.title"),
    "employment_type": ("employment_type",),
    "years_experience": (
        "years_experience",
        "experience.0.duration_years",
    ),
    "education_school": ("education.school", "education.0.school"),
    "education_degree": ("education.degree", "education.0.degree"),
    "education_field": ("education.field", "education.0.field"),
    "education_start_year": (
        "education.start_year",
        "education.0.start_year",
    ),
    "education_end_year": ("education.year", "education.0.year"),
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
UPLOAD_WORDS = ("upload", "attach", "browse", "choose", "select", "add file")

SUBMIT_PATTERN = re.compile(
    r"^(submit|submit application|apply|apply now|send application|finish application)$",
    re.I,
)
NEXT_PATTERN = re.compile(r"\b(next|continue|proceed|save and continue|next step)\b", re.I)

PLACEHOLDER_VALUES = {
    "",
    "select",
    "select one",
    "select an option",
    "select option",
    "please select",
    "please select one",
    "choose",
    "choose one",
    "choose an option",
    "please choose",
    "please choose one",
    "select country",
    "select state",
    "select city",
    "select employment type",
}

# Avoid mapping historical experience questions to current-role profile fields.
HISTORICAL_TERMS = {"previous", "previously", "former", "prior", "past"}


def _normalize(value: Any) -> str:
    """Normalize labels and attributes for case/punctuation-insensitive matching."""
    value = str(value or "").replace("_", " ").replace("-", " ")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"[^\w]+", " ", value.lower(), flags=re.UNICODE)
    value = re.sub(r"\blinked\s+in\b", "linkedin", value)
    value = re.sub(r"\bgit\s+hub\b", "github", value)
    return re.sub(r"\s+", " ", value).strip()


def _has_value(value: Any) -> bool:
    """Return false for missing values and common untouched example-profile placeholders."""
    if value is None:
        return False
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return False
        lowered = cleaned.lower()
        placeholders = {
            "null", "none", "n/a", "na", "not provided", "not specified",
            "your full name", "your first name", "your last name",
            "your current company", "your job title", "company name",
            "school name", "university name", "yourusername",
        }
        if lowered in placeholders:
            return False
        if lowered.startswith(("your ", "replace with ", "insert your ", "enter your ", "sample ")):
            return False
        if "example.com" in lowered or "xxxxxxxx" in lowered:
            return False
        return True
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    # False and zero are meaningful configured answers.
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
    """Dump profile data and safely derive common values where possible."""
    data = profile.model_dump(mode="python")

    name = str(data.get("name") or data.get("full_name") or "").strip()
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

    location = str(data.get("location") or "").strip()
    if location and "," in location:
        parts = [part.strip() for part in location.split(",") if part.strip()]
        if len(parts) >= 2:
            if not data.get("city"):
                data["city"] = parts[0]
            if not data.get("state"):
                data["state"] = parts[1]
            if len(parts) >= 3 and not data.get("country"):
                data["country"] = parts[-1]

    if not data.get("postal_code") and data.get("zip"):
        data["postal_code"] = data["zip"]
    if not data.get("zip") and data.get("postal_code"):
        data["zip"] = data["postal_code"]

    if not _has_value(data.get("visa_sponsorship")):
        if data.get("requires_visa_sponsorship") is not None:
            data["visa_sponsorship"] = data["requires_visa_sponsorship"]
    if not _has_value(data.get("relocation")):
        if data.get("willing_to_relocate") is not None:
            data["relocation"] = data["willing_to_relocate"]

    experiences = data.get("experience") or []
    if experiences and isinstance(experiences[0], dict):
        first_experience = experiences[0]
        if not data.get("current_company"):
            data["current_company"] = first_experience.get("company", "")
        if not data.get("current_title"):
            data["current_title"] = first_experience.get("title", "")

    return data


def _get_field_value(profile_data: dict[str, Any], field: str) -> Any:
    """Return the first configured profile value that is not empty."""
    for path in PROFILE_KEYS.get(field, ()):
        value = _get_nested(profile_data, path)
        if _has_value(value):
            if isinstance(value, list) and path == "skills":
                return ", ".join(str(item) for item in value)
            return value
    return None


def _iter_search_contexts(target: Page | Frame) -> list[Page | Frame]:
    """Return the main document and any embedded application frames."""
    try:
        contexts = list(target.frames)  # Page exposes frames; Frame does not.
        if contexts:
            return contexts
    except Exception:
        pass
    return [target]


def _label_candidates(element: Any) -> list[str]:
    """Collect accessible labels and identifying attributes for a control."""
    candidates: list[str] = []
    try:
        labels = element.evaluate(
            """el => {
                const texts = [];
                const add = node => {
                    const text = node &&
                        (node.innerText || node.textContent || '').trim();
                    if (text) texts.push(text);
                };
                if (el.labels) Array.from(el.labels).forEach(add);
                add(el.closest('label'));
                const fieldset = el.closest('fieldset');
                if (fieldset) add(fieldset.querySelector('legend'));
                const group = el.closest('[role="group"], [role="radiogroup"]');
                if (group) add(group);
                for (const id of (el.getAttribute('aria-labelledby') || '').split(/\\s+/)) {
                    if (id) add(document.getElementById(id));
                }
                const wrapper = el.closest('[data-field], [data-testid*="field"], .form-field, .field');
                if (wrapper) {
                    wrapper.querySelectorAll('label, legend, [data-testid*="label"]').forEach(add);
                }
                return texts;
            }"""
        )
        if isinstance(labels, list):
            candidates.extend(_normalize(label) for label in labels if label)
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

    # For custom widgets, the accessible role is useful only as a fallback;
    # it does not by itself identify which profile field the control represents.
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _get_aliases(key: str, mappings: dict[str, list[str]]) -> set[str]:
    aliases = mappings.get(key, [])
    if isinstance(aliases, str):
        aliases = [aliases]
    result = {_normalize(key)}
    result.update(_normalize(alias) for alias in aliases if _normalize(alias))
    return result


def _field_context_is_safe(key: str, candidate: str) -> bool:
    """Reject common labels that would map a historical/other field incorrectly."""
    words = set(candidate.split())
    if key in {"current_company", "current_title"} and words & HISTORICAL_TERMS:
        # "most recent employer" is a common way of asking about the current role.
        if "most recent" not in candidate:
            return False
    if key == "full_name" and words & {"first", "given", "last", "surname", "family", "middle"}:
        return False
    if key == "first_name" and words & {"last", "surname", "family", "middle"}:
        return False
    if key == "last_name" and words & {"first", "given", "middle"}:
        return False
    if key == "education_start_year" and words & {"graduation", "graduated", "completion", "completed", "end"}:
        return False
    if key == "education_end_year" and words & {"start", "starting", "enrollment", "enrolled"}:
        return False
    return True


def _alias_score(candidate: str, alias: str, key: str) -> int:
    """Score exact and whole-phrase matches; zero means no safe match."""
    if not candidate or not alias or not _field_context_is_safe(key, candidate):
        return 0
    if candidate == alias:
        return 10_000 + len(alias) * 10
    padded_candidate = f" {candidate} "
    padded_alias = f" {alias} "
    if padded_alias in padded_candidate:
        # Prefer longer/more-specific aliases. Mildly penalize long surrounding text.
        return 1_000 + len(alias) * 10 - max(0, len(candidate) - len(alias))
    return 0


def _resolve_field(
    candidates: list[str],
    profile_data: dict[str, Any],
    mappings: dict[str, list[str]],
) -> tuple[str, Any] | None:
    """Resolve the strongest safe alias match, refusing ties between fields."""
    scored: list[tuple[int, str, Any]] = []
    for key in PROFILE_KEYS:
        value = _get_field_value(profile_data, key)
        if not _has_value(value):
            continue
        aliases = _get_aliases(key, mappings)
        best_for_key = 0
        for candidate in candidates:
            for alias in aliases:
                best_for_key = max(best_for_key, _alias_score(candidate, alias, key))
        if best_for_key:
            scored.append((best_for_key, key, value))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score = scored[0][0]
    winners = [item for item in scored if item[0] == best_score]
    winner_keys = {item[1] for item in winners}
    if len(winner_keys) != 1:
        return None
    _, key, value = winners[0]
    return key, value


def _element_type(element: Any) -> tuple[str, str]:
    try:
        tag = element.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        tag = ""
    try:
        kind = (element.get_attribute("type") or "").lower()
    except Exception:
        kind = ""
    return tag, kind


def _element_role(element: Any) -> str:
    try:
        return (element.get_attribute("role") or "").strip().lower()
    except Exception:
        return ""


def _is_custom_combobox(element: Any, tag: str) -> bool:
    if tag == "select":
        return False
    try:
        role = (element.get_attribute("role") or "").strip().lower()
        popup = (element.get_attribute("aria-haspopup") or "").strip().lower()
        return role == "combobox" or popup == "listbox"
    except Exception:
        return False


def detect_resume_upload(page: Page | Frame) -> Any | None:
    """Find a likely resume input across the main page and embedded frames."""
    ranked: list[tuple[int, int, Any]] = []
    order = 0
    for context in _iter_search_contexts(page):
        try:
            inputs = context.locator('input[type="file"]')
            count = inputs.count()
        except Exception:
            continue
        for index in range(count):
            element = inputs.nth(index)
            try:
                attributes = " ".join(
                    element.get_attribute(attr) or ""
                    for attr in (
                        "id", "name", "accept", "aria-label", "title",
                        "data-testid", "data-qa", "data-field",
                    )
                )
                # Associated label and surrounding field copy catch many ATS
                # implementations whose file input itself has no useful name.
                label_text = " ".join(_label_candidates(element))
                parent_text = ""
                try:
                    parent = element.locator(
                        "xpath=ancestor::*[self::label or self::div or self::section][1]"
                    )
                    if parent.count():
                        parent_text = parent.first.inner_text(timeout=500)
                except Exception:
                    pass
                context_text = _normalize(attributes + " " + label_text + " " + parent_text)
                score = 0
                if any(_normalize(word) in context_text for word in RESUME_WORDS):
                    score += 10
                if any(_normalize(word) in context_text for word in UPLOAD_WORDS):
                    score += 3
                if "cover letter" in context_text and not any(
                    _normalize(word) in context_text for word in RESUME_WORDS
                ):
                    score = -10
                if score >= 0:
                    ranked.append((score, order, element))
                order += 1
            except Exception:
                order += 1
                continue

    if not ranked:
        return None
    highest = max(item[0] for item in ranked)
    winners = [item for item in ranked if item[0] == highest]
    # A specific label can disambiguate among several file inputs. Never guess
    # between equally plausible resume fields or unrelated unlabelled uploads.
    if highest > 0:
        return winners[0][2] if len(winners) == 1 else None
    return ranked[0][2] if len(ranked) == 1 else None


def upload_resume(page: Page | Frame, resume_path: str | Path) -> bool:
    """Upload a resume through the detected file input."""
    path = Path(resume_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Resume file not found: {path}")
    element = detect_resume_upload(page)
    if element is None:
        return False
    element.set_input_files(str(path))
    try:
        page.wait_for_timeout(500)
    except Exception:
        pass
    return True


def _visible_form_snapshot(page: Page | Frame) -> tuple[tuple[str, str], ...]:
    """Capture visible control values across the main page and embedded frames."""
    snapshot: list[tuple[str, str]] = []
    for frame_index, context in enumerate(_iter_search_contexts(page)):
        fields = context.locator("input, textarea, select")
        try:
            count = fields.count()
        except Exception:
            continue

        for index in range(count):
            element = fields.nth(index)
            try:
                if not element.is_visible():
                    continue
                tag, kind = _element_type(element)
                if kind in {"password", "file", "submit", "button", "reset"}:
                    continue
                value = element.input_value()
                if kind in {"checkbox", "radio"}:
                    value = "checked" if element.is_checked() else "unchecked"
                identifier = element.get_attribute("name") or element.get_attribute("id") or str(index)
                labels = "|".join(_label_candidates(element)[:2])
                context_url = getattr(context, "url", "")
                snapshot.append((f"{frame_index}:{context_url}:{tag}:{kind}:{identifier}:{labels}", value))
            except Exception:
                continue
    return tuple(snapshot)


def _wait_for_resume_processing(page: Page | Frame, timeout_ms: int = 8_000) -> None:
    """Wait for asynchronous resume parsing to settle, with a hard timeout."""
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
    """Fill a normal text-like input, textarea, or contenteditable and verify it."""
    tag, kind = _element_type(element)
    try:
        contenteditable = (element.get_attribute("contenteditable") or "").lower() == "true"
    except Exception:
        contenteditable = False

    if tag not in {"input", "textarea"} and not contenteditable:
        return False
    if kind in {"hidden", "file", "submit", "button", "reset", "checkbox", "radio", "password"}:
        return False
    if element.get_attribute("readonly") is not None or not element.is_visible() or not element.is_enabled():
        return False

    element.fill(value)
    try:
        actual = element.input_value() if tag in {"input", "textarea"} else element.inner_text()
    except Exception:
        return False
    return str(actual).strip() == value.strip()


def _answer_text(value: Any) -> str:
    """Convert booleans and numeric profile values to stable form text."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _choice_matches(wanted: str, option_text: str) -> bool:
    """Match exact normalized choices, allowing explicit Yes/No explanations."""
    wanted_normalized = _normalize(wanted)
    option_normalized = _normalize(option_text)
    if not wanted_normalized or not option_normalized:
        return False
    if wanted_normalized == option_normalized:
        return True
    return wanted_normalized in {"yes", "no"} and option_normalized.startswith(wanted_normalized + " ")


def _select_option(element: Any, value: Any) -> bool:
    """Select a native option only when exactly one option matches."""
    if not element.is_visible() or not element.is_enabled():
        return False
    wanted = _answer_text(value)
    options = element.locator("option")
    matches: list[int] = []
    for index in range(options.count()):
        option = options.nth(index)
        label = option.inner_text()
        option_value = option.get_attribute("value") or ""
        if _choice_matches(wanted, label) or _choice_matches(wanted, option_value):
            matches.append(index)
    if len(matches) != 1:
        return False

    element.select_option(index=matches[0])
    selected = element.locator("option:checked")
    if selected.count() != 1:
        return False
    selected_label = selected.first.inner_text()
    selected_value = selected.first.get_attribute("value") or ""
    return _choice_matches(wanted, selected_label) or _choice_matches(wanted, selected_value)


def _radio_group_key(element: Any, index: int) -> str:
    name = element.get_attribute("name") or ""
    if name:
        return f"name:{name}"
    try:
        group_label = element.evaluate(
            """el => {
                const fieldset = el.closest('fieldset');
                return fieldset ? ((fieldset.querySelector('legend') || {}).textContent || '').trim() : '';
            }"""
        )
        if group_label:
            return f"legend:{_normalize(group_label)}"
    except Exception:
        pass
    return f"radio:{element.get_attribute('id') or index}"


def _radio_group_elements(element: Any, index: int) -> list[Any]:
    del index
    name = element.get_attribute("name") or ""
    radios = element.page.locator('input[type="radio"]')
    if name:
        return [
            radios.nth(position)
            for position in range(radios.count())
            if (radios.nth(position).get_attribute("name") or "") == name
        ]
    try:
        fieldset = element.locator("xpath=ancestor::fieldset[1]")
        if fieldset.count():
            group = fieldset.locator('input[type="radio"]')
            return [group.nth(position) for position in range(group.count())]
    except Exception:
        pass
    return [element]


def _radio_option_labels(element: Any) -> list[str]:
    values: list[str] = []
    try:
        labels = element.evaluate(
            """el => el.labels ? Array.from(el.labels).map(label => (label.innerText || label.textContent || '').trim()).filter(Boolean) : []"""
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


def _select_radio_option(element: Any, value: Any, options: list[Any]) -> bool:
    """Check a radio only when its accessible label/value is an unambiguous match."""
    del element
    wanted = _answer_text(value)
    matches: list[Any] = []
    for option in options:
        try:
            if not option.is_visible() or not option.is_enabled():
                continue
            if any(_choice_matches(wanted, label) for label in _radio_option_labels(option)):
                matches.append(option)
        except Exception:
            continue
    if len(matches) != 1:
        return False
    matches[0].check()
    return matches[0].is_checked()


def _is_placeholder_select(element: Any) -> bool:
    try:
        selected = element.locator("option:checked")
        if not selected.count():
            return True
        label = _normalize(selected.first.inner_text())
        value = _normalize(selected.first.get_attribute("value") or "")
        return _is_placeholder_text(label) or _is_placeholder_text(value)
    except Exception:
        return False


def _is_placeholder_text(value: str) -> bool:
    normalized = _normalize(value)
    if normalized in PLACEHOLDER_VALUES:
        return True
    return normalized.startswith("select ") or normalized.startswith("choose ") or normalized.startswith("please select ") or normalized.startswith("please choose ")


def _control_values(element: Any, tag: str) -> list[str]:
    """Return possible displayed/current values for a control."""
    values: list[str] = []
    for attribute in ("aria-valuetext", "value"):
        try:
            raw = element.get_attribute(attribute)
        except Exception:
            raw = None
        if raw:
            values.append(str(raw).strip())
    if tag in {"input", "textarea", "select"}:
        try:
            raw = element.input_value()
            if raw:
                values.append(raw.strip())
        except Exception:
            pass
    else:
        try:
            raw = element.inner_text()
            if raw:
                values.append(raw.strip())
        except Exception:
            pass
    return list(dict.fromkeys(value for value in values if value))


def _control_matches_choice(element: Any, tag: str, wanted: str) -> bool:
    desired = _normalize(wanted)
    for current in _control_values(element, tag):
        normalized = _normalize(current)
        if _choice_matches(wanted, current):
            return True
        # Selected custom controls sometimes include a field label plus value.
        if desired and f" {desired} " in f" {normalized} ":
            return True
    return False


def _find_custom_options(page: Page, wanted: str) -> list[Any]:
    """Find visible option items in common ARIA listbox implementations."""
    options = page.locator("[role='option'], [role='listbox'] li, [role='listbox'] button")
    matches: list[Any] = []
    for index in range(options.count()):
        option = options.nth(index)
        try:
            if not option.is_visible() or not option.is_enabled():
                continue
            candidates = [option.inner_text()]
            for attribute in ("aria-label", "value", "data-value"):
                attr_value = option.get_attribute(attribute)
                if attr_value:
                    candidates.append(attr_value)
            if any(_choice_matches(wanted, candidate) for candidate in candidates if candidate):
                matches.append(option)
        except Exception:
            continue
    return matches


def _select_custom_combobox(element: Any, page: Page, value: Any) -> bool:
    """Select a matching option from an accessible custom combobox/listbox."""
    if not element.is_visible() or not element.is_enabled():
        return False

    wanted = _answer_text(value)
    tag, _ = _element_type(element)
    initial_values = _control_values(element, tag)
    original_value = initial_values[-1] if initial_values else ""

    try:
        element.click()
    except Exception:
        return False

    try:
        page.wait_for_timeout(100)
    except Exception:
        pass
    matches = _find_custom_options(page, wanted)

    # Searchable comboboxes may not render options until text is entered.
    if not matches and tag == "input":
        try:
            readonly = element.get_attribute("readonly") is not None
            input_type = (element.get_attribute("type") or "text").lower()
            if not readonly and input_type in {"text", "search", "email", "tel"}:
                element.fill(wanted)
                try:
                    page.wait_for_timeout(250)
                except Exception:
                    pass
                matches = _find_custom_options(page, wanted)
        except Exception:
            matches = []

    if len(matches) != 1:
        # Don't leave a guessed free-text value in a combobox when no option matched.
        if tag == "input":
            try:
                element.fill(original_value)
                element.press("Escape")
            except Exception:
                pass
        else:
            try:
                element.press("Escape")
            except Exception:
                pass
        return False

    option = matches[0]
    try:
        option.click()
    except Exception:
        return False

    try:
        page.wait_for_timeout(100)
    except Exception:
        pass

    if _control_matches_choice(element, tag, wanted):
        return True
    try:
        return (
            (option.get_attribute("aria-selected") or "").lower() == "true"
            or (option.get_attribute("aria-checked") or "").lower() == "true"
        )
    except Exception:
        return False


def _display_field(candidates: list[str], fallback: str) -> str:
    """Return a readable label for the gap report."""
    for candidate in candidates:
        if candidate and not candidate.startswith(("application", "input", "field")):
            return candidate
    return fallback


def _fill_standard_fields_in_context(
    page: Page | Frame,
    profile: CandidateProfile,
    field_mappings: dict[str, list[str]] | None = None,
) -> FormGapReport:
    """Fill recognized empty form fields; preserve existing answers and verify changes."""
    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    profile_data = _profile_data(profile)
    report = FormGapReport()

    # Includes custom accessible dropdowns that are not native <select> elements.
    fields = page.locator(
        "input, textarea, select, [role='combobox'], [aria-haspopup='listbox'], [contenteditable='true']"
    )
    seen_radio_groups: set[str] = set()

    for index in range(fields.count()):
        element = fields.nth(index)
        try:
            if not element.is_visible() or not element.is_enabled():
                continue
            tag, kind = _element_type(element)
            role = _element_role(element)
            is_custom_combo = _is_custom_combobox(element, tag)

            if kind in {"hidden", "submit", "reset", "file", "password"}:
                continue
            if kind == "button" and not is_custom_combo:
                continue
            # Read-only text inputs can't be filled, but read-only comboboxes can
            # still be clicked to reveal their options.
            if element.get_attribute("readonly") is not None and not is_custom_combo and tag != "select":
                continue

            candidates = _label_candidates(element)
            label = _display_field(candidates, f"Field #{index + 1}")

            if kind == "radio":
                group_key = _radio_group_key(element, index)
                if group_key in seen_radio_groups:
                    continue
                seen_radio_groups.add(group_key)
                options = _radio_group_elements(element, index)
                if _radio_group_selected(options):
                    report.skipped.append(f"{label} (already answered)")
                    continue
                match = _resolve_field(candidates, profile_data, mappings)
                if match is None:
                    report.unknown.append(label)
                    continue
                key, value = match
                if _select_radio_option(element, value, options):
                    report.filled.append(f"{label} <- {key}")
                else:
                    report.failed.append(f"{label} (no unambiguous radio option for profile answer)")
                continue

            if kind == "checkbox" or role == "checkbox":
                match = _resolve_field(candidates, profile_data, mappings)
                if match is None:
                    report.unknown.append(label)
                    continue
                key, value = match
                if not isinstance(value, bool):
                    report.skipped.append(f"{label} (checkbox requires an explicit true/false profile value)")
                    continue
                try:
                    current_checked = element.is_checked()
                except Exception:
                    current_checked = (element.get_attribute("aria-checked") or "").lower() == "true"
                if current_checked == value:
                    report.skipped.append(f"{label} (already matches profile)")
                    continue
                try:
                    if value:
                        element.check()
                    else:
                        element.uncheck()
                    verified = element.is_checked() == value
                except Exception:
                    try:
                        element.click()
                        verified = (element.get_attribute("aria-checked") or "").lower() == str(value).lower()
                    except Exception:
                        verified = False
                if verified:
                    report.filled.append(f"{label} <- {key}")
                else:
                    report.failed.append(f"{label} (checkbox change could not be verified)")
                continue

            current_values = _control_values(element, tag)
            if tag == "select":
                if not _is_placeholder_select(element):
                    report.skipped.append(f"{label} (already filled)")
                    continue
            elif is_custom_combo:
                if current_values and not any(_is_placeholder_text(_normalize(v)) for v in current_values):
                    # Preserve an existing meaningful custom selection.
                    if not all(_is_placeholder_text(_normalize(v)) for v in current_values):
                        report.skipped.append(f"{label} (already filled)")
                        continue
            elif current_values and any(value.strip() for value in current_values):
                report.skipped.append(f"{label} (already filled)")
                continue

            match = _resolve_field(candidates, profile_data, mappings)
            if match is None:
                report.unknown.append(label)
                continue
            key, value = match
            value_text = _answer_text(value)

            if tag == "select":
                success = _select_option(element, value)
            elif is_custom_combo:
                success = _select_custom_combobox(element, page, value)
            else:
                success = _fill_text(element, value_text)

            if success:
                report.filled.append(f"{label} <- {key}")
            else:
                report.failed.append(f"{label} (could not set and verify profile value for {key})")

        except Exception as exc:
            report.failed.append(f"Field #{index + 1} ({type(exc).__name__})")

    return report


def fill_standard_fields(
    page: Page | Frame,
    profile: CandidateProfile,
    field_mappings: dict[str, list[str]] | None = None,
) -> FormGapReport:
    """Fill recognized fields in the main page and any embedded application frames."""
    mappings = field_mappings if field_mappings is not None else load_field_mappings()
    combined = FormGapReport()
    for context in _iter_search_contexts(page):
        report = _fill_standard_fields_in_context(context, profile, mappings)
        combined.filled.extend(report.filled)
        combined.skipped.extend(report.skipped)
        combined.failed.extend(report.failed)
        combined.unknown.extend(report.unknown)
    combined.filled = list(dict.fromkeys(combined.filled))
    combined.skipped = list(dict.fromkeys(combined.skipped))
    combined.failed = list(dict.fromkeys(combined.failed))
    combined.unknown = list(dict.fromkeys(combined.unknown))
    return combined


def _find_next_button(page: Page | Frame) -> Any | None:
    """Find a visible Next/Continue action in the page or an embedded frame."""
    for context in _iter_search_contexts(page):
        for role in ("button", "link"):
            try:
                locator = context.get_by_role(role, name=NEXT_PATTERN)
                for index in range(locator.count()):
                    element = locator.nth(index)
                    if not element.is_visible() or not element.is_enabled():
                        continue
                    try:
                        label = _normalize(element.inner_text() or element.get_attribute("aria-label") or "")
                    except Exception:
                        label = ""
                    # Do not treat "Apply", "Submit", or finish controls as
                    # navigation actions even if their longer name contains "continue".
                    if re.search(r"\b(submit|apply|finish|send application)\b", label):
                        continue
                    return element
            except Exception:
                continue
    return None


def _has_submit_button(page: Page | Frame) -> bool:
    """Detect a final-submit control in the main page or any embedded frame."""
    for context in _iter_search_contexts(page):
        try:
            buttons = context.get_by_role("button", name=SUBMIT_PATTERN)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                if button.is_visible() and button.is_enabled():
                    return True
        except Exception:
            continue
    return False


def _wait_for_form_progress(
    page: Page,
    previous_url: str,
    previous_snapshot: tuple[tuple[str, str], ...],
    timeout_ms: int = 3_000,
) -> bool:
    """Wait briefly for a SPA/page transition after a Next button is clicked."""
    deadline = time.monotonic() + max(250, timeout_ms) / 1000
    while time.monotonic() < deadline:
        try:
            page.wait_for_timeout(250)
        except Exception:
            return True
        try:
            if page.url != previous_url:
                return True
        except Exception:
            pass
        try:
            if _visible_form_snapshot(page) != previous_snapshot:
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
        candidates = [base_dir / f"{resume_name}{suffix}" for suffix in (".pdf", ".docx", ".doc")]
        resume_path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])

    report = FormGapReport()
    if resume_path.is_file():
        try:
            report.resume_uploaded = upload_resume(page, resume_path)
            if not report.resume_uploaded:
                report.skipped.append("No recognizable resume upload field found")
            else:
                _wait_for_resume_processing(page)
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

        # Never click the final Submit/Apply button.
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
    """Pause for manual review; Job-Agent never submits an application."""
    del page
    print("\nReview the form in the browser and complete any unanswered questions.")
    print("Job-Agent will not click Submit or Apply for you.")
    input("Press Enter here after you have reviewed the application...")
