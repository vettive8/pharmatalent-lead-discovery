-- PharmaTalent Europe — lead-discovery pipeline schema.
--
-- Every statement is idempotent (CREATE ... IF NOT EXISTS), so this file is run
-- on pipeline startup against any Supabase/Postgres project — blank or already
-- populated — without error and without manual table creation.
--
-- Design notes live in docs/adr-001-supabase-schema.md. ER summary:
--
--   jobs ──(organization_slug)──▶ companies(decision) ──< dmm_queries  [credit guard]
--                                      │
--                                      ▼
--                                  contacts ──< contact_jobs >── jobs   [N:M]
--
-- gen_random_uuid() is built into Postgres 13+ (Supabase is 15+), no extension
-- required.

-- ---------------------------------------------------------------------------
-- jobs — raw cache of every scraped LinkedIn job (incl. active clients).
-- PK is the Apify actor's stable job id, which makes re-scrape idempotent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id                      TEXT PRIMARY KEY,
    linkedin_job_id         TEXT,
    title                   TEXT NOT NULL,
    organization            TEXT NOT NULL,        -- raw company name as scraped
    organization_slug       TEXT,                 -- linkedin_org_slug (company key)
    organization_url        TEXT,
    url                     TEXT,
    date_posted             TIMESTAMPTZ,
    description_text        TEXT,
    seniority               TEXT,
    employment_type         TEXT[],
    locations_derived       TEXT[],
    city                    TEXT,
    region                  TEXT,
    country                 TEXT,
    linkedin_org_employees  INTEGER,
    linkedin_org_size       TEXT,
    linkedin_org_industry   TEXT,
    linkedin_org_headquarters TEXT,
    linkedin_org_specialties  TEXT[],
    linkedin_org_description  TEXT,
    is_recruitment_agency   BOOLEAN DEFAULT FALSE,
    raw                     JSONB,                 -- full original record, lossless
    scraped_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_org_slug ON jobs (organization_slug);
CREATE INDEX IF NOT EXISTS idx_jobs_date_posted ON jobs (date_posted);

-- ---------------------------------------------------------------------------
-- companies — every non-active-client posting company we evaluated, with the
-- ICP fit-check verdict + rationale. Deduped by linkedin_slug. Active clients
-- never land here (excluded before fit-check); they go to the side-output CSV.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linkedin_slug     TEXT UNIQUE NOT NULL,        -- dedup / idempotency key
    name              TEXT NOT NULL,
    normalized_name   TEXT,
    domain            TEXT,                         -- resolved during web research
    employees         INTEGER,
    size_band         TEXT,                         -- DMM band: '50-200' etc.
    industry          TEXT,
    headquarters      TEXT,
    decision          TEXT NOT NULL CHECK (decision IN ('fit', 'not_fit')),
    fit_score         INTEGER CHECK (fit_score BETWEEN 0 AND 100),  -- P2: graded fit
    confidence        TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    rationale         TEXT,                         -- must reference website findings
    checked_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_companies_decision ON companies (decision);

-- ---------------------------------------------------------------------------
-- contacts — validated decision-makers (LLM said "yes"). One row per person.
-- dedup_key = canonical linkedin_url, else 'normalized_full_name|domain'
-- (the precedence from DMM.md), enforced UNIQUE for idempotent reruns.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id            UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    dedup_key             TEXT UNIQUE NOT NULL,
    full_name             TEXT NOT NULL,
    normalized_full_name  TEXT,
    title                 TEXT,
    linkedin_url          TEXT,
    location              TEXT,
    about_snippet         TEXT,
    provider              TEXT CHECK (provider IN ('ai_ark', 'prospeo')),
    cascade_level         TEXT CHECK (cascade_level IN ('city', 'country', 'region', 'worldwide', 'company')),
    target_title          TEXT,                     -- DMM target title that surfaced them
    validation_decision   TEXT CHECK (validation_decision IN ('yes', 'no')),
    validation_reason     TEXT,
    found_at              TIMESTAMPTZ,
    validated_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts (company_id);

-- ---------------------------------------------------------------------------
-- contact_jobs — N:M link. The same person can be the decision-maker for
-- several scraped jobs at the same company; we keep one contact row and link
-- every surfacing job here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contact_jobs (
    contact_id  UUID NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    job_id      TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    linked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (contact_id, job_id)
);

-- ---------------------------------------------------------------------------
-- dmm_queries — credit guard + audit for the people-search step. One row per
-- (company, target_title) pair ever queried, UNIQUE so a rerun NEVER re-spends
-- AI Ark / Prospeo credits on a pair we already resolved.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dmm_queries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    target_title  TEXT NOT NULL,
    cascade_level TEXT,                             -- level that hit, or last tried
    provider      TEXT,                             -- ai_ark | prospeo | none
    result_count  INTEGER NOT NULL DEFAULT 0,       -- people returned (= credits spent)
    outcome       TEXT NOT NULL CHECK (outcome IN ('hit', 'no_candidate')),
    queried_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, target_title)
);
