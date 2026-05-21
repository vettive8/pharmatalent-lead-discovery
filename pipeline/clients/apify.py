"""Apify client — fantastic.jobs LinkedIn Jobs API actor (vIGxjRrHqDTPuE6M4).

The actor is a queryable index (not a per-run scraper), so the sync endpoint
returns dataset items in seconds. We use ``run-sync-get-dataset-items``.
"""

from __future__ import annotations

import json

import httpx

from .. import icp
from ..config import Settings
from ..logging_setup import get_logger
from .http import with_retry

log = get_logger("apify")

# Verified against the live actor Input schema (advanced-linkedin-job-search-api
# by fantastic-jobs): the recency filter is `timeRange` (enum 1h/24h/7d/6m) and
# the result cap is `limit` (integer, max 5000). Confirmed 2026-05-21.
TIME_RANGE_PARAM = "timeRange"


def build_actor_input(settings: Settings) -> dict:
    """Translate the ICP scrape parameters (ICP.md Half 1 + TOOLS.md §1) into the
    actor's input. One scrape, ICP-shaped; size filtering happens after the scrape
    so active-client mega-pharma stay visible for the P2 hiring-signal side-output.
    """
    return {
        "titleSearch": icp.TITLE_SEARCH,                 # OR-combined target titles
        "locationSearch": settings.scrape_locations,     # English EU location names
        "EmploymentTypeFilter": icp.EMPLOYMENT_TYPES,    # FULL_TIME, CONTRACTOR
        TIME_RANGE_PARAM: settings.scrape_time_range,    # last 7 days
        "removeAgency": True,                            # drop staffing/recruiting
        "descriptionType": "text",                       # cheaper than html
        "includeAi": False,                              # AI enrichment is tech-only
        "limit": settings.scrape_max_items,              # actor's result cap (max 5000)
    }


class ApifyClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base = "https://api.apify.com/v2"

    def fetch_jobs(self) -> list[dict]:
        """Return raw job rows — from the live actor, or the bundled fixture."""
        if self.settings.use_fixtures:
            path = self.settings.fixtures_dir / "sample_apify_jobs.json"
            log.info("apify fixture load", extra={"event": "scrape.fixture", "path": str(path)})
            return json.loads(path.read_text(encoding="utf-8"))
        return self._run_actor(build_actor_input(self.settings))

    @with_retry
    def _run_actor(self, actor_input: dict) -> list[dict]:
        url = f"{self._base}/acts/{self.settings.apify_actor_id}/run-sync-get-dataset-items"
        log.info("apify run", extra={"event": "scrape.run", "actor": self.settings.apify_actor_id,
                                     "locations": len(self.settings.scrape_locations),
                                     "max_items": self.settings.scrape_max_items})
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, params={"token": self.settings.apify_token}, json=actor_input)
            resp.raise_for_status()
            data = resp.json()
        # The sync endpoint returns a JSON array of dataset items.
        return data if isinstance(data, list) else data.get("items", [])
