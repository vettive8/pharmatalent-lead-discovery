"""Runtime configuration — every account-specific value comes from the env.

Nothing here is hardcoded to a particular Apify / AI Ark / Supabase / OpenRouter
account. Swapping ``.env`` is sufficient to retarget the whole pipeline, which is
exactly how the reviewers rerun it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Repo root = parent of the ``pipeline`` package directory.
ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT_DIR / "fixtures"
OUTPUT_DIR = ROOT_DIR / "output"
SQL_DIR = ROOT_DIR / "sql"

# Default ICP scrape locations (ICP.md, Half 1) — English names only.
DEFAULT_LOCATIONS = [
    "Germany", "Switzerland", "Netherlands", "Belgium", "Denmark", "Sweden",
    "Ireland", "France", "United Kingdom", "Spain", "Italy", "Austria",
    "Finland", "Norway",
]


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return parts or None


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one pipeline run."""

    # --- mode ---------------------------------------------------------------
    use_fixtures: bool = False           # source jobs/people from fixture files
    no_db: bool = False                  # use the in-memory dry-run store

    # --- Apify --------------------------------------------------------------
    apify_token: str | None = None
    apify_actor_id: str = "vIGxjRrHqDTPuE6M4"

    # --- AI Ark (primary people-search) ------------------------------------
    ai_ark_token: str | None = None
    ai_ark_base_url: str = "https://api.ai-ark.com"

    # --- Prospeo (fallback people-search) ----------------------------------
    prospeo_api_key: str | None = None

    # --- OpenRouter ---------------------------------------------------------
    openrouter_api_key: str | None = None
    fitcheck_model: str = "perplexity/sonar"
    validation_model: str = "deepseek/deepseek-chat"

    # --- Supabase (Postgres) -----------------------------------------------
    supabase_db_url: str | None = None

    # --- scrape knobs -------------------------------------------------------
    scrape_locations: list[str] = field(default_factory=lambda: list(DEFAULT_LOCATIONS))
    scrape_time_range: str = "7d"
    scrape_max_items: int = 200

    # --- paths --------------------------------------------------------------
    output_dir: Path = OUTPUT_DIR
    fixtures_dir: Path = FIXTURES_DIR
    schema_path: Path = SQL_DIR / "schema.sql"

    @property
    def prospeo_enabled(self) -> bool:
        return bool(self.prospeo_api_key)

    @property
    def llm_offline(self) -> bool:
        """True when LLM stages must fall back to the offline heuristic."""
        return not self.openrouter_api_key


def load_settings(*, use_fixtures: bool = False, no_db: bool = False) -> Settings:
    """Build :class:`Settings` from the environment (loading ``.env`` first)."""
    load_dotenv(ROOT_DIR / ".env")

    max_items_raw = os.getenv("SCRAPE_MAX_ITEMS")
    try:
        max_items = int(max_items_raw) if max_items_raw else 200
    except ValueError:
        max_items = 200

    return Settings(
        use_fixtures=use_fixtures,
        no_db=no_db,
        apify_token=os.getenv("APIFY_TOKEN") or None,
        apify_actor_id=os.getenv("APIFY_ACTOR_ID", "vIGxjRrHqDTPuE6M4"),
        ai_ark_token=os.getenv("AI_ARK_TOKEN") or None,
        ai_ark_base_url=os.getenv("AI_ARK_BASE_URL", "https://api.ai-ark.com"),
        prospeo_api_key=os.getenv("PROSPEO_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        fitcheck_model=os.getenv("OPENROUTER_FITCHECK_MODEL", "perplexity/sonar"),
        validation_model=os.getenv("OPENROUTER_VALIDATION_MODEL", "deepseek/deepseek-chat"),
        supabase_db_url=os.getenv("SUPABASE_DB_URL") or None,
        scrape_locations=_split_csv(os.getenv("SCRAPE_LOCATIONS")) or list(DEFAULT_LOCATIONS),
        scrape_time_range=os.getenv("SCRAPE_TIME_RANGE", "7d"),
        scrape_max_items=max_items,
    )


class ConfigError(RuntimeError):
    """Raised when required configuration for the chosen run mode is missing."""


def require_live_credentials(settings: Settings) -> None:
    """Validate that a *live* run has the credentials it needs, failing fast.

    Fixture runs are exempt from the provider keys; the only thing a fixture run
    needs is either a DB URL or ``--no-db``.
    """
    missing: list[str] = []
    if not settings.use_fixtures:
        if not settings.apify_token:
            missing.append("APIFY_TOKEN")
        if not settings.openrouter_api_key:
            missing.append("OPENROUTER_API_KEY")
        if not settings.ai_ark_token and not settings.prospeo_api_key:
            missing.append("AI_ARK_TOKEN or PROSPEO_API_KEY")
    if not settings.no_db and not settings.supabase_db_url:
        missing.append("SUPABASE_DB_URL (or pass --no-db for a dry run)")
    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in."
        )
