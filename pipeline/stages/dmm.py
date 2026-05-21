"""Stage 6: decision-maker mapping (DMM).

For each ICP-fit company: pick the size-band target titles (DMM.md), then run a
geographic cascade (city -> country -> EU region -> worldwide) of capped
people-search calls, stopping at the first level that returns anyone.

Budget protections (the operational-thinking axis):
* Max 2 results per call (enforced in the client too).
* One call per cascade level, passing the whole band title list, stopping on the
  first hit — fewer credits than one call per title. (Documented reinterpretation
  of "stop on first hit per (company, title)" — see README/ADR.)
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


def _cascade_levels(company: Company, band_label: str) -> list[tuple[str, str]]:
    """(cascade_level, location) pairs in priority order for this company."""
    country = company.countries[0] if company.countries else None
    city = company.cities[0] if company.cities else None
    levels: list[tuple[str, str]] = []
    if city:
        levels.append(("city", f"{city}, {country}" if country else city))
    if country:
        levels.append(("country", country))
    region = icp.eu_region_for(country)
    if region:
        levels.append(("region", region))
    # Worldwide only for the smallest band, where a single global owner is plausible.
    if band_label == "50-200":
        levels.append(("worldwide", "worldwide"))
    return levels


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

        hit = _search_cascade(client, company, titles, band_label)
        if hit is None:
            store.record_dmm_query(company.id, primary_title, None, None, 0, "no_candidate")
            result.no_candidate.append(company.name)
            log.info("dmm no candidate", extra={"event": "dmm.no_candidate", "company": company.name})
            continue

        candidates, cascade_level, provider = hit
        store.record_dmm_query(company.id, primary_title, cascade_level, provider,
                               len(candidates), "hit")
        result.credits_spent += len(candidates)
        result.hits.append(CompanyCandidates(company, candidates, cascade_level, provider, primary_title))
        log.info("dmm hit", extra={"event": "dmm.hit", "company": company.name,
                 "cascade": cascade_level, "provider": provider, "candidates": len(candidates)})

    log.info("dmm complete", extra={"event": "dmm.done", "hits": len(result.hits),
             "no_candidate": len(result.no_candidate),
             "skipped": len(result.skipped_already_queried),
             "credits_spent": result.credits_spent})
    return result


def _search_cascade(client: PeopleSearchClient, company: Company, titles: list[str],
                    band_label: str) -> tuple[list[PersonCandidate], str, str] | None:
    for cascade_level, location in _cascade_levels(company, band_label):
        candidates, provider = client.search(
            company_name=company.name, company_domain=company.domain, titles=titles,
            location=location, cascade_level=cascade_level, limit=2,
        )
        if candidates and provider:
            return candidates, cascade_level, provider
    return None
