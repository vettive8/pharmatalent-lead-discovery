"""Stage 7-8: LLM hiring-manager validation, then persist validated contacts.

Every candidate person from the DMM step must pass LLM validation before it is
written to contacts. A candidate is validated against each scraped job at the
company; it is kept if it plausibly owns at least one of those jobs, and is
linked (contact_jobs) to every job it passed for — so one person surfaced by
several jobs at the same company yields a single contact row (DMM.md dedup).

Drops are always logged with a reason (a hard requirement). Offline mode (no
OPENROUTER_API_KEY) uses a clearly-labelled heuristic mirroring the prompt rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..clients.openrouter import OpenRouterClient
from ..clients.store import Store
from ..config import Settings
from ..logging_setup import get_logger
from ..models import Company, PersonCandidate, ValidationResult
from .dmm import CompanyCandidates

log = get_logger("validate")

_LEADER = ["head", "chief", "vp", "vice president", "director", "global head", "lead", "principal"]
_TALENT = ["talent", "people", "hr", "human resources", "recruit"]
_JUNIOR = ["associate", "assistant", "coordinator", "specialist", "junior", " ii", "intern"]


@dataclass
class ValidateResult:
    contacts_created: int = 0
    contacts_existing: int = 0
    job_links: int = 0
    dropped: list[dict] = field(default_factory=list)   # {company, person, reason}
    validations_run: int = 0


def run_validate(hits: list[CompanyCandidates], settings: Settings, store: Store) -> ValidateResult:
    client = None if settings.llm_offline else OpenRouterClient(settings)
    result = ValidateResult()
    now = datetime.now(timezone.utc)
    # Cache decisions within a run by (person, job) per the fixture's suggestion.
    cache: dict[tuple[str, str], ValidationResult] = {}

    for hit in hits:
        company = hit.company
        for candidate in hit.candidates:
            passed_jobs = []
            last_reason = "no plausible decision-maker match"
            for job in company.jobs:
                key = ((candidate.linkedin_url or candidate.full_name), job.id)
                if key not in cache:
                    cache[key] = _validate_one(client, company, candidate, job)
                    result.validations_run += 1
                verdict = cache[key]
                if verdict.decision == "yes":
                    passed_jobs.append(job)
                else:
                    last_reason = verdict.reason

            if not passed_jobs:
                result.dropped.append({"company": company.name, "person": candidate.full_name,
                                       "title": candidate.title, "reason": last_reason})
                log.info("contact dropped", extra={"event": "validate.drop", "company": company.name,
                         "person": candidate.full_name, "title": candidate.title, "reason": last_reason})
                continue

            # Keep: persist the contact and link every job it passed for.
            keep_reason = next((cache[((candidate.linkedin_url or candidate.full_name), j.id)].reason
                                for j in passed_jobs), "validated")
            dedup_key = candidate.dedup_key(company.domain)
            contact_id, created = store.upsert_contact(
                company_id=company.id, candidate=candidate, dedup_key=dedup_key,
                cascade_level=hit.cascade_level, target_title=hit.target_title,
                validation=ValidationResult("yes", keep_reason), found_at=now, validated_at=now,
            )
            for job in passed_jobs:
                store.link_contact_job(contact_id, job.id)
                result.job_links += 1
            if created:
                result.contacts_created += 1
            else:
                result.contacts_existing += 1
            log.info("contact kept", extra={"event": "validate.keep", "company": company.name,
                     "person": candidate.full_name, "title": candidate.title,
                     "jobs_linked": len(passed_jobs), "is_new": created})

    log.info("validate complete", extra={"event": "validate.done",
             "contacts_created": result.contacts_created, "contacts_existing": result.contacts_existing,
             "dropped": len(result.dropped), "job_links": result.job_links})
    return result


def _validate_one(client: OpenRouterClient | None, company: Company, candidate: PersonCandidate, job) -> ValidationResult:
    payload = {
        "scraped_job_title": job.title,
        "scraped_job_description_snippet": (job.description_text or "")[:500],
        "scraped_job_location": (job.locations_derived[0] if job.locations_derived else ""),
        "person_full_name": candidate.full_name,
        "person_title": candidate.title or "",
        "person_about_snippet": candidate.about_snippet or "",
        "person_location": candidate.location or "",
        "company_name": company.name,
        "company_size_band": company.size_band or "",
    }
    if client is None:
        return _offline_validate(job.title, candidate.title or "", company.size_band or "")
    try:
        return client.validate(payload)
    except Exception as exc:  # noqa: BLE001 - boundary: never abort the run on one call
        log.warning("validation llm failed, using heuristic", extra={"event": "validate.llm_error",
                    "person": candidate.full_name, "error": str(exc)})
        return _offline_validate(job.title, candidate.title or "", company.size_band or "")


def _offline_validate(job_title: str, person_title: str, size_band: str) -> ValidationResult:
    """Heuristic mirror of the validation prompt rules (offline/fixture mode)."""
    pt = person_title.lower()
    is_leader = any(k in pt for k in _LEADER)
    is_junior = any(k in pt for k in _JUNIOR)

    if is_junior and not is_leader:
        return ValidationResult("no", f"[offline] '{person_title}' is a peer/junior-level role, "
                                       "not the decision-maker for this requisition.")
    if is_leader:
        return ValidationResult("yes", f"[offline] '{person_title}' is a function head/leader who "
                                        "plausibly owns this hiring decision.")
    if any(t in pt for t in _TALENT):
        if size_band == "50-200":
            return ValidationResult("yes", "[offline] Talent leader at a sub-200 company owns hiring end-to-end.")
        return ValidationResult("no", "[offline] Generic talent role is outranked by the functional head at this size.")
    return ValidationResult("no", f"[offline] '{person_title}' does not indicate decision-maker authority.")
