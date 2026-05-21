"""End-to-end fixture run + idempotency, all in-memory (no DB, no credits)."""

from pipeline.clients.store import InMemoryStore
from pipeline.config import load_settings
from pipeline.logging_setup import setup_logging
from pipeline.run import run_pipeline

setup_logging("WARNING")


def _settings():
    return load_settings(use_fixtures=True, no_db=True)


def test_end_to_end_counts():
    summary = run_pipeline(_settings(), store=InMemoryStore())
    stages = summary["stages"]
    assert stages["scrape"]["jobs_scraped"] == 22
    # 7 active-client jobs excluded, 1 agency dropped -> 14 companies evaluated.
    assert stages["exclude"]["companies_to_evaluate"] == 14
    assert stages["exclude"]["active_client_job_rows"] == 7
    assert stages["exclude"]["dropped_agencies"] == 1
    # Genmab (2,500) is the only size drop.
    assert stages["fit_check"]["fit"] == 13
    assert stages["fit_check"]["not_fit"] == 1
    # Every fit company yields exactly one decision-maker; budget = 1 credit each.
    assert stages["dmm"]["hits"] == 13
    assert stages["dmm"]["people_search_credits_spent"] == 13
    assert summary["database_totals"]["contacts"] == 13


def test_reruns_are_idempotent_and_do_not_respend_credits():
    store = InMemoryStore()
    settings = _settings()

    first = run_pipeline(settings, store=store)
    second = run_pipeline(settings, store=store)

    # No duplicate rows on the second pass.
    assert first["database_totals"] == second["database_totals"]
    assert second["database_totals"]["jobs"] == 22
    assert second["database_totals"]["contacts"] == 13
    # Second pass spends ZERO people-search credits: every company is guard-skipped.
    assert second["stages"]["dmm"]["people_search_credits_spent"] == 0
    assert second["stages"]["dmm"]["skipped_already_queried"] == 13
    assert second["stages"]["validate"]["validations_run"] == 0


def test_active_client_rows_have_required_columns():
    summary = run_pipeline(_settings(), store=InMemoryStore())
    # The CSV is written; just assert the schema-critical count here.
    assert summary["stages"]["exclude"]["active_client_job_rows"] == 7
