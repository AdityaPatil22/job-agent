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
