"""Stage 6: decision-maker mapping (DMM).

For each ICP-fit company we pick the size-band target titles (DMM.md) and run one
capped Prospeo people-search. Prospeo matches by company name (it does not take a
location parameter), so the DMM.md geographic cascade (city→country→region→
worldwide) does not apply to it — we search the company once and record the scope
as ``company``. See docs/adr-002.

Budget protections (the operational-thinking axis):
* Max 2 results per call (enforced in the client too).
* The (company, primary-title) pair is recorded in dmm_queries; a rerun checks
  that guard first and never re-spends a credit on a company already resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import icp
from ..clients.people_search import PeopleSearchClient
from ..clients.store import Store
from ..config import Settings
from ..logging_setup import get_logger
from ..models import Company, PersonCandidate

log = get_logger("dmm")

# Prospeo is company-scoped, so the recorded cascade level is always "company".
CASCADE_LEVEL = "company"


@dataclass
class CompanyCandidates:
    company: Company
    candidates: list[PersonCandidate]
    cascade_level: str
    provider: str
    target_title: str


@dataclass
class DMMResult:
    hits: list[CompanyCandidates] = field(default_factory=list)
    no_candidate: list[str] = field(default_factory=list)
    skipped_already_queried: list[str] = field(default_factory=list)
    credits_spent: int = 0


def run_dmm(fit_companies: list[Company], settings: Settings, store: Store) -> DMMResult:
    client = PeopleSearchClient(settings)
    result = DMMResult()

    for company in fit_companies:
        band = icp.size_band_for(company.employees)
        if band is None:                       # defensive; fit implies a band
            result.no_candidate.append(company.name)
            continue
        band_label, titles = band
        company.size_band = band_label
        primary_title = titles[0]

        # Credit guard: skip companies already resolved on a prior run.
        seen = store.dmm_query_seen(company.id, primary_title)
        if seen is not None:
            result.skipped_already_queried.append(company.name)
            log.info("dmm skip (already queried)", extra={"event": "dmm.skip",
                     "company": company.name, "prior_outcome": seen["outcome"]})
            continue

        candidates, provider = client.search(
            company_name=company.name, company_domain=company.domain, titles=titles, limit=2,
        )
        if not candidates or not provider:
            store.record_dmm_query(company.id, primary_title, None, None, 0, "no_candidate")
            result.no_candidate.append(company.name)
            log.info("dmm no candidate", extra={"event": "dmm.no_candidate", "company": company.name})
            continue

        store.record_dmm_query(company.id, primary_title, CASCADE_LEVEL, provider,
                               len(candidates), "hit")
        result.credits_spent += len(candidates)
        result.hits.append(CompanyCandidates(company, candidates, CASCADE_LEVEL, provider, primary_title))
        log.info("dmm hit", extra={"event": "dmm.hit", "company": company.name,
                 "provider": provider, "candidates": len(candidates)})

    log.info("dmm complete", extra={"event": "dmm.done", "hits": len(result.hits),
             "no_candidate": len(result.no_candidate),
             "skipped": len(result.skipped_already_queried),
             "credits_spent": result.credits_spent})
    return result
