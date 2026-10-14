"""Parse job listing metadata from career page URLs."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import Job


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 15


def detect_page_type(html: str, url: str = "") -> str:
    """Return a best-effort ATS/site type label."""
    combined = f"{url}\n{html[:100_000]}".lower()
    if "greenhouse.io" in combined:
        return "greenhouse"
    if "lever.co" in combined or "jobs.lever.co" in combined:
        return "lever"
    if "myworkdayjobs.com" in combined or "workday" in combined:
        return "workday"
    if "linkedin.com" in combined:
        return "linkedin"
    return "generic"


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _meta_content(soup: BeautifulSoup, *selectors: tuple[str, dict[str, str]]) -> str:
    for name, attrs in selectors:
        tag = soup.find(name, attrs=attrs)
        if tag and tag.get("content"):
            return _clean(str(tag["content"]))
    return ""


def normalize_job_data(raw_data: dict[str, str], url: str) -> Job:
    """Normalize scraped fields and validate the source URL."""
    company = _clean(raw_data.get("company")) or "Unknown company"
    title = _clean(raw_data.get("title")) or "Untitled role"
    description = _clean(raw_data.get("description"))
    location = _clean(raw_data.get("location"))
    return Job(company=company, title=title, url=url, description=description, location=location)


def parse_job_html(html: str, url: str) -> Job:
    """Parse job metadata from HTML without making a network request."""
    soup = BeautifulSoup(html, "html.parser")
    title = _meta_content(soup, ("meta", {"property": "og:title"}), ("meta", {"name": "twitter:title"}))
    if not title and soup.title:
        title = _clean(soup.title.get_text(" ", strip=True))
    if not title:
        heading = soup.find(["h1", "h2"])
        title = _clean(heading.get_text(" ", strip=True)) if heading else ""

    company = _meta_content(soup, ("meta", {"property": "og:site_name"}), ("meta", {"name": "author"}))
    description = _meta_content(soup, ("meta", {"property": "og:description"}), ("meta", {"name": "description"}))
    location = ""
    for selector in ['[data-testid*="location"]', '[class*="location"]', '[itemprop="jobLocation"]']:
        element = soup.select_one(selector)
        if element:
            location = _clean(element.get_text(" ", strip=True))
            if location:
                break

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            import html as html_module
            payload = json.loads(html_module.unescape(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if item_type != "JobPosting" and not (isinstance(item_type, list) and "JobPosting" in item_type):
                continue
            title = _clean(str(item.get("title") or title))
            hiring_org = item.get("hiringOrganization") or {}
            if isinstance(hiring_org, dict):
                company = _clean(str(hiring_org.get("name") or company))
            description = _clean(BeautifulSoup(str(item.get("description") or description), "html.parser").get_text(" ", strip=True))
            job_location = item.get("jobLocation")
            if isinstance(job_location, dict):
                address = job_location.get("address") or {}
                if isinstance(address, dict):
                    location = ", ".join(str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key)) or location
            elif isinstance(job_location, list) and job_location and isinstance(job_location[0], dict):
                address = job_location[0].get("address") or {}
                if isinstance(address, dict):
                    location = ", ".join(str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key)) or location
            break

    return normalize_job_data({"title": title, "company": company, "description": description, "location": location}, url)


def parse_job_from_url(url: str, playwright_page=None) -> Job:
    """Fetch and parse job details with requests or a supplied Playwright page."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Please provide a complete job URL beginning with https:// or http://")
    if playwright_page is not None:
        playwright_page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        html = playwright_page.content()
    else:
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not fetch job page {url}: {exc}") from exc
        html = response.text
    return parse_job_html(html, url)
