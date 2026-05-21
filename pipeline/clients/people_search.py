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
from .http import with_retry

log = get_logger("people_search")

_FIRST = ["Sandra", "Markus", "Elena", "Lukas", "Marie", "Johan", "Anna", "Pieter",
          "Sofia", "Niklas", "Clara", "Tomas", "Eva", "Henrik"]
_LAST = ["Muller", "Frei", "Rossi", "Janssen", "Larsson", "Dubois", "Nowak",
         "Bauer", "Andersen", "Costa", "Vermeulen", "Lindqvist", "Keller", "Moreau"]


class PeopleSearchClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ai_ark_path = os.getenv("AI_ARK_PEOPLE_SEARCH_PATH", "/people_search")
        self._prospeo_url = os.getenv("PROSPEO_PEOPLE_SEARCH_URL",
                                      "https://api.prospeo.io/linkedin-people-search")

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
        url = self.settings.ai_ark_base_url.rstrip("/") + self._ai_ark_path
        payload = {
            "company_name": company_name,
            "company_domain": company_domain,
            "target_titles": titles,
            "location": location,
            "limit": limit,
        }
        headers = {"Authorization": f"Bearer {self.settings.ai_ark_token}"}
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results", data if isinstance(data, list) else [])
        return [PersonCandidate.from_result(r, "ai_ark") for r in results[:limit]]

    # -- Prospeo ------------------------------------------------------------
    @with_retry
    def _prospeo(self, company_name, company_domain, titles, location, limit) -> list[PersonCandidate]:
        payload = {
            "company": company_name,
            "company_domain": company_domain,
            "job_title": titles,
            "location": location,
            "limit": limit,
        }
        headers = {"X-KEY": self.settings.prospeo_api_key, "Content-Type": "application/json"}
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(self._prospeo_url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("response", {}).get("results") if isinstance(data.get("response"), dict) else None
        results = results or data.get("results", [])
        return [PersonCandidate.from_result(r, "prospeo") for r in results[:limit]]

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
