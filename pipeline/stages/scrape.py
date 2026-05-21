"""Stage 1-2: scrape open jobs from Apify and persist them to the jobs table.

Every scraped job is stored (active clients included) — the jobs table is the
raw cache. Active-client / ICP filtering happens downstream at the company level.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..clients.apify import ApifyClient
from ..clients.store import Store
from ..config import Settings
from ..logging_setup import get_logger
from ..models import Job

log = get_logger("scrape")


def run_scrape(settings: Settings, store: Store) -> list[Job]:
    raw_rows = ApifyClient(settings).fetch_jobs()

    # Map and dedupe by job id (the actor can surface ats duplicates).
    seen: set[str] = set()
    jobs: list[Job] = []
    for row in raw_rows:
        if not (row.get("id") or row.get("linkedin_id")):
            continue
        job = Job.from_apify(row)
        if job.id in seen:
            continue
        seen.add(job.id)
        jobs.append(job)

    # Live runs trust the actor's 7d window but re-assert it defensively. Fixture
    # runs keep all rows so the demo stays stable as the sample dates age.
    if not settings.use_fixtures:
        cutoff = datetime.now(timezone.utc) - timedelta(days=8)  # 7d + indexing slack
        before = len(jobs)
        jobs = [j for j in jobs if j.date_posted is None or j.date_posted >= cutoff]
        if before != len(jobs):
            log.info("scrape recency filter", extra={"event": "scrape.recency",
                     "dropped": before - len(jobs), "kept": len(jobs)})

    new, total = store.upsert_jobs(jobs)
    log.info("scrape complete", extra={"event": "scrape.done", "scraped": total,
             "new": new, "already_seen": total - new})
    return jobs
