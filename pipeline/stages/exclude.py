"""Stage 3: exclude active clients, drop agencies, dedupe into companies.

Runs BEFORE the ICP fit-check (a hard ordering requirement). Active clients are
routed to the P2 "active client is hiring" side-output and never reach the
companies/contacts tables. Recruitment agencies are a structural ICP
disqualifier we can detect from the scrape data alone, so we drop them here too
rather than spend an LLM fit-check on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..logging_setup import get_logger
from ..matching import ActiveClientMatcher
from ..models import Company, Job

log = get_logger("exclude")


@dataclass
class ExcludeResult:
    companies: list[Company] = field(default_factory=list)          # continue to fit-check
    active_client_rows: list[dict] = field(default_factory=list)    # P2 CSV rows (per job)
    dropped_agencies: list[str] = field(default_factory=list)


def _group_by_company(jobs: list[Job]) -> dict[str, list[Job]]:
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.company_key, []).append(job)
    return groups


def run_exclude(jobs: list[Job], detected_at: datetime) -> ExcludeResult:
    matcher = ActiveClientMatcher()
    result = ExcludeResult()

    for key, group in _group_by_company(jobs).items():
        company = Company.from_jobs(group)
        match = matcher.match(name=company.name, slug=company.linkedin_slug, domain=company.domain)

        if match:
            for job in group:
                result.active_client_rows.append({
                    "client_name": match.client_name,
                    "matched_company_name_raw": job.organization,
                    "scraped_job_title": job.title,
                    "scraped_job_url": job.url or "",
                    "location": (job.locations_derived[0] if job.locations_derived else ""),
                    "posted_at": job.date_posted.isoformat() if job.date_posted else "",
                    "detected_at": detected_at.isoformat(),
                })
            log.info("excluded active client", extra={"event": "exclude.active_client",
                     "company": company.name, "matched": match.client_name,
                     "method": match.method, "jobs": len(group)})
            continue

        if any(j.is_recruitment_agency for j in group):
            result.dropped_agencies.append(company.name)
            log.info("dropped recruitment agency", extra={"event": "exclude.agency",
                     "company": company.name})
            continue

        result.companies.append(company)

    log.info("exclude complete", extra={"event": "exclude.done",
             "companies_to_evaluate": len(result.companies),
             "active_client_companies": len({r['client_name'] for r in result.active_client_rows}),
             "active_client_job_rows": len(result.active_client_rows),
             "dropped_agencies": len(result.dropped_agencies)})
    return result
