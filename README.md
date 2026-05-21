# PharmaTalent Europe — Lead-Discovery Pipeline

A Python pipeline that finds biotech/pharma companies in Europe that are actively
hiring the roles **PharmaTalent Europe** fills, qualifies each posting company
against the client ICP (with a real website-research pass), maps the right
decision-maker, validates that person with an LLM, and lands everything in
Supabase — `jobs`, `companies`, `contacts` — ready for downstream outreach.

> _Take-home for the delivery-engineer interview. Self-reported time: **~7 focused
> hours**, AI-assisted with Claude Code (see [AI-assisted workflow](#ai-assisted-workflow))._
>
> _People-search uses **Prospeo** (AI Ark requires a business-email account, which I
> didn't have for this project). It was run end-to-end on free-tier accounts; the
> only paid item was a **$5 OpenRouter top-up** (sanctioned by `TOOLS.md`), of which
> the live runs used ~$0.23._

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
    people_search.py Prospeo people-search (+ optional AI Ark, fixture synth)
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
| `PROSPEO_API_KEY` | yes* | People-search (DMM) — the configured & verified provider |
| `AI_ARK_TOKEN` | optional* | Alternative people-search; set it to make AI Ark primary (Prospeo fallback) |
| `SUPABASE_DB_URL` | yes | Postgres connection string — schema is created from it |
| `OPENROUTER_FITCHECK_MODEL` | optional | Default `perplexity/sonar` |
| `OPENROUTER_VALIDATION_MODEL` | optional | Default `deepseek/deepseek-chat` |
| `SCRAPE_LOCATIONS` / `SCRAPE_TIME_RANGE` / `SCRAPE_MAX_ITEMS` | optional | Scrape tuning |

\* Provide `PROSPEO_API_KEY` **or** `AI_ARK_TOKEN` (or both). **This repo is
configured and verified end-to-end with Prospeo.** If `AI_ARK_TOKEN` is also set,
AI Ark is tried first and Prospeo is the fallback. Everything else required is
checked at startup with a clear error if missing.

**Supabase note (important for reviewers).** We connect with `psycopg` over the
direct Postgres connection string so the schema can be created from code on a
**blank** project (`CREATE TABLE IF NOT EXISTS` in `sql/schema.sql`, run at
startup) — the PostgREST/anon REST API cannot run DDL. Get the URI from
*Supabase Dashboard → Project Settings → Database → Connection string → URI*
(use the direct/session connection, not the transaction pooler, so DDL works).
This is the only Supabase variable you need; `SUPABASE_URL` / service-role key are
not used by the default path.

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
/search-person`, `X-KEY` auth). AI Ark is the brief's default, but it requires a
business-email account I didn't have for this project, so **Prospeo is what runs
here** and what the verification used.

AI Ark support is still included as an **optional drop-in**: set `AI_ARK_TOKEN` and
it becomes the primary provider (built to AI Ark's documented `/v1/people` API)
with Prospeo as the fallback. It is wired but not exercised in this submission. The
P2 `mcp.json` is provided so a reviewer can connect their own AI Ark account to
Claude Code / Cursor for ad-hoc exploration. Because Prospeo matches by company
name (it is location-agnostic), a Prospeo run does one company-scoped search per
company rather than AI Ark's geographic city→country→region→worldwide cascade —
see [docs/adr-002-people-search-budget.md](docs/adr-002-people-search-budget.md).

## Operational behavior

- **Idempotent reruns.** Every write is `INSERT … ON CONFLICT` on a natural key.
  A second run produces no duplicate rows and (verified in tests) spends **0**
  people-search credits — already-resolved `(company, title)` pairs are skipped
  via `dmm_queries`. See [docs/adr-002-people-search-budget.md](docs/adr-002-people-search-budget.md).
- **Budget discipline.** People-search is capped at 2 results/call and never
  re-queries a resolved company — a discipline designed for the tightest free tier
  (AI Ark's 100-credit ceiling) and applied to Prospeo too.
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

_The committed copies are from a real live run (`mode: "live"`) against free-tier
accounts — Apify scrape + `perplexity/sonar` web fit-check + Prospeo people-search
+ Supabase. A reviewer's run overwrites them._

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
   `timeRange` (enum `1h/24h/7d/6m`), result cap is `limit` (max 5000). Verified.
2. **Prospeo** — `POST https://api.prospeo.io/search-person` (`X-KEY` auth) with a
   `filters.company.names.include` query; returns 25/page (capped to 2 client-side),
   ranked by seniority. `NO_RESULTS` and rate-limits arrive as HTTP 400 and are
   handled (no-candidate / retry). Verified with live calls.
3. **AI Ark** (optional) — built to the documented API
   (`POST /api/developer-portal/v1/people`, `X-TOKEN` auth, `account`/`contact`
   filter objects, `content[]` response). Wired but not live-tested (this repo runs
   on Prospeo); all account-scoped values are env-overridable.
4. **OpenRouter** — the web model (`perplexity/sonar`) rejects
   `response_format=json_object`, so the fit-check requests plain text and parses
   the JSON out; validation (`deepseek/deepseek-chat`) uses JSON mode. Verified.
5. **AI Ark MCP URL** in `mcp.json` (P2 bonus) — confirm the transport/URL from AI
   Ark's MCP docs before connecting (not exercised by the pipeline).

## Scope — what's done

- **P0 (all done):** Apify scrape with ICP keywords + locations + 7-day window →
  `jobs`; active-client exclusion *before* fit-check; web-research ICP fit-check →
  `companies` + rationale; Prospeo `people_search` (AI Ark optional/primary when its
  token is set) capped at 2, logging cascade level + provider; mandatory LLM
  hiring-manager validation with a logged reason for every drop; validated contacts
  → `contacts` linked to company + job(s); schema created from code; README for a
  fresh clone.
- **P1 (all done):** idempotent reruns (no dup rows, no re-spent credits); contact
  dedup with the N:M job join; structured logs + `run_summary.json`; schema
  bootstraps a blank Supabase.
- **P2 (selected):** `active_client_hiring.csv`; LLM-scored fit (`fit_score`) +
  per-company rationale; bounded retries; `mcp.json` for the AI Ark MCP server;
  ADRs in `docs/`.

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
- **DMM "stop on first hit per (company, title)".** We pass the band's full title
  list in one capped call per cascade level instead of one call per title —
  strictly fewer credits, same outcome. Detailed in ADR 002.

## AI-assisted workflow

Built with **Claude Code**. The core pipeline landed via a Claude Code pull
request (see the repo's PR history) — per the brief's requirement that at least
one merged PR be opened via Claude Code or Codex.

## License

[MIT](LICENSE).
