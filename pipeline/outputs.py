"""File artifacts written to ``output/``: the two CSVs and run_summary.json."""

from __future__ import annotations

import csv
import json
from pathlib import Path

ACTIVE_CLIENT_COLUMNS = [
    "client_name", "matched_company_name_raw", "scraped_job_title",
    "scraped_job_url", "location", "posted_at", "detected_at",
]
FIT_DECISION_COLUMNS = [
    "company_name", "company_domain", "linkedin_slug", "employees", "size_band",
    "decision", "confidence", "fit_score", "rationale", "checked_at",
]


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_active_client_hiring_csv(rows: list[dict], output_dir: Path) -> Path:
    path = output_dir / "active_client_hiring.csv"
    _write_csv(path, ACTIVE_CLIENT_COLUMNS, rows)
    return path


def write_icp_fit_decisions_csv(rows: list[dict], output_dir: Path) -> Path:
    path = output_dir / "icp_fit_decisions.csv"
    _write_csv(path, FIT_DECISION_COLUMNS, rows)
    return path


def write_run_summary(summary: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "run_summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return path
