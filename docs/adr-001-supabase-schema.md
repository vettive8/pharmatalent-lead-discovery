# ADR 001 — Supabase schema & how the three tables relate

**Status:** accepted · **Date:** 2026-05-21

## Context

The case study requires three tables — `jobs`, `companies`, `contacts` — and
says the relationships are part of what's evaluated. The pipeline must also
create the schema from code on a *blank* Supabase project (reviewers swap in
their own credentials and rerun), with idempotent reruns that don't duplicate
rows. Two questions had to be answered: **how do the tables relate**, and **how
does the schema get created without manual steps**.

## Decision

### Relationships

```
jobs ──(organization_slug)──▶ companies(decision) ──< dmm_queries  [credit guard]
                                   │
                                   ▼
                               contacts ──< contact_jobs >── jobs   [N:M]
```

- **`jobs`** is the raw scrape cache. PK = the Apify actor's stable job `id`, so
  re-scraping the same job is an upsert, never a duplicate. Every scraped job is
  stored, *including active clients* — `jobs` is the lossless record; filtering
  happens downstream at the company level.
- **`companies`** is the canonical company entity, deduped by `linkedin_slug`. It
  holds **every evaluated, non-active-client posting company** with its fit-check
  `decision` (`fit`/`not_fit`), `rationale`, `confidence`, and optional
  `fit_score`. Keeping `not_fit` rows here (rather than only the qualified set)
  satisfies "persist the drop rationale somewhere" and means the table doubles as
  the audit trail. Active clients never reach this table — they go to the
  side-output CSV only.
- **`contacts`** holds validated decision-makers, one row per person, FK to
  `companies`. Dedup is enforced by a single `dedup_key` column: the canonical
  LinkedIn URL when present, else `normalized_full_name|domain` — exactly the
  precedence DMM.md specifies. A `UNIQUE` constraint makes reruns idempotent.
- **`contact_jobs`** is the N:M join. The same person can be the decision-maker
  for several jobs at one company; we keep one contact and link each surfacing
  job here, rather than duplicating the person per job.
- **`dmm_queries`** is a support table that is the people-search **credit guard**: one
  row per `(company, primary target title)` ever queried, `UNIQUE` on that pair.
  Before any people-search call the pipeline checks this table; an already-
  resolved company is skipped, so a rerun never re-spends a people-search credit.

### Alternatives considered

- *Separate audit table for not_fit companies.* Cleaner separation, but a second
  table for the same entity adds joins for little gain at this scale; a
  `decision` column on `companies` is simpler and still auditable.
- *Array column of job ids on `contacts`* instead of a join table. Rejected —
  it breaks referential integrity and makes "which contacts surfaced from job X"
  un-queryable. The join table is the right relational model.

## Consequences

- Idempotent by construction: every write is `INSERT … ON CONFLICT` on a natural
  key (`jobs.id`, `companies.linkedin_slug`, `contacts.dedup_key`,
  `contact_jobs(contact_id, job_id)`, `dmm_queries(company_id, target_title)`).
- The credit guard couples idempotency and budget: skipping a resolved company
  avoids both duplicate rows *and* re-spent credits in one check.

See [ADR 002](adr-002-people-search-budget.md) for the people-search budget design.
