"""Shared data models used by the CLI, database, and browser workflow."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


ApplicationStatus = Literal[
    "discovered",
    "shortlisted",
    "ready",
    "applied",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
]


class Education(BaseModel):
    model_config = ConfigDict(extra="allow")

    school: str = ""
    degree: str = ""
    field: str = ""
    year: int | None = None
    start_year: int | None = None
    gpa: str = ""


class Experience(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = ""
    company: str = ""
    duration: str = ""
    duration_years: float | None = None
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    achievements: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    """Candidate profile loaded from config/profile.json.

    Unknown keys are deliberately retained so adding a profile field does not
    silently delete it during validation and prevent the autofiller using it.
    """

    model_config = ConfigDict(extra="allow")

    name: str = ""
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""

    email: str = ""
    phone: str = ""
    country_code: str = ""

    address: str = ""
    address_line_2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    zip: str = ""
    country: str = ""
    location: str = ""

    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""

    current_company: str = ""
    current_title: str = ""
    employment_type: str = ""
    years_experience: float | None = None

    work_authorization: str | bool | None = ""
    visa_sponsorship: str | bool | None = ""
    requires_visa_sponsorship: str | bool | None = None

    expected_salary: str | int | float = ""
    salary_currency: str = ""
    notice_period: str = ""

    relocation: str | bool | None = ""
    willing_to_relocate: str | bool | None = None
    referral_source: str = ""

    resume_path: str = ""
    cover_letter_path: str = ""

    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[Any] = Field(default_factory=list)
    projects: list[Any] = Field(default_factory=list)


class Job(BaseModel):
    """Normalized job posting details."""

    id: int | None = None
    company: str = "Unknown company"
    title: str = "Untitled role"
    url: str
    description: str = ""
    location: str = ""
    date_found: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "Job URL must be an absolute http:// or https:// URL"
            )

        return value


class Application(BaseModel):
    """Application tracking record associated with a job."""

    id: int | None = None
    job_id: int
    status: ApplicationStatus = "discovered"
    resume_used: str = ""
    applied_date: date | None = None
    next_action: date | None = None
    notes: str = ""
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class FormGapReport(BaseModel):
    """Summary of fields handled during a form autofill run."""

    application_form_detected: bool = True
    filled: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    resume_uploaded: bool = False

    def render(self) -> str:
        lines = ["Form review report:"]
        lines.append(
            f"  Application form detected: {'yes' if self.application_form_detected else 'no'}"
        )
        lines.append(
            f"  Filled ({len(self.filled)}): "
            f"{', '.join(self.filled) or 'none'}"
        )
        lines.append(
            f"  Skipped ({len(self.skipped)}): "
            f"{', '.join(self.skipped) or 'none'}"
        )
        lines.append(
            f"  Failed ({len(self.failed)}): "
            f"{', '.join(self.failed) or 'none'}"
        )
        lines.append(
            f"  Unknown ({len(self.unknown)}): "
            f"{', '.join(self.unknown) or 'none'}"
        )
        lines.append(
            f"  Resume uploaded: "
            f"{'yes' if self.resume_uploaded else 'no'}"
        )

        return "\n".join(lines)