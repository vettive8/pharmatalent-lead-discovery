# PharmaTalent Europe — Lead-Discovery Pipeline

A Python pipeline that finds biotech/pharma companies in Europe that are actively
hiring the roles **PharmaTalent Europe** fills, qualifies each posting company
against the client ICP (with a real website-research pass), maps the right
decision-maker, validates that person with an LLM, and lands everything in
Supabase — `jobs`, `companies`, `contacts` — ready for downstream outreach.

> _Take-home for the delivery-engineer interview, AI-assisted with Claude Code
> (see [AI-assisted workflow](#ai-assisted-workflow))._
>
> _People-search uses **Prospeo** (see [People-search provider](#people-search-provider)
> for why, not the brief's default AI Ark). Run end-to-end on free-tier accounts; the
> only paid item was a **$5 OpenRouter top-up** (sanctioned by `TOOLS.md`), of which
> all the development + live runs used **~$0.63 total**._

---

## What it does (one paragraph)

Each weekly run: **(1)** scrapes open jobs from LinkedIn via the Apify
*fantastic.jobs* actor using the ICP titles + EU locations, last 7 days; **(2)**
stores every job in Supabase; **(3)** excludes active clients (fuzzy/slug/name
matching) and drops recruitment agencies, deduping the rest into companies;
**(4)** ICP fit-checks each company with a **web-browsing LLM that reads the real
company website**; **(5)** stores qualifying companies with the fit rationale;
**(6)** maps a decision-maker at each via the **Prospeo `people_search` API**,
capped at 2 results/call with a credit guard so reruns never re-spend; **(7)**
validates each candidate with a cheaper LLM ("could this
person plausibly own this requisition?"); **(8)** stores validated contacts linked
back to their company and the surfacing job(s); and **(9)** writes structured logs
plus `output/run_summary.json`. Reruns are idempotent and never re-spend credits.

## Pipeline architecture

```
 Apify scrape ─▶ jobs (Supabase)
      │
      ▼
 exclude active clients + drop agencies ─▶ active_client_hiring.csv  (P2 side-output)
      │
      ▼
 ICP fit-check (web-research LLM) ─▶ companies (Supabase) + icp_fit_decisions.csv
      │  (fit only)
      ▼
 DMM people-search (Prospeo, cap 2, credit guard)
      │
      ▼
 LLM hiring-manager validation ─▶ contacts (Supabase) ──< contact_jobs >── jobs
      │
      ▼
 run_summary.json + structured JSON logs
```

Module map (clean stage boundaries — scrape / qualify / map / validate / persist):

```
pipeline/
  config.py          env-driven settings, fail-fast credential checks
  logging_setup.py   JSON-lines structured logging
  models.py          typed Job / Company / PersonCandidate + Apify parser
  normalize.py       company-name + LinkedIn-URL/name canonicalization
  icp.py             titles, size-band→DMM-title map, geography, active clients
  matching.py        active-client matcher (exact/slug/domain/guarded fuzzy)
  clients/
    http.py          shared bounded-retry policy (429/5xx backoff)
    apify.py         fantastic.jobs actor (+ fixture fallback)
    openrouter.py    web fit-check + cheap validation (prompts + JSON parsing)
    people_search.py Prospeo people-search (+ fixture synth)
    store.py         data layer: PostgresStore + InMemoryStore dry-run double
  stages/            scrape, exclude, fitcheck, dmm, validate
  outputs.py         the two CSVs + run_summary.json
  run.py             orchestrator
  __main__.py        `python -m pipeline`
sql/schema.sql       idempotent CREATE TABLE IF NOT EXISTS for all tables
docs/                ADRs (schema design, people-search budget)
tests/               16 tests, no network/DB
```

## Quick start

```bash
git clone https://github.com/vettive8/pharmatalent-lead-discovery.git
cd pharmatalent-lead-discovery

python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt

# 1) Offline dry run on the bundled fixtures — spends ZERO API credits,
#    needs no accounts at all (in-memory store, offline LLM heuristic):
python -m pipeline --fixtures --no-db

# 2) Fixtures, but persist to your Supabase (set SUPABASE_DB_URL in .env):
python -m pipeline --fixtures

# 3) Full live run against your real accounts:
cp .env.example .env   # fill it in (see below)
python -m pipeline
```

## Configuration — swapping in your own credentials

The pipeline reads **every** account-specific value from environment variables —
no hardcoded tokens, URLs, or project IDs anywhere. **To run against a different
set of accounts, change only `.env`. No code edits.**

Copy `.env.example` → `.env` and fill in:

| Variable | Required for live | Purpose |
|---|---|---|
| `APIFY_TOKEN` | yes | Apify LinkedIn jobs scraper |
| `OPENROUTER_API_KEY` | yes | ICP fit-check + hiring-manager validation |
| `PROSPEO_API_KEY` | yes | People-search (DMM step) — the provider this pipeline uses |
| `SUPABASE_DB_URL` | yes | Postgres connection string — schema is created from it |
| `OPENROUTER_FITCHECK_MODEL` | optional | Default `perplexity/sonar` |
| `OPENROUTER_VALIDATION_MODEL` | optional | Default `deepseek/deepseek-chat` |
| `SCRAPE_LOCATIONS` / `SCRAPE_TIME_RANGE` / `SCRAPE_MAX_ITEMS` | optional | Scrape tuning |

All required values are checked at startup, with a clear error naming anything
missing. (Why Prospeo and not the brief's default AI Ark? See
[People-search provider](#people-search-provider).)

**Supabase note (important for reviewers).** We connect with `psycopg` over the
Postgres connection string so the schema can be created from code on a **blank**
project (`CREATE TABLE IF NOT EXISTS` in `sql/schema.sql`, run at startup) — the
PostgREST/anon REST API cannot run DDL. So `SUPABASE_DB_URL` is the only Supabase
variable you need; `SUPABASE_URL` / service-role key are not used.

> **Use the Session pooler string** (Supabase → *Connect* → *Session pooler*, host
> like `aws-...pooler.supabase.com:5432`). It is **IPv4-compatible** and supports the
> schema's DDL. Avoid the other two: the **Direct** connection is **IPv6-only** and
> fails on IPv4-only networks, and the **Transaction pooler** (port 6543) can't run
> DDL. **So: Session pooler.**

## Supabase schema (ER sketch)

```
jobs ──(organization_slug)──▶ companies(decision: fit|not_fit) ──< dmm_queries  [credit guard]
                                   │
                                   ▼
                               contacts ──< contact_jobs >── jobs   [N:M]
```

- **`jobs`** — raw scrape cache, PK = Apify job id (re-scrape = upsert).
- **`companies`** — canonical company entity, deduped by `linkedin_slug`; holds
  every evaluated non-active-client company with its fit `decision` + `rationale`
  (so drops are auditable too).
- **`contacts`** — validated decision-makers, deduped by `dedup_key` (canonical
  LinkedIn URL, else `normalized_name|domain`), FK to `companies`.
- **`contact_jobs`** — N:M join so one person links to every surfacing job.
- **`dmm_queries`** — `(company, title)` audit + credit guard; a rerun skips
  already-resolved companies and spends no new people-search credits.

Full rationale and alternatives in [docs/adr-001-supabase-schema.md](docs/adr-001-supabase-schema.md).

## Model choices (and why)

| Stage | Default model | Why |
|---|---|---|
| ICP fit-check (web research) | `perplexity/sonar` | The fit-check **must** read the company's real website, not the LinkedIn snippet — this needs a browsing model. Sonar is a cheap web-grounded option; the cost is justified because it runs once per qualifying company. |
| Hiring-manager validation | `deepseek/deepseek-chat` | Runs once per candidate person, so it must be cheap and reliable at structured (JSON) output. Called at temperature 0 with a one-sentence-reason contract. |

Both are overridable via env. Rationale follows TOOLS.md §3: spend the budget on
the web model, keep validation cheap.

## People-search provider

This pipeline uses **Prospeo** for the decision-maker mapping step (`POST
/search-person`, `X-KEY` auth), verified end-to-end against the live API.

**Why not AI Ark (the brief's default)?** AI Ark requires a *business-email*
account, which I didn't have for this take-home — so I could not test it live.
Rather than ship a provider I never exercised (untested code is a liability, not a
feature), I went all-in on Prospeo, which the brief accepts as an equal
alternative (TOOLS.md §2b). This is the one trade-off it costs: the P2 AI Ark
`mcp.json` bonus is not included. Everything is env-driven, so swapping providers
later is a config + small client change, not a rewrite.

**One consequence for the cascade:** Prospeo matches by company **name** and takes
no location parameter, so the DMM.md geographic cascade
(city→country→region→worldwide) — which is meaningful for a *location*-parameterised
API — does not apply. We do one company-scoped search and rank the returned people
by decision-maker seniority. The recorded `cascade_level` is therefore `company`.
See [docs/adr-002-people-search-budget.md](docs/adr-002-people-search-budget.md).

## Operational behavior

- **Idempotent reruns.** Every write is `INSERT … ON CONFLICT` on a natural key.
  A second run produces no duplicate rows and (verified in tests) spends **0**
  people-search credits — already-resolved `(company, title)` pairs are skipped
  via `dmm_queries`. See [docs/adr-002-people-search-budget.md](docs/adr-002-people-search-budget.md).
- **Budget discipline.** People-search is capped at 2 results/call and never
  re-queries a resolved `(company, title)` — designed to respect tight people-search
  free tiers (Prospeo's credits, and the brief's even-tighter default budget).
- **Resilience.** Provider calls use bounded exponential backoff on 429/5xx
  (never infinite). A failed fit-check or validation call degrades to the offline
  heuristic for that one item instead of aborting the run. Config errors fail fast
  with an actionable message.
- **Observability.** Structured JSON logs per stage + `output/run_summary.json`
  with per-stage counts, credits spent, and DB totals.

## Outputs (`output/`)

- `run_summary.json` — last-run stats (P1).
- `active_client_hiring.csv` — active clients that showed up in the scrape, an
  upsell signal for the account manager (P2).
- `icp_fit_decisions.csv` — full fit-check audit trail (every keep/drop + rationale).

_The committed copies are from a representative **first** live run (`mode: "live"`):
real Apify scrape, `perplexity/sonar` web fit-check, Prospeo people-search, and LLM
hiring-manager validation — so the DMM/validate stages show real work (hits,
validations, contacts created), not the all-zeros of an idempotent rerun. A
reviewer's own run overwrites them and persists to their Supabase._

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

16 tests, no network or DB: name/URL normalization, active-client matching (incl.
slug match and the short-name false-positive guard), the validation heuristic
against the real people-search fixture, and a full fixture run asserting stage
counts **and idempotency** (second pass = 0 credits, no duplicate rows).

## Live-run verification (done)

This pipeline was run end-to-end against real free-tier accounts and populated a
blank Supabase project. Integration details verified against the live APIs:

1. **Apify** — confirmed against the actor's live Input schema: recency param is
   `timeRange` (enum `1h/24h/7d/6m`), result cap is `limit` (max 5000).
2. **Prospeo** — `POST https://api.prospeo.io/search-person` (`X-KEY` auth) with a
   `filters.company.names.include` query; returns 25/page (capped to 2 client-side),
   ranked by seniority. `NO_RESULTS` and rate-limits arrive as HTTP 400 and are
   handled (no-candidate / retry). Verified with live calls.
3. **OpenRouter** — the web model (`perplexity/sonar`) rejects
   `response_format=json_object`, so the fit-check requests plain text and parses
   the JSON out; validation (`deepseek/deepseek-chat`) uses JSON mode.
4. **Supabase** — schema bootstrapped from code on a blank project; jobs / companies
   / contacts populated over the direct Postgres (psycopg) connection.

## Scope — what's done

- **P0 (all done):** Apify scrape with ICP keywords + locations + 7-day window →
  `jobs`; active-client exclusion *before* fit-check; web-research ICP fit-check →
  `companies` + rationale; Prospeo `people_search` capped at 2, logging the provider
  and scope on every contact; mandatory LLM hiring-manager validation with a logged
  reason for every drop; validated contacts → `contacts` linked to company + job(s);
  schema created from code; README for a fresh clone.
- **P1 (all done):** idempotent reruns (no dup rows, no re-spent credits); contact
  dedup with the N:M job join; structured logs + `run_summary.json`; schema
  bootstraps a blank Supabase.
- **P2 (selected):** `active_client_hiring.csv`; LLM-scored fit (`fit_score`) +
  per-company rationale; bounded retries / rate-limit-aware client; ADRs in `docs/`.
  *(Not done: the AI Ark `mcp.json` bonus — we don't ship the untested AI Ark path;
  see [People-search provider](#people-search-provider).)*

### Cut for time / what I'd do with another day

- **Async/concurrent provider calls** — stages run sequentially; at this volume
  it's fast enough, but a `httpx.AsyncClient` + a small worker pool would cut live
  wall-time. The retry policy is already in place to build on.
- **Checkpoint/resume** — a run is restartable (idempotent) but doesn't checkpoint
  mid-stage; I'd persist a run id and resume from the last completed company.
- **Few-shot validation prompt** — TOOLS.md suggests seeding the validation prompt
  with real examples after the first 5–10 leads; I'd add that once live data exists.

## Contradictions / judgment calls flagged

- **Size pre-filter vs. the P2 hiring signal.** Pushing `organizationEmployeesGte/
  Lte` to the scrape saves budget but hides active-client mega-pharma (Pfizer/
  Bayer/Roche), killing the "active client is hiring" signal. We therefore do **one
  ICP-shaped scrape and filter size *after***, so the same scrape feeds both the
  main pipeline and the side-output. (TOOLS.md design note.)
- **DMM geographic cascade.** DMM.md describes a city→country→region→worldwide
  cascade, which assumes a *location*-parameterised people-search. Prospeo matches
  by company name and takes no location, so we do one company-scoped search and
  rank by seniority instead — same goal (find one valid decision-maker), fewer
  credits. We record the band's primary title as the `(company, title)` guard key.
  Detailed in ADR 002.

## AI-assisted workflow

Built with **Claude Code**. Every change landed through a merged Claude Code pull
request — the full series (core pipeline → live-API hardening → provider
transparency → ICP size-rule fix → Prospeo-only refactor → Supabase pooler fix →
fit-check caching → docs/artifact polish) is in the repo's **Pull requests** tab.
This satisfies the brief's requirement that at least one merged PR be opened via
Claude Code or Codex.

## License

[MIT](LICENSE).
