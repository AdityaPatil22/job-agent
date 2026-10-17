"""Regression tests for robust job URL ingestion."""

from __future__ import annotations

import json

import pytest
import requests

from job_agent import job_parser


def test_parse_job_html_reads_json_ld_job_posting() -> None:
    html = """
    <html><head><title>Careers</title>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Senior Platform Engineer",
      "description": "<p>Build reliable systems.</p>",
      "hiringOrganization": {"@type": "Organization", "name": "Example Inc."},
      "jobLocation": {
        "@type": "Place",
        "address": {
          "addressLocality": "Pune",
          "addressRegion": "Maharashtra",
          "addressCountry": "India"
        }
      }
    }
    </script></head><body></body></html>
    """
    job = job_parser.parse_job_html(html, "https://careers.example.com/jobs/platform-engineer")

    assert job.title == "Senior Platform Engineer"
    assert job.company == "Example Inc."
    assert "Build reliable systems" in job.description
    assert job.location == "Pune, Maharashtra, India"


def test_url_is_saved_as_draft_when_http_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_get(*args, **kwargs):
        raise requests.HTTPError("403 Forbidden")

    monkeypatch.setattr(job_parser.requests, "get", blocked_get)
    monkeypatch.setattr(job_parser, "_fetch_html_with_playwright", lambda _url: "")

    url = "https://www.rubrik.com/company/careers/job.8258200.1929?reqId=INSW11531"
    job = job_parser.parse_job_from_url(url)

    assert job.url == url
    assert job.company == "Rubrik"
    assert job.title == "Untitled role"


def test_browser_html_is_used_when_http_page_has_no_job_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        text = "<html><head><title>Careers</title></head><body>Loading...</body></html>"

        @staticmethod
        def raise_for_status() -> None:
            return None

    browser_html = """
    <html><head>
    <meta property="og:title" content="Backend Engineer">
    <meta property="og:site_name" content="Example Corp">
    </head><body></body></html>
    """
    monkeypatch.setattr(job_parser.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(job_parser, "_fetch_html_with_playwright", lambda _url: browser_html)

    job = job_parser.parse_job_from_url("https://careers.example.com/jobs/123")

    assert job.title == "Backend Engineer"
    assert job.company == "Example Corp"


def test_url_fallback_extracts_title_from_ats_slug() -> None:
    url = "https://jobs.lever.co/example-co/senior-platform-engineer-12345678-abcd-1234"
    job = job_parser._fallback_job_from_url(url)

    assert job.company == "Example Co"
    assert job.title == "Senior Platform Engineer"


def test_invalid_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="complete job URL"):
        job_parser.parse_job_from_url("not-a-url")
