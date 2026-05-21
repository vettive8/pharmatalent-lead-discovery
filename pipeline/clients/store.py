"""Persistence layer — the only place that talks to Supabase/Postgres.

Two interchangeable implementations behind one interface:

* ``PostgresStore`` — the real store. psycopg over the Supabase Postgres
  connection string. Creates the schema from code (``sql/schema.sql``) and uses
  ``INSERT ... ON CONFLICT`` so every write is idempotent.
* ``InMemoryStore`` — a dry-run double used for credit-free local testing and
  unit tests (``--no-db``). Holds nothing on disk and runs no SQL; Postgres
  remains the only real persistence layer (no second production datastore).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..models import Company, Job, PersonCandidate, ValidationResult
from ..normalize import normalize_full_name


def make_store(settings) -> "Store":
    """Pick the store implementation for this run."""
    if settings.no_db or not settings.supabase_db_url:
        return InMemoryStore()
    return PostgresStore(settings.supabase_db_url, settings.schema_path)


class Store(ABC):
    """Thin data layer the pipeline persists through."""

    @abstractmethod
    def bootstrap(self) -> None: ...

    @abstractmethod
    def upsert_jobs(self, jobs: list[Job]) -> tuple[int, int]:
        """Upsert jobs; return (newly_inserted, total_seen)."""

    @abstractmethod
    def upsert_company(self, company: Company) -> str:
        """Upsert a company by linkedin_slug; return its id."""

    @abstractmethod
    def company_decision(self, linkedin_slug: str) -> dict | None:
        """Return a company's stored fit-check decision (id, decision, rationale,
        confidence, fit_score, size_band, domain) if it was already checked, else
        None — the fit-check cache that stops reruns re-spending OpenRouter."""

    @abstractmethod
    def dmm_query_seen(self, company_id: str, target_title: str) -> dict | None:
        """Return a prior (company, title) people-search record, or None."""

    @abstractmethod
    def record_dmm_query(self, company_id: str, target_title: str, cascade_level: str | None,
                         provider: str | None, result_count: int, outcome: str) -> None: ...

    @abstractmethod
    def upsert_contact(self, *, company_id: str, candidate: PersonCandidate, dedup_key: str,
                       cascade_level: str | None, target_title: str | None,
                       validation: ValidationResult, found_at: datetime,
                       validated_at: datetime) -> tuple[str, bool]:
        """Upsert a contact by dedup_key; return (contact_id, created)."""

    @abstractmethod
    def link_contact_job(self, contact_id: str, job_id: str) -> None: ...

    @abstractmethod
    def counts(self) -> dict: ...

    def close(self) -> None:  # pragma: no cover - optional
        pass


# ---------------------------------------------------------------------------
# Postgres / Supabase
# ---------------------------------------------------------------------------
class PostgresStore(Store):
    def __init__(self, db_url: str, schema_path) -> None:
        import psycopg  # imported lazily so --no-db / tests don't need a driver

        self._psycopg = psycopg
        self._schema_path = schema_path
        self.conn = psycopg.connect(db_url, autocommit=True)

    def bootstrap(self) -> None:
        sql = self._schema_path.read_text(encoding="utf-8")
        with self.conn.cursor() as cur:
            cur.execute(sql)

    def upsert_jobs(self, jobs: list[Job]) -> tuple[int, int]:
        if not jobs:
            return (0, 0)
        from psycopg.types.json import Json

        ids = [j.id for j in jobs]
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM jobs WHERE id = ANY(%s)", (ids,))
            existing = {r[0] for r in cur.fetchall()}
            rows = [(
                j.id, j.linkedin_job_id, j.title, j.organization, j.organization_slug,
                j.organization_url, j.url, j.date_posted, j.description_text, j.seniority,
                j.employment_type, j.locations_derived, j.city, j.region, j.country,
                j.linkedin_org_employees, j.linkedin_org_size, j.linkedin_org_industry,
                j.linkedin_org_headquarters, j.linkedin_org_specialties,
                j.linkedin_org_description, j.is_recruitment_agency, Json(j.raw),
            ) for j in jobs]
            cur.executemany(
                """
                INSERT INTO jobs (
                    id, linkedin_job_id, title, organization, organization_slug,
                    organization_url, url, date_posted, description_text, seniority,
                    employment_type, locations_derived, city, region, country,
                    linkedin_org_employees, linkedin_org_size, linkedin_org_industry,
                    linkedin_org_headquarters, linkedin_org_specialties,
                    linkedin_org_description, is_recruitment_agency, raw
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    organization = EXCLUDED.organization,
                    organization_slug = EXCLUDED.organization_slug,
                    date_posted = EXCLUDED.date_posted,
                    description_text = EXCLUDED.description_text,
                    linkedin_org_employees = EXCLUDED.linkedin_org_employees,
                    raw = EXCLUDED.raw,
                    scraped_at = now()
                """,
                rows,
            )
        new = sum(1 for j in jobs if j.id not in existing)
        return (new, len(jobs))

    def upsert_company(self, company: Company) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO companies (
                    linkedin_slug, name, normalized_name, domain, employees, size_band,
                    industry, headquarters, decision, fit_score, confidence, rationale, checked_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (linkedin_slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    domain = COALESCE(EXCLUDED.domain, companies.domain),
                    employees = EXCLUDED.employees,
                    size_band = EXCLUDED.size_band,
                    decision = EXCLUDED.decision,
                    fit_score = EXCLUDED.fit_score,
                    confidence = EXCLUDED.confidence,
                    rationale = EXCLUDED.rationale,
                    checked_at = EXCLUDED.checked_at,
                    updated_at = now()
                RETURNING id
                """,
                (company.linkedin_slug, company.name, company.normalized_name, company.domain,
                 company.employees, company.size_band, company.industry, company.headquarters,
                 company.decision, company.fit_score, company.confidence, company.rationale,
                 company.checked_at),
            )
            return str(cur.fetchone()[0])

    def company_decision(self, linkedin_slug: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, decision, rationale, confidence, fit_score, size_band, domain, checked_at "
                "FROM companies WHERE linkedin_slug = %s",
                (linkedin_slug,),
            )
            row = cur.fetchone()
        if not row or row[7] is None:        # no checked_at => not yet fit-checked
            return None
        return {"id": str(row[0]), "decision": row[1], "rationale": row[2], "confidence": row[3],
                "fit_score": row[4], "size_band": row[5], "domain": row[6], "checked_at": row[7]}

    def dmm_query_seen(self, company_id: str, target_title: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT outcome, provider, cascade_level, result_count FROM dmm_queries "
                "WHERE company_id = %s AND target_title = %s",
                (company_id, target_title),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"outcome": row[0], "provider": row[1], "cascade_level": row[2], "result_count": row[3]}

    def record_dmm_query(self, company_id, target_title, cascade_level, provider, result_count, outcome) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dmm_queries (company_id, target_title, cascade_level, provider, result_count, outcome)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (company_id, target_title) DO UPDATE SET
                    cascade_level = EXCLUDED.cascade_level,
                    provider = EXCLUDED.provider,
                    result_count = EXCLUDED.result_count,
                    outcome = EXCLUDED.outcome,
                    queried_at = now()
                """,
                (company_id, target_title, cascade_level, provider, result_count, outcome),
            )

    def upsert_contact(self, *, company_id, candidate, dedup_key, cascade_level, target_title,
                       validation, found_at, validated_at) -> tuple[str, bool]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO contacts (
                    company_id, dedup_key, full_name, normalized_full_name, title, linkedin_url,
                    location, about_snippet, provider, cascade_level, target_title,
                    validation_decision, validation_reason, found_at, validated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (dedup_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    validation_decision = EXCLUDED.validation_decision,
                    validation_reason = EXCLUDED.validation_reason,
                    validated_at = EXCLUDED.validated_at
                RETURNING id, (xmax = 0) AS created
                """,
                (company_id, dedup_key, candidate.full_name, normalize_full_name(candidate.full_name),
                 candidate.title, candidate.linkedin_url, candidate.location, candidate.about_snippet,
                 candidate.provider, cascade_level, target_title, validation.decision,
                 validation.reason, found_at, validated_at),
            )
            row = cur.fetchone()
            return (str(row[0]), bool(row[1]))

    def link_contact_job(self, contact_id: str, job_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO contact_jobs (contact_id, job_id) VALUES (%s,%s) "
                "ON CONFLICT (contact_id, job_id) DO NOTHING",
                (contact_id, job_id),
            )

    def counts(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            jobs = cur.fetchone()[0]
            cur.execute("SELECT decision, count(*) FROM companies GROUP BY decision")
            by_decision = dict(cur.fetchall())
            cur.execute("SELECT count(*) FROM contacts")
            contacts = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM contact_jobs")
            links = cur.fetchone()[0]
        return {
            "jobs": jobs,
            "companies_fit": by_decision.get("fit", 0),
            "companies_not_fit": by_decision.get("not_fit", 0),
            "contacts": contacts,
            "contact_job_links": links,
        }

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# In-memory dry-run double
# ---------------------------------------------------------------------------
class InMemoryStore(Store):
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.companies: dict[str, dict] = {}        # slug -> row
        self._company_ids: dict[str, str] = {}      # slug -> id
        self.contacts: dict[str, dict] = {}         # dedup_key -> row
        self.contact_jobs: set[tuple[str, str]] = set()
        self.dmm: dict[tuple[str, str], dict] = {}

    def bootstrap(self) -> None:
        pass

    def upsert_jobs(self, jobs: list[Job]) -> tuple[int, int]:
        new = 0
        for j in jobs:
            if j.id not in self.jobs:
                new += 1
            self.jobs[j.id] = j
        return (new, len(jobs))

    def upsert_company(self, company: Company) -> str:
        cid = self._company_ids.get(company.linkedin_slug) or str(uuid.uuid4())
        self._company_ids[company.linkedin_slug] = cid
        self.companies[company.linkedin_slug] = {
            "id": cid, "name": company.name, "decision": company.decision,
            "rationale": company.rationale, "confidence": company.confidence,
            "fit_score": company.fit_score, "size_band": company.size_band,
            "domain": company.domain, "employees": company.employees,
            "checked_at": company.checked_at,
        }
        return cid

    def company_decision(self, linkedin_slug: str) -> dict | None:
        row = self.companies.get(linkedin_slug)
        if not row or not row.get("checked_at"):
            return None
        return row

    def dmm_query_seen(self, company_id, target_title) -> dict | None:
        return self.dmm.get((company_id, target_title))

    def record_dmm_query(self, company_id, target_title, cascade_level, provider, result_count, outcome) -> None:
        self.dmm[(company_id, target_title)] = {
            "outcome": outcome, "provider": provider,
            "cascade_level": cascade_level, "result_count": result_count,
        }

    def upsert_contact(self, *, company_id, candidate, dedup_key, cascade_level, target_title,
                       validation, found_at, validated_at) -> tuple[str, bool]:
        created = dedup_key not in self.contacts
        cid = self.contacts.get(dedup_key, {}).get("id") or str(uuid.uuid4())
        self.contacts[dedup_key] = {
            "id": cid, "company_id": company_id, "full_name": candidate.full_name,
            "title": candidate.title, "linkedin_url": candidate.linkedin_url,
            "provider": candidate.provider, "cascade_level": cascade_level,
            "target_title": target_title, "validation_decision": validation.decision,
            "validation_reason": validation.reason,
        }
        return (cid, created)

    def link_contact_job(self, contact_id: str, job_id: str) -> None:
        self.contact_jobs.add((contact_id, job_id))

    def counts(self) -> dict:
        fit = sum(1 for c in self.companies.values() if c["decision"] == "fit")
        not_fit = sum(1 for c in self.companies.values() if c["decision"] == "not_fit")
        return {
            "jobs": len(self.jobs),
            "companies_fit": fit,
            "companies_not_fit": not_fit,
            "contacts": len(self.contacts),
            "contact_job_links": len(self.contact_jobs),
        }
