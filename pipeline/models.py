"""Typed data models that flow between stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .normalize import canonicalize_linkedin_url, normalize_company_name, normalize_full_name, root_domain


def parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp, assuming UTC when no offset is present."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _first(seq: list | None) -> str | None:
    return seq[0] if seq else None


@dataclass
class Job:
    """One scraped LinkedIn job (subset of the fantastic.jobs actor output)."""

    id: str
    title: str
    organization: str
    organization_slug: str | None = None
    organization_url: str | None = None
    url: str | None = None
    date_posted: datetime | None = None
    description_text: str | None = None
    seniority: str | None = None
    employment_type: list[str] = field(default_factory=list)
    locations_derived: list[str] = field(default_factory=list)
    city: str | None = None
    region: str | None = None
    country: str | None = None
    linkedin_job_id: str | None = None
    linkedin_org_employees: int | None = None
    linkedin_org_size: str | None = None
    linkedin_org_industry: str | None = None
    linkedin_org_headquarters: str | None = None
    linkedin_org_specialties: list[str] = field(default_factory=list)
    linkedin_org_description: str | None = None
    is_recruitment_agency: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_apify(cls, row: dict) -> "Job":
        return cls(
            id=str(row.get("id") or row.get("linkedin_id")),
            title=row.get("title") or "",
            organization=row.get("organization") or "",
            organization_slug=row.get("linkedin_org_slug"),
            organization_url=row.get("organization_url") or row.get("linkedin_org_url"),
            url=row.get("url"),
            date_posted=parse_dt(row.get("date_posted")),
            description_text=row.get("description_text"),
            seniority=row.get("seniority"),
            employment_type=list(row.get("employment_type") or []),
            locations_derived=list(row.get("locations_derived") or []),
            city=_first(row.get("cities_derived")),
            region=_first(row.get("regions_derived")),
            country=_first(row.get("countries_derived")),
            linkedin_job_id=row.get("linkedin_id"),
            linkedin_org_employees=row.get("linkedin_org_employees"),
            linkedin_org_size=row.get("linkedin_org_size"),
            linkedin_org_industry=row.get("linkedin_org_industry"),
            linkedin_org_headquarters=row.get("linkedin_org_headquarters"),
            linkedin_org_specialties=list(row.get("linkedin_org_specialties") or []),
            linkedin_org_description=row.get("linkedin_org_description"),
            is_recruitment_agency=bool(row.get("linkedin_org_recruitment_agency_derived")),
            raw=row,
        )

    @property
    def company_key(self) -> str:
        """Stable per-company key: prefer the LinkedIn slug, else normalized name."""
        return (self.organization_slug or normalize_company_name(self.organization)).lower()

    def all_countries(self) -> list[str]:
        """Every country this job is derived to, falling back to the single one."""
        derived = self.raw.get("countries_derived")
        if derived:
            return list(derived)
        return [self.country] if self.country else []


@dataclass
class Company:
    """A posting company aggregated from one or more of its scraped jobs."""

    linkedin_slug: str
    name: str
    normalized_name: str
    employees: int | None = None
    industry: str | None = None
    headquarters: str | None = None
    description: str | None = None
    specialties: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    domain: str | None = None
    jobs: list[Job] = field(default_factory=list)

    # filled after persistence / by the fit-check stage
    id: str | None = None                # companies.id once upserted
    size_band: str | None = None
    decision: str | None = None          # 'fit' | 'not_fit'
    rationale: str | None = None
    confidence: str | None = None
    fit_score: int | None = None
    checked_at: datetime | None = None

    @classmethod
    def from_jobs(cls, jobs: list[Job]) -> "Company":
        """Build a company from the jobs that reference it (most-complete first)."""
        primary = max(jobs, key=lambda j: j.linkedin_org_employees or 0)
        countries, cities = [], []
        for j in jobs:
            for c in j.all_countries():
                if c and c not in countries:
                    countries.append(c)
            if j.city and j.city not in cities:
                cities.append(j.city)
        return cls(
            linkedin_slug=primary.company_key,
            name=primary.organization,
            normalized_name=normalize_company_name(primary.organization),
            employees=primary.linkedin_org_employees,
            industry=primary.linkedin_org_industry,
            headquarters=primary.linkedin_org_headquarters,
            description=primary.linkedin_org_description,
            specialties=primary.linkedin_org_specialties,
            countries=countries,
            cities=cities,
            domain=None,  # the real website domain is resolved during fit-check
            jobs=list(jobs),
        )


@dataclass
class FitDecision:
    decision: str               # 'fit' | 'not_fit'
    rationale: str
    confidence: str             # 'high' | 'medium' | 'low'
    fit_score: int | None = None
    domain: str | None = None
    source: str = "llm_web"     # 'llm_web' | 'offline_heuristic' | 'rule'
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PersonCandidate:
    full_name: str
    title: str | None
    linkedin_url: str | None
    location: str | None = None
    about_snippet: str | None = None
    provider: str = "ai_ark"          # 'ai_ark' | 'prospeo'
    company_domain: str | None = None

    @classmethod
    def from_result(cls, row: dict, provider: str) -> "PersonCandidate":
        name = row.get("full_name") or " ".join(
            x for x in (row.get("first_name"), row.get("last_name")) if x
        )
        return cls(
            full_name=name or "",
            title=row.get("title"),
            linkedin_url=row.get("linkedin_url"),
            location=row.get("location"),
            about_snippet=row.get("about_snippet") or row.get("about") or "",
            provider=provider,
            company_domain=row.get("company_domain"),
        )

    def dedup_key(self, company_domain: str | None) -> str:
        canon = canonicalize_linkedin_url(self.linkedin_url)
        if canon:
            return canon
        domain = self.company_domain or company_domain or ""
        return f"{normalize_full_name(self.full_name)}|{root_domain(domain)}"


@dataclass
class ValidationResult:
    decision: str   # 'yes' | 'no'
    reason: str
