"""Regression tests for form matching, custom dropdowns, and verification.

Run from the repository root after installing dependencies and Chromium:
    python -m playwright install chromium
    pytest -q tests/test_autofill.py
"""

from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import Error, Page, sync_playwright

from job_agent.autofill import _profile_data, _resolve_field, fill_standard_fields
from job_agent.models import CandidateProfile


FIELD_MAPPINGS: dict[str, list[str]] = {
    "linkedin": [
        "linkedin profile",
        "linkedin profile url",
        "linkedin profile link",
    ],
    "current_company": [
        "current employer",
        "most recent employer",
        "company name",
    ],
    "current_title": ["current job title", "current position"],
    "employment_type": ["employment type", "work arrangement"],
    "years_experience": [
        "years of experience",
        "total professional experience",
        "total years of work experience",
    ],
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(headless=True)
        except Error as first_error:
            # Useful in Linux CI images that provide system Chromium but not
            # Playwright's bundled browser. Standard installs use the first path.
            system_chromium = shutil.which("chromium") or shutil.which("chromium-browser")
            if system_chromium:
                try:
                    instance = playwright.chromium.launch(
                        headless=True, executable_path=system_chromium
                    )
                except Error as second_error:
                    pytest.skip(
                        "Could not launch Playwright or system Chromium. Install with "
                        f"`python -m playwright install chromium`. {second_error}"
                    )
            else:
                pytest.skip(
                    "Chromium is not installed; run `python -m playwright install chromium`. "
                    f"Original error: {first_error}"
                )
        yield instance
        instance.close()


@pytest.fixture

def page(browser) -> Page:
    current_page = browser.new_page()
    yield current_page
    current_page.close()


@pytest.fixture

def profile() -> CandidateProfile:
    return CandidateProfile(
        name="Aditya Patil",
        first_name="Aditya",
        last_name="Patil",
        email="aditya@example.com",
        linkedin_url="https://linkedin.com/in/aditya",
        current_company="Red Hat",
        current_title="Associate Software Engineer",
        employment_type="Full-time",
        years_experience=2,
        work_authorization="India",
        requires_visa_sponsorship=False,
        willing_to_relocate=True,
        education=[
            {
                "school": "MIT-WPU",
                "degree": "B.Tech",
                "field": "Computer Engineering",
                "start_year": 2020,
                "year": 2024,
            }
        ],
    )


def test_resolves_label_when_wording_differs(profile: CandidateProfile) -> None:
    resolved = _resolve_field(
        ["linkedin profile link"],
        _profile_data(profile),
        FIELD_MAPPINGS,
    )
    assert resolved == ("linkedin", "https://linkedin.com/in/aditya")


def test_does_not_map_previous_employer_to_current_company(
    profile: CandidateProfile,
) -> None:
    resolved = _resolve_field(
        ["previous employer"],
        _profile_data(profile),
        FIELD_MAPPINGS,
    )
    assert resolved is None


def test_fills_text_field_and_verifies_value(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <form>
          <label for="profile-link">LinkedIn Profile Link</label>
          <input id="profile-link" name="candidate_profile_link" />
        </form>
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator("#profile-link").input_value() == "https://linkedin.com/in/aditya"
    assert any("linkedin" in item for item in report.filled)
    assert not report.failed


def test_selects_native_dropdown_by_visible_label(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <label for="employment">Employment Type</label>
        <select id="employment" name="employment_type">
          <option value="">Select one</option>
          <option value="full-time">Full-time</option>
          <option value="contract">Contract</option>
        </select>
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator("#employment").input_value() == "full-time"
    assert any("employment_type" in item for item in report.filled)
    assert not report.failed


def test_selects_accessible_custom_dropdown(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <div class="form-field">
          <label id="employment-label">Employment Type</label>
          <button id="employment" type="button" role="combobox"
                  aria-labelledby="employment-label"
                  aria-expanded="false">Select employment type</button>
          <div id="employment-options" role="listbox" hidden>
            <div role="option">Full-time</div>
            <div role="option">Contract</div>
          </div>
        </div>
        <script>
          const button = document.querySelector('#employment');
          const list = document.querySelector('#employment-options');
          button.addEventListener('click', () => {
            list.hidden = false;
            button.setAttribute('aria-expanded', 'true');
          });
          list.querySelectorAll('[role="option"]').forEach(option => {
            option.addEventListener('click', () => {
              button.textContent = option.textContent;
              list.hidden = true;
              button.setAttribute('aria-expanded', 'false');
              option.setAttribute('aria-selected', 'true');
            });
          });
        </script>
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator("#employment").inner_text() == "Full-time"
    assert any("employment_type" in item for item in report.filled)
    assert not report.failed


def test_selects_searchable_custom_dropdown(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <label for="employment">Employment Type</label>
        <input id="employment" role="combobox" aria-expanded="false" />
        <div id="options" role="listbox" hidden>
          <div role="option">Full-time</div>
          <div role="option">Contract</div>
        </div>
        <script>
          const input = document.querySelector('#employment');
          const list = document.querySelector('#options');
          input.addEventListener('input', () => { list.hidden = false; });
          input.addEventListener('click', () => { list.hidden = false; });
          list.querySelectorAll('[role="option"]').forEach(option => {
            option.addEventListener('click', () => {
              input.value = option.textContent;
              list.hidden = true;
              input.setAttribute('aria-expanded', 'false');
            });
          });
        </script>
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator("#employment").input_value() == "Full-time"
    assert any("employment_type" in item for item in report.filled)
    assert not report.failed


def test_does_not_choose_a_non_matching_dropdown_option(
    page: Page, profile: CandidateProfile,
) -> None:
    page.set_content(
        """
        <label for="employment">Employment Type</label>
        <select id="employment">
          <option value="">Select one</option>
          <option value="contract">Contract</option>
          <option value="temporary">Temporary</option>
        </select>
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator("#employment").input_value() == ""
    assert any("employment_type" in item for item in report.failed)


def test_reports_unknown_custom_question(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <label for="language">What is your favorite programming language?</label>
        <input id="language" name="favorite_language" />
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator("#language").input_value() == ""
    assert report.unknown


def test_preserves_existing_non_empty_answer(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <label for="email">Email Address</label>
        <input id="email" value="already-entered@example.com" />
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator("#email").input_value() == "already-entered@example.com"
    assert any("already filled" in item for item in report.skipped)


def test_selects_radio_option_from_question_legend(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <fieldset>
          <legend>Do you require visa sponsorship?</legend>
          <label><input type="radio" name="sponsorship" value="yes"> Yes</label>
          <label><input type="radio" name="sponsorship" value="no"> No</label>
        </fieldset>
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator('input[name="sponsorship"][value="no"]').is_checked()
    assert any("visa_sponsorship" in item for item in report.filled)


def test_sets_explicit_boolean_checkbox(page: Page, profile: CandidateProfile) -> None:
    page.set_content(
        """
        <label><input type="checkbox" name="relocation">Willing to relocate</label>
        """
    )

    report = fill_standard_fields(page, profile, FIELD_MAPPINGS)

    assert page.locator('input[name="relocation"]').is_checked()
    assert any("relocation" in item for item in report.filled)
