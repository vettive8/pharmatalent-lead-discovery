"""People-search client for the DMM step — Prospeo `/search-person`.

Prospeo is the sole people-search provider (see docs/adr-002 for the provider
decision). Prospeo matches by company **name** (it does not filter by job title or
location server-side), so we search the company, rank the returned people by
decision-maker seniority, and keep the top ``limit`` (max 2, per DMM.md). Prospeo signals both "no results" and rate-limits via HTTP 400, so we
branch on its ``error_code``. The fixture path returns a deterministic synthetic
candidate so the pipeline runs end-to-end with zero credits.
"""

from __future__ import annotations

import hashlib
import os

import httpx

from ..config import Settings
from ..logging_setup import get_logger
from ..models import PersonCandidate
from .http import RetryableProviderError, with_retry

log = get_logger("people_search")

# Title words signalling a decision-maker, used to rank Prospeo results (which are
# company-scoped, not title-filtered) down to the most senior candidates.
_LEADER_WORDS = ["head", "chief", "vp", "vice president", "director", "global", "lead", "president"]
_BAND_DOMAIN_WORDS = ["talent", "people", "hr", "human resources", "regulatory",
                      "clinical", "operations", "medical", "affairs"]

# Synthetic name pools for the fixture path (no real PII).
_FIRST = ["Sandra", "Markus", "Elena", "Lukas", "Marie", "Johan", "Anna", "Pieter",
          "Sofia", "Niklas", "Clara", "Tomas", "Eva", "Henrik"]
_LAST = ["Muller", "Frei", "Rossi", "Janssen", "Larsson", "Dubois", "Nowak",
         "Bauer", "Andersen", "Costa", "Vermeulen", "Lindqvist", "Keller", "Moreau"]


class PeopleSearchClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._url = os.getenv("PROSPEO_PEOPLE_SEARCH_URL", "https://api.prospeo.io/search-person")

    def search(self, *, company_name: str, company_domain: str | None,
               titles: list[str], limit: int = 2) -> tuple[list[PersonCandidate], str | None]:
        """One capped people-search for a company.

        Returns ``(candidates, provider)``; provider is None when nothing was found.
        """
        limit = min(limit, 2)  # hard ceiling regardless of caller (DMM.md)
        if self.settings.use_fixtures:
            return self._fixture_candidates(company_name, company_domain, titles, limit), "prospeo"
        candidates = self._prospeo(company_name, company_domain, titles, limit)
        return (candidates, "prospeo") if candidates else ([], None)

    @with_retry
    def _prospeo(self, company_name, company_domain, titles, limit) -> list[PersonCandidate]:
        body = {"filters": {"company": {"names": {"include": [company_name]}}}, "page": 1}
        if company_domain:
            body["filters"]["company"]["websites"] = {"include": [company_domain]}
        headers = {"X-KEY": self.settings.prospeo_api_key, "Content-Type": "application/json"}
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(self._url, headers=headers, json=body)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

        if resp.status_code == 200 and not data.get("error"):
            people = [_parse_prospeo_person(item) for item in data.get("results", [])]
            return _rank_by_seniority(people, titles)[:limit]

        # Prospeo signals no-results and rate-limits via its error_code; the HTTP
        # status has been observed as 400 (live) and is documented as 429.
        code = str(data.get("error_code", "")).lower()
        if "no_result" in code or "no result" in code:
            return []
        # Match "rate limit", "rate_limited", "RATE_LIMITED", etc., plus HTTP 429.
        if ("rate" in code and "limit" in code) or resp.status_code == 429:
            raise RetryableProviderError(f"prospeo rate limited: {code or resp.status_code}")
        resp.raise_for_status()
        raise RuntimeError(f"prospeo error: {resp.status_code} {data.get('error_code')}")

    def _fixture_candidates(self, company_name, company_domain, titles, limit) -> list[PersonCandidate]:
        """Deterministic synthetic decision-maker for credit-free runs (no real PII)."""
        seed = int(hashlib.md5(company_name.encode()).hexdigest(), 16)
        first = _FIRST[seed % len(_FIRST)]
        last = _LAST[(seed // 7) % len(_LAST)]
        slug = f"{first}-{last}-{seed % 1000}".lower()
        cand = PersonCandidate(
            full_name=f"{first} {last}",
            title=titles[0] if titles else "Head of Talent",
            linkedin_url=f"https://www.linkedin.com/in/{slug}/",
            location=None,
            about_snippet="(synthetic fixture candidate — no real PII)",
            provider="prospeo",
            company_domain=company_domain,
        )
        return [cand][:limit]


def _parse_prospeo_person(item: dict) -> PersonCandidate:
    """Map a Prospeo `/search-person` result (nested person/company) to a candidate.
    Email/mobile fields are intentionally ignored — emails are out of scope."""
    p = item.get("person") or {}
    comp = item.get("company") or {}
    loc = p.get("location")
    if isinstance(loc, dict):
        location = ", ".join(x for x in [loc.get("city"), loc.get("state"), loc.get("country")] if x) or None
    else:
        location = str(loc) if loc else None
    full_name = p.get("full_name") or " ".join(x for x in [p.get("first_name"), p.get("last_name")] if x)
    return PersonCandidate(
        full_name=full_name or "",
        title=p.get("current_job_title") or p.get("headline"),
        linkedin_url=p.get("linkedin_url"),
        location=location,
        about_snippet=p.get("headline") or "",
        provider="prospeo",
        company_domain=comp.get("domain") or comp.get("website"),
    )


def _rank_by_seniority(people: list[PersonCandidate], titles: list[str]) -> list[PersonCandidate]:
    """Rank company people most-decision-maker-like first, since Prospeo is
    company-scoped (not title-filtered) — applied before the 2-result cap."""
    band = " ".join(titles).lower()

    def score(c: PersonCandidate) -> int:
        t = (c.title or "").lower()
        s = sum(2 for w in _LEADER_WORDS if w in t)
        s += sum(1 for w in _BAND_DOMAIN_WORDS if w in t and w in band)
        return s

    return sorted(people, key=score, reverse=True)
