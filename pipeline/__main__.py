"""CLI entrypoint: ``python -m pipeline``.

Examples:
    python -m pipeline --fixtures          # offline dry run, zero credits
    python -m pipeline --fixtures --no-db  # also skip Supabase (in-memory store)
    python -m pipeline                     # live run against your .env credentials
"""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_settings, require_live_credentials
from .logging_setup import setup_logging
from .run import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipeline", description="PharmaTalent lead-discovery pipeline")
    p.add_argument("--fixtures", action="store_true",
                   help="Source jobs/people from bundled fixtures (spends no API credits).")
    p.add_argument("--no-db", action="store_true",
                   help="Use the in-memory dry-run store instead of Supabase/Postgres.")
    p.add_argument("--log-level", default="INFO",
                   help="Logging level (DEBUG, INFO, WARNING, ERROR). Default INFO.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = setup_logging(args.log_level)

    settings = load_settings(use_fixtures=args.fixtures, no_db=args.no_db)
    try:
        require_live_credentials(settings)
    except ConfigError as exc:
        log.error("configuration error", extra={"event": "config.error", "detail": str(exc)})
        return 2

    try:
        run_pipeline(settings)
    except Exception:  # noqa: BLE001 - top-level guard: log full trace, exit non-zero
        log.exception("pipeline failed", extra={"event": "pipeline.error"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
