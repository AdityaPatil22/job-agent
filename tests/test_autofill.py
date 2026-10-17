"""Regression tests for the ATS-agnostic autofill workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from job_agent.autofill import (
    _get_field_value,
    _profile_data,
    _choice_matches,
    _find_next_button,
    _has_submit_button,
    autofill_form,
    fill_standard_fields,
    prepare_application_page,
    upload_resume,
)
from job_agent.models import CandidateProfile


@pytest.fixture
def page():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        yield page
    finally:
        browser.close()
        playwright.stop()


def test_fills_profile_fields_and_selects_native_dropdown(page) -> None:
    page.set_content(
        """
        <form>
          <label for="first_name">First Name</label>
          <input id="first_name" name="first_name">
          <label for="email">Email address</label>
          <input id="email" name="email" type="email">
          <label for="country">Country</label>
          <select id="country" name="country">
            <option value="">Select country</option>
            <option value="IN">India</option>
            <option value="US">United States</option>
          </select>
        </form>
        """
    )
    profile = CandidateProfile(first_name="Ada", email="ada@example.org", country="India")

    report = fill_standard_fields(
        page,
        profile,
        {
            "first_name": ["first name"],
            "email": ["email address"],
            "country": ["country"],
        },
    )

    assert page.locator("#first_name").input_value() == "Ada"
    assert page.locator("#email").input_value() == "ada@example.org"
    assert page.locator("#country").input_value() == "IN"
    assert len(report.filled) == 3
    assert not report.failed


def test_fills_fields_inside_embedded_application_iframe(page) -> None:
    page.set_content("<iframe id='application'></iframe>")
    page.evaluate(
        """
        () => {
          const doc = document.querySelector('#application').contentDocument;
          doc.body.innerHTML = '<label for="email">Email address</label><input id="email" name="email">';
        }
        """
    )
    profile = CandidateProfile(email="ada@example.org")

    report = fill_standard_fields(page, profile, {"email": ["email address"]})

    assert page.frame_locator("#application").locator("#email").input_value() == "ada@example.org"
    assert any("email" in item.lower() and "<- email" in item.lower() for item in report.filled)
    assert not report.failed


def test_selects_custom_combobox_using_descriptive_option_text(page) -> None:
    page.set_content(
        """
        <label for="country">Country</label>
        <input id="country" role="combobox" aria-haspopup="listbox" placeholder="Select country">
        <ul id="options" role="listbox" hidden>
          <li role="option">India (+91)</li>
          <li role="option">Indonesia</li>
        </ul>
        <script>
          const input = document.querySelector('#country');
          const list = document.querySelector('#options');
          input.addEventListener('click', () => { list.hidden = false; });
          for (const option of list.querySelectorAll('[role="option"]')) {
            option.addEventListener('click', () => {
              input.value = option.textContent;
              input.setAttribute('aria-valuetext', option.textContent);
              list.hidden = true;
            });
          }
        </script>
        """
    )
    profile = CandidateProfile(country="India")

    report = fill_standard_fields(page, profile, {"country": ["country"]})

    assert page.locator("#country").input_value() == "India (+91)"
    assert any("country <- country" in item.lower() for item in report.filled)
    assert not report.failed


def test_resume_upload_is_detected_inside_iframe(page, tmp_path: Path) -> None:
    resume = tmp_path / "candidate-resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test resume")
    page.set_content("<iframe id='application'></iframe>")
    page.evaluate(
        """
        () => {
          const doc = document.querySelector('#application').contentDocument;
          doc.body.innerHTML = '<label for="resume">Resume / CV</label><input id="resume" name="resume" type="file">';
        }
        """
    )

    assert upload_resume(page, resume)
    assert (
        page.frame_locator("#application")
        .locator("#resume")
        .evaluate("element => element.files[0].name")
        == "candidate-resume.pdf"
    )


def test_multistep_form_fills_each_page_and_never_clicks_submit(page, tmp_path: Path) -> None:
    page.set_content(
        """
        <form id="application">
          <label for="first_name">First Name</label>
          <input id="first_name" name="first_name">
          <button id="next" type="button">Continue to contact details</button>
        </form>
        <script>
          document.querySelector('#next').addEventListener('click', () => {
            document.querySelector('#application').innerHTML =
              '<label for="email">Email address</label>' +
              '<input id="email" name="email" type="email">' +
              '<button id="submit" type="button">Submit application</button>';
          });
        </script>
        """
    )
    profile = CandidateProfile(first_name="Ada", email="ada@example.org")

    report = autofill_form(
        page,
        job_id=1,
        resume_name="missing.pdf",
        profile=profile,
        resumes_dir=tmp_path,
        field_mappings={
            "first_name": ["first name"],
            "email": ["email address"],
        },
        max_pages=3,
    )

    assert page.locator("#email").input_value() == "ada@example.org"
    assert len(report.filled) == 2
    assert _has_submit_button(page)
    assert page.locator("#submit").count() == 1
    assert not report.failed


def test_does_not_select_submit_as_a_next_action(page) -> None:
    page.set_content(
        """
        <button type="button">Submit application</button>
        <button type="button">Apply now</button>
        """
    )

    assert _find_next_button(page) is None
    assert _has_submit_button(page)


def test_example_profile_placeholders_are_not_used_as_answers() -> None:
    profile = CandidateProfile(name="Your Full Name", first_name="Your First Name", email="your.email@example.com")

    values = _profile_data(profile)

    assert _get_field_value(values, "full_name") is None
    assert _get_field_value(values, "first_name") is None
    assert _get_field_value(values, "email") is None


@pytest.mark.parametrize(
    ("wanted", "option", "expected"),
    [
        ("India", "India (+91)", True),
        ("Full-time", "Full-time employment", True),
        ("Yes", "Yes, I agree", True),
        ("No", "I do not agree", False),
        ("India", "Indonesia", False),
    ],
)
def test_dropdown_choice_matching(wanted: str, option: str, expected: bool) -> None:
    assert _choice_matches(wanted, option) is expected


def test_real_name_overrides_unedited_name_placeholders() -> None:
    profile = CandidateProfile(
        name="Ada Lovelace",
        first_name="Your First Name",
        last_name="Your Last Name",
    )

    values = _profile_data(profile)

    assert _get_field_value(values, "first_name") == "Ada"
    assert _get_field_value(values, "last_name") == "Lovelace"


def test_fills_skills_location_and_second_address_line(page) -> None:
    page.set_content(
        """
        <label for="skills">Technical skills</label>
        <input id="skills" name="skills">
        <label for="location">Current location</label>
        <input id="location" name="location">
        <label for="address_line_2">Address line 2</label>
        <input id="address_line_2" name="address_line_2">
        """
    )
    profile = CandidateProfile(
        skills=["Python", "Playwright"],
        location="Pune, Maharashtra, India",
        address_line_2="Baner Road",
    )

    report = fill_standard_fields(
        page,
        profile,
        {
            "skills": ["technical skills"],
            "location": ["current location"],
            "address_line_2": ["address line 2"],
        },
    )

    assert page.locator("#skills").input_value() == "Python, Playwright"
    assert page.locator("#location").input_value() == "Pune, Maharashtra, India"
    assert page.locator("#address_line_2").input_value() == "Baner Road"
    assert len(report.filled) == 3
    assert not report.failed


def test_handles_custom_aria_checkbox(page) -> None:
    page.set_content(
        """
        <div role="group" aria-label="Relocation">
          <div id="relocation" role="checkbox" aria-label="Willing to relocate"
               aria-checked="false" tabindex="0">Willing to relocate</div>
        </div>
        <script>
          const checkbox = document.querySelector('#relocation');
          checkbox.addEventListener('click', () => {
            checkbox.setAttribute(
              'aria-checked',
              checkbox.getAttribute('aria-checked') === 'true' ? 'false' : 'true'
            );
          });
        </script>
        """
    )
    profile = CandidateProfile(relocation=True)

    report = fill_standard_fields(
        page,
        profile,
        {"relocation": ["willing to relocate"]},
    )

    assert page.locator("#relocation").get_attribute("aria-checked") == "true"
    assert any("relocation <- relocation" in item.lower() for item in report.filled)
    assert not report.failed


def test_handles_custom_aria_radio_group(page) -> None:
    page.set_content(
        """
        <div role="radiogroup" aria-label="Work authorization">
          <div id="authorized-yes" role="radio" aria-label="Yes"
               aria-checked="false" tabindex="0">Yes</div>
          <div id="authorized-no" role="radio" aria-label="No"
               aria-checked="false" tabindex="0">No</div>
        </div>
        <script>
          for (const radio of document.querySelectorAll('[role="radio"]')) {
            radio.addEventListener('click', () => {
              for (const option of document.querySelectorAll('[role="radio"]')) {
                option.setAttribute('aria-checked', String(option === radio));
              }
            });
          }
        </script>
        """
    )
    profile = CandidateProfile(work_authorization="Yes")

    report = fill_standard_fields(
        page,
        profile,
        {"work_authorization": ["work authorization", "authorized to work"]},
    )

    assert page.locator("#authorized-yes").get_attribute("aria-checked") == "true"
    assert page.locator("#authorized-no").get_attribute("aria-checked") == "false"
    assert any("work authorization <- work_authorization" in item.lower() for item in report.filled)
    assert not report.failed


def test_opens_apply_link_when_job_details_page_has_no_form(page) -> None:
    page.set_content(
        """
        <a id="apply" href="#application">Apply now</a>
        <div id="application" hidden>
          <label for="first_name">First name</label>
          <input id="first_name" name="first_name">
          <label for="email">Email address</label>
          <input id="email" name="email">
        </div>
        <script>
          document.querySelector('#apply').addEventListener('click', event => {
            event.preventDefault();
            document.querySelector('#application').hidden = false;
          });
        </script>
        """
    )

    prepared = prepare_application_page(page, {"first_name": ["first name"], "email": ["email address"]})

    assert prepared is page
    assert page.locator("#first_name").is_visible()
    assert page.locator("#email").is_visible()


def test_does_not_click_apply_button_inside_a_form(page) -> None:
    page.set_content(
        """
        <form id="newsletter">
          <label for="newsletter-email">Email address</label>
          <input id="newsletter-email" name="email">
          <button id="apply" type="button">Apply now</button>
        </form>
        <script>
          document.querySelector('#apply').addEventListener('click', () => {
            document.body.dataset.applyClicked = 'true';
          });
        </script>
        """
    )

    prepared = prepare_application_page(page, {"email": ["email address"]})

    assert prepared is page
    assert page.locator("body").get_attribute("data-apply-clicked") is None


def test_does_not_click_ambiguous_one_click_apply_button(page) -> None:
    page.set_content(
        """
        <button id="apply">Apply</button>
        <script>
          document.querySelector('#apply').addEventListener('click', () => {
            document.body.dataset.applyClicked = 'true';
          });
        </script>
        """
    )

    prepared = prepare_application_page(page, {})

    assert prepared is page
    assert page.locator("body").get_attribute("data-apply-clicked") is None


def test_detects_single_field_ats_step_without_application_form_wrapper(page, tmp_path: Path) -> None:
    page.set_content(
        """
        <label for="first_name">First name</label>
        <input id="first_name" name="first_name">
        <button id="next" type="button">Continue</button>
        <script>
          document.querySelector('#next').addEventListener('click', () => {
            document.body.innerHTML =
              '<label for="email">Email address</label>' +
              '<input id="email" name="email">' +
              '<button id="submit" type="button">Submit application</button>';
          });
        </script>
        """
    )

    report = autofill_form(
        page,
        job_id=1,
        resume_name="missing.pdf",
        profile=CandidateProfile(first_name="Ada", email="ada@example.org"),
        resumes_dir=tmp_path,
        field_mappings={"first_name": ["first name"], "email": ["email address"]},
        max_pages=3,
    )

    assert page.locator("#email").input_value() == "ada@example.org"
    assert len(report.filled) == 2
    assert not report.failed
