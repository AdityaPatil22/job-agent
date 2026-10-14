"""Shared data models used by the CLI, database, and browser workflow."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


ApplicationStatus = Literal[
    "discovered",
    "shortlisted",
    "ready",
    "applied",
    "interview",
    "rejected",
    "withdrawn",
]


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    field: str = ""
    year: int | None = None


class Experience(BaseModel):
    title: str = ""
    company: str = ""
    duration: str = ""
    duration_years: float | None = None
    description: str = ""


class CandidateProfile(BaseModel):
    """Candidate details loaded from config/profile.json."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    current_company: str = ""
    current_title: str = ""
    location: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class Job(BaseModel):
    """Normalized job posting details."""

    id: int | None = None
    company: str = "Unknown company"
    title: str = "Untitled role"
    url: str
    description: str = ""
    location: str = ""
    date_found: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Job URL must be an absolute http:// or https:// URL")
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
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FormGapReport(BaseModel):
    """Summary of fields handled during a form autofill run."""

    filled: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    resume_uploaded: bool = False

    def render(self) -> str:
        lines = ["Form review report:"]
        lines.append(f"  Filled ({len(self.filled)}): {', '.join(self.filled) or 'none'}")
        lines.append(f"  Skipped ({len(self.skipped)}): {', '.join(self.skipped) or 'none'}")
        lines.append(f"  Failed ({len(self.failed)}): {', '.join(self.failed) or 'none'}")
        lines.append(f"  Unknown ({len(self.unknown)}): {', '.join(self.unknown) or 'none'}")
        lines.append(f"  Resume uploaded: {'yes' if self.resume_uploaded else 'no'}")
        return "\n".join(lines)
