"""Pipeline orchestrator — wires the stages together and writes the artifacts."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import __version__
from .clients.store import Store, make_store
from .config import ROOT_DIR, Settings
from .logging_setup import get_logger
from .outputs import write_active_client_hiring_csv, write_icp_fit_decisions_csv, write_run_summary
from .stages.dmm import run_dmm
from .stages.exclude import run_exclude
from .stages.fitcheck import run_fitcheck
from .stages.scrape import run_scrape
from .stages.validate import run_validate

log = get_logger("run")


def _rel(path) -> str:
    """Repo-relative POSIX path for a committed artifact reference."""
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return str(path)


def run_pipeline(settings: Settings, store: Store | None = None) -> dict:
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    mode = "fixtures" if settings.use_fixtures else "live"
    log.info("pipeline start", extra={"event": "pipeline.start", "mode": mode,
             "version": __version__, "store": "memory" if settings.no_db or not settings.supabase_db_url else "postgres",
             "llm": "offline" if settings.llm_offline else "openrouter"})

    owns_store = store is None
    store = store or make_store(settings)
    store.bootstrap()

    try:
        # 1-2  scrape -> jobs
        jobs = run_scrape(settings, store)
        # 3     exclude active clients + dedupe into companies
        excl = run_exclude(jobs, started)
        # 4-5  ICP fit-check -> companies
        fit = run_fitcheck(excl.companies, settings, store)
        # 6     decision-maker mapping
        dmm = run_dmm(fit.fit_companies, settings, store)
        # 7-8  validate -> contacts (+ job links)
        val = run_validate(dmm.hits, settings, store)

        # Artifacts
        active_csv = write_active_client_hiring_csv(excl.active_client_rows, settings.output_dir)
        fit_csv = write_icp_fit_decisions_csv(fit.decision_rows, settings.output_dir)

        db_counts = store.counts()
        summary = {
            "version": __version__,
            "mode": mode,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.monotonic() - t0, 2),
            "stages": {
                "scrape": {"jobs_scraped": len(jobs)},
                "exclude": {
                    "companies_to_evaluate": len(excl.companies),
                    "active_client_job_rows": len(excl.active_client_rows),
                    "dropped_agencies": len(excl.dropped_agencies),
                },
                "fit_check": {
                    "fit": len(fit.fit_companies),
                    "not_fit": len(fit.decision_rows) - len(fit.fit_companies),
                    "cached": fit.cached,
                },
                "dmm": {
                    "hits": len(dmm.hits),
                    "no_candidate": len(dmm.no_candidate),
                    "skipped_already_queried": len(dmm.skipped_already_queried),
                    "prospeo_search_calls": dmm.search_calls,      # ~1 Prospeo credit each
                    "candidates_returned": dmm.candidates_returned,
                },
                "validate": {
                    "contacts_created": val.contacts_created,
                    "contacts_existing": val.contacts_existing,
                    "dropped": len(val.dropped),
                    "validations_run": val.validations_run,
                    "job_links": val.job_links,
                },
            },
            "database_totals": db_counts,
            "artifacts": {
                "active_client_hiring_csv": _rel(active_csv),
                "icp_fit_decisions_csv": _rel(fit_csv),
            },
        }
        summary_path = write_run_summary(summary, settings.output_dir)
        log.info("pipeline complete", extra={"event": "pipeline.done", **summary["stages"],
                 "database_totals": db_counts, "summary": str(summary_path)})
        return summary
    finally:
        if owns_store:
            store.close()
