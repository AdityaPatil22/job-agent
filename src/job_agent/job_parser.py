"""Parse job listing metadata from career page URLs.

Career sites frequently block ordinary HTTP clients or render the posting with
JavaScript. The parser therefore tries requests first, retries with Playwright
when needed, and finally returns a URL-derived draft so a valid URL can still
be tracked when the site cannot be scraped.
"""

from __future__ import annotations

import html as html_module
import json
import re
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

from .models import Job


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 15
BROWSER_TIMEOUT_MS = 15_000
PLACEHOLDER_VALUES = {"", "unknown company", "untitled role"}
GENERIC_PAGE_TITLES = {
    "career", "careers", "careers home", "job", "jobs", "job search",
    "job listings", "job openings", "job opportunities", "open positions",
    "opportunities", "sign in", "login", "home",
}
CHALLENGE_MARKERS = (
    "just a moment",
    "access denied",
    "attention required",
    "verify you are human",
    "security check",
    "captcha",
    "request blocked",
)
IGNORED_PATH_SEGMENTS = {
    "apply", "application", "applications", "career", "careers", "company",
    "details", "en", "in", "job", "jobs", "opening", "openings", "position",
    "positions", "posting", "postings", "requisition", "requisitions", "role",
    "roles", "search", "us", "view",
}
IGNORED_HOST_LABELS = {
    "apply", "boards", "careers", "career", "jobs", "job", "join", "www",
    "workday", "myworkdayjobs", "greenhouse", "lever", "ashbyhq",
    "smartrecruiters", "workable", "co", "com", "org", "net", "io", "in",
    "uk", "de", "fr", "au", "gov", "edu",
}
ATS_HOST_SUFFIXES = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "smartrecruiters.com",
)


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


def _is_detected(value: str, placeholder: str) -> bool:
    cleaned = _clean(value)
    lowered = cleaned.lower()
    return bool(
        cleaned
        and lowered != placeholder.lower()
        and lowered not in GENERIC_PAGE_TITLES
        and not any(marker in lowered for marker in CHALLENGE_MARKERS)
    )


def _needs_browser_retry(job: Job) -> bool:
    return not _is_detected(job.title, "Untitled role") or not _is_detected(job.company, "Unknown company")


def _fetch_html_with_playwright(url: str) -> str:
    """Try Chromium for client-rendered sites or sites that block requests."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
                except Exception:
                    # Some sites keep navigations open while loading analytics.
                    # Their already-rendered HTML can still contain the posting.
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=3_000)
                except Exception:
                    pass
                return page.content()
            finally:
                browser.close()
    except Exception:
        # Playwright's browser binary may not be installed, or the site may
        # refuse browser traffic too. URL-based fallback still lets users track it.
        return ""


def _infer_company_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path_parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]

    if any(host.endswith(suffix) for suffix in ATS_HOST_SUFFIXES):
        for part in path_parts:
            if part.lower() not in IGNORED_PATH_SEGMENTS and not part.isdigit():
                return re.sub(r"[-_]+", " ", part).strip().title()

    labels = host.split(".")
    labels = [label for label in labels if label and not re.fullmatch(r"wd\d+", label)]
    for label in reversed(labels):
        if label in IGNORED_HOST_LABELS or label in {"http", "https"}:
            continue
        if re.fullmatch(r"[0-9a-f-]{12,}", label):
            continue
        return re.sub(r"[-_]+", " ", label).strip().title()
    return "Unknown company"


def _infer_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    if any(host.endswith(suffix) for suffix in ATS_HOST_SUFFIXES) and parts:
        # These ATS URL patterns start with an organisation slug.
        parts = parts[1:]

    for part in reversed(parts):
        candidate = part.lower()
        if candidate in IGNORED_PATH_SEGMENTS or re.fullmatch(r"\d+", candidate):
            continue
        if re.fullmatch(r"[0-9a-f-]{12,}", candidate) or candidate.startswith("job."):
            continue
        candidate = re.sub(r"(?:-[0-9a-f]{4,}){1,5}$", "", part, flags=re.IGNORECASE)
        candidate = re.sub(r"[-_.]?[0-9a-f]{8,}$", "", candidate, flags=re.IGNORECASE).strip("-_. ")
        if len(candidate) < 4 or not re.search(r"[-_ ]", candidate):
            continue
        return re.sub(r"[-_]+", " ", candidate).strip().title()
    return "Untitled role"


def _fallback_job_from_url(url: str) -> Job:
    """Build a trackable draft when a career page cannot be fetched."""
    return normalize_job_data(
        {"company": _infer_company_from_url(url), "title": _infer_title_from_url(url)},
        url,
    )


def _merge_detected(primary: Job | None, secondary: Job) -> Job:
    """Keep useful primary fields and fill gaps with the secondary fetch."""
    if primary is None:
        return secondary
    title = (
        primary.title if _is_detected(primary.title, "Untitled role")
        else secondary.title if _is_detected(secondary.title, "Untitled role")
        else "Untitled role"
    )
    company = (
        primary.company if _is_detected(primary.company, "Unknown company")
        else secondary.company if _is_detected(secondary.company, "Unknown company")
        else "Unknown company"
    )
    description = secondary.description or primary.description
    location = secondary.location or primary.location
    return normalize_job_data(
        {"title": title, "company": company, "description": description, "location": location},
        primary.url,
    )


def parse_job_from_url(url: str, playwright_page=None) -> Job:
    """Fetch and parse job details, with browser and URL-based fallbacks.

    A valid job URL is still returned as a draft if the host blocks scraping;
    callers can save it and let the user correct details in the dashboard.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Please provide a complete job URL beginning with https:// or http://")

    clean_url = url.strip()
    fallback = _fallback_job_from_url(clean_url)
    if playwright_page is not None:
        try:
            playwright_page.goto(clean_url, wait_until="domcontentloaded", timeout=30_000)
            return _merge_detected(parse_job_html(playwright_page.content(), clean_url), fallback)
        except Exception:
            return fallback

    job: Job | None = None
    try:
        response = requests.get(clean_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        job = parse_job_html(response.text, clean_url)
    except requests.RequestException:
        # 403/429 responses are common on ATS pages and should not stop tracking.
        job = None

    if job is None or _needs_browser_retry(job):
        browser_html = _fetch_html_with_playwright(clean_url)
        if browser_html:
            browser_job = parse_job_html(browser_html, clean_url)
            job = _merge_detected(job, browser_job)

    if job is None:
        return fallback

    # Never store a challenge-page heading as if it were a job title/company.
    return normalize_job_data(
        {
            "title": job.title if _is_detected(job.title, "Untitled role") else fallback.title,
            "company": job.company if _is_detected(job.company, "Unknown company") else fallback.company,
            "description": job.description,
            "location": job.location,
        },
        clean_url,
    )
