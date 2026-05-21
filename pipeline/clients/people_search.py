"""People-search client for the DMM step — AI Ark primary, Prospeo fallback.

Budget discipline (DMM.md / TOOLS.md): every call is capped at ``limit`` (max 2)
results. The cascade, stop-on-first-hit, and (company, title) credit guard live in
the DMM stage; this client just performs one capped search at one cascade level
and reports which provider produced the hit.

NOTE (live verification): the exact AI Ark / Prospeo request paths and field names
are confirmed against each provider's live API + docs when real credentials are
first supplied (see README "Live-run verification"). Base URLs and paths are
env-overridable so a correction is configuration, not a code change. The fixture
path below fully exercises the cascade/cap/dedup logic with zero credits.
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

# Title words that signal a decision-maker, used to rank Prospeo results (which
# match by company and ignore title) down to the most senior candidates.
_LEADER_WORDS = ["head", "chief", "vp", "vice president", "director", "global", "lead", "president"]

_FIRST = ["Sandra", "Markus", "Elena", "Lukas", "Marie", "Johan", "Anna", "Pieter",
          "Sofia", "Niklas", "Clara", "Tomas", "Eva", "Henrik"]
_LAST = ["Muller", "Frei", "Rossi", "Janssen", "Larsson", "Dubois", "Nowak",
         "Bauer", "Andersen", "Costa", "Vermeulen", "Lindqvist", "Keller", "Moreau"]


class PeopleSearchClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ai_ark_path = os.getenv("AI_ARK_PEOPLE_SEARCH_PATH", "/api/developer-portal/v1/people")
        self._prospeo_url = os.getenv("PROSPEO_PEOPLE_SEARCH_URL",
                                      "https://api.prospeo.io/search-person")

    def search(self, *, company_name: str, company_domain: str | None, titles: list[str],
               location: str, cascade_level: str, limit: int = 2) -> tuple[list[PersonCandidate], str | None]:
        """One capped people-search at one cascade level.

        Returns ``(candidates, provider)``; provider is None when nothing was found
        across the providers that are configured.
        """
        limit = min(limit, 2)  # hard ceiling regardless of caller (DMM.md)

        if self.settings.use_fixtures:
            return self._fixture_candidates(company_name, company_domain, titles, location, limit), "ai_ark"

        # Live: AI Ark first.
        if self.settings.ai_ark_token:
            cands = self._ai_ark(company_name, company_domain, titles, location, limit)
            if cands:
                return cands, "ai_ark"
            log.info("ai_ark empty, considering fallback", extra={"event": "dmm.ai_ark_empty",
                     "company": company_name, "cascade": cascade_level})

        # Fallback: Prospeo.
        if self.settings.prospeo_enabled:
            cands = self._prospeo(company_name, company_domain, titles, location, limit)
            if cands:
                return cands, "prospeo"

        return [], None

    # -- AI Ark -------------------------------------------------------------
    @with_retry
    def _ai_ark(self, company_name, company_domain, titles, location, limit) -> list[PersonCandidate]:
        """AI Ark POST /api/developer-portal/v1/people (X-TOKEN auth). Built to the
        documented request/response schema (docs.ai-ark.com). One returned person =
        one credit, so `size` is the 2-result cap."""
        url = self.settings.ai_ark_base_url.rstrip("/") + self._ai_ark_path
        account = {"name": {"any": {"include": {"mode": "SMART", "content": [company_name]}}}}
        if company_domain:
            account["domain"] = {"any": {"include": [company_domain]}}
        contact = {"experience": {"current": {"title": {"any": {"include": {"mode": "SMART", "content": titles}}}}}}
        if location and location not in ("", "worldwide"):
            contact["location"] = {"any": {"include": [location]}}
        body = {"account": account, "contact": contact, "page": 0, "size": limit}
        headers = {"X-TOKEN": self.settings.ai_ark_token, "Content-Type": "application/json"}
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("content", []) if isinstance(data, dict) else []
        return [_parse_ai_ark_person(x) for x in results[:limit]]

    # -- Prospeo ------------------------------------------------------------
    @with_retry
    def _prospeo(self, company_name, company_domain, titles, location, limit) -> list[PersonCandidate]:
        """Prospeo /search-person matches by company name (location is ignored), so
        we search the company, then rank the returned people by decision-maker
        seniority and keep the top `limit`. Prospeo returns HTTP 400 for both
        NO_RESULTS (=> no candidates) and rate limits (=> retry)."""
        body = {"filters": {"company": {"names": {"include": [company_name]}}}, "page": 1}
        if company_domain:
            body["filters"]["company"]["websites"] = {"include": [company_domain]}
        headers = {"X-KEY": self.settings.prospeo_api_key, "Content-Type": "application/json"}
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(self._prospeo_url, headers=headers, json=body)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

        if resp.status_code == 200 and not data.get("error"):
            people = [_parse_prospeo_person(item) for item in data.get("results", [])]
            return _rank_by_seniority(people, titles)[:limit]

        code = str(data.get("error_code", "")).lower()
        if "no_result" in code or "no result" in code:
            return []
        if "rate limit" in code or resp.status_code == 429:
            raise RetryableProviderError(f"prospeo rate limited: {code}")
        resp.raise_for_status()
        raise RuntimeError(f"prospeo error: {resp.status_code} {data.get('error_code')}")

    # -- fixtures -----------------------------------------------------------
    def _fixture_candidates(self, company_name, company_domain, titles, location, limit) -> list[PersonCandidate]:
        """Deterministic synthetic decision-maker for credit-free demos.

        Returns one clearly-synthetic candidate holding the band's top-priority
        (decision-maker) title, so the cascade stops at the first level and the
        validation + contacts flow runs end to end without spending credits.
        """
        seed = int(hashlib.md5(company_name.encode()).hexdigest(), 16)
        first = _FIRST[seed % len(_FIRST)]
        last = _LAST[(seed // 7) % len(_LAST)]
        name = f"{first} {last}"
        slug = f"{first}-{last}-{seed % 1000}".lower()
        cand = PersonCandidate(
            full_name=name,
            title=titles[0] if titles else "Head of Talent",
            linkedin_url=f"https://www.linkedin.com/in/{slug}/",
            location=location,
            about_snippet="(synthetic fixture candidate — no real PII)",
            provider="ai_ark",
            company_domain=company_domain,
        )
        return [cand][:limit]


def _parse_ai_ark_person(item: dict) -> PersonCandidate:
    """Map an AI Ark /v1/people `content[]` record to a candidate."""
    prof = item.get("profile") or {}
    loc = item.get("location") or {}
    location = loc.get("short") or ", ".join(
        x for x in [loc.get("city"), loc.get("state"), loc.get("country")] if x) or None
    full_name = prof.get("full_name") or " ".join(
        x for x in [prof.get("first_name"), prof.get("last_name")] if x)
    return PersonCandidate(
        full_name=full_name or "",
        title=prof.get("title") or prof.get("headline"),
        linkedin_url=(item.get("link") or {}).get("linkedin"),
        location=location,
        about_snippet=prof.get("headline") or "",
        provider="ai_ark",
        company_domain=None,
    )


def _parse_prospeo_person(item: dict) -> PersonCandidate:
    """Map a Prospeo /search-person result (nested person/company) to a candidate.
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


_BAND_DOMAIN_WORDS = ["talent", "people", "hr", "human resources", "regulatory",
                      "clinical", "operations", "medical", "affairs"]


def _rank_by_seniority(people: list[PersonCandidate], titles: list[str]) -> list[PersonCandidate]:
    """Rank company people most-decision-maker-like first (Prospeo ignores title,
    so we rank client-side before applying the 2-result cap)."""
    band = " ".join(titles).lower()

    def score(c: PersonCandidate) -> int:
        t = (c.title or "").lower()
        s = sum(2 for w in _LEADER_WORDS if w in t)
        s += sum(1 for w in _BAND_DOMAIN_WORDS if w in t and w in band)
        return s

    return sorted(people, key=score, reverse=True)
