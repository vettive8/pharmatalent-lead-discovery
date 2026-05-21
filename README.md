# PharmaTalent Europe — Lead-Discovery Pipeline

> Take-home for the delivery-engineer interview. A Python pipeline that finds
> biotech/pharma companies in Europe actively hiring the roles PharmaTalent
> fills, qualifies them against the client ICP, maps the right decision-maker at
> each, validates that person with an LLM, and lands everything in Supabase.

**Status:** in active development. See [Scope](#scope--what-is-and-isnt-done).

---

## What it does (one paragraph)

Each weekly run: (1) scrapes open biotech/pharma jobs from LinkedIn via the
Apify *fantastic.jobs* actor using the ICP titles + EU locations, last 7 days;
(2) stores every job in Supabase; (3) drops active clients and dedupes the
posting companies; (4) ICP fit-checks each remaining company with a
web-browsing LLM that reads the company's real website; (5) stores qualifying
companies with the fit rationale; (6) maps a decision-maker at each via the
AI Ark `people_search` API (Prospeo fallback), capped at 2 results/call with a
geographic cascade; (7) validates each candidate with a cheaper LLM
("could this person plausibly own this requisition?"); (8) stores validated
contacts linked back to their company and the job(s) that surfaced them; and
(9) writes structured logs plus a `run_summary.json`. Reruns are idempotent.

## Pipeline stages

```
Apify scrape ─▶ jobs(Supabase) ─▶ exclude active clients + dedupe
   └▶ ICP fit-check (web-research LLM) ─▶ companies(Supabase)
        └▶ DMM people_search (AI Ark→Prospeo, cascade, cap 2)
             └▶ LLM hiring-manager validation ─▶ contacts(Supabase)
                  └▶ run_summary.json + structured logs
```

## Quick start

```bash
git clone https://github.com/vettive8/pharmatalent-lead-pipeline.git
cd pharmatalent-lead-pipeline

python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in your credentials (see below)

# Dry run on the bundled fixtures — spends zero API credits:
python -m pipeline --fixtures

# Full live run against your real accounts:
python -m pipeline
```

## Configuration — swapping in your own credentials

The pipeline reads **every** account-specific value from environment variables;
there are no hardcoded tokens, URLs, or project IDs. To run it against a
different set of accounts, change only `.env` — no code edits.

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `APIFY_TOKEN` | yes | Apify LinkedIn jobs scraper |
| `AI_ARK_TOKEN` | yes | Primary people-search (DMM) |
| `PROSPEO_API_KEY` | optional | Fallback people-search |
| `OPENROUTER_API_KEY` | yes | ICP fit-check + hiring-manager validation |
| `SUPABASE_DB_URL` | yes | Postgres connection string (schema is created from code) |

> **Supabase note:** we connect with `psycopg` over the direct Postgres
> connection string so the schema can be created from code on a *blank* project
> (`CREATE TABLE IF NOT EXISTS`) — the PostgREST/anon API cannot run DDL. Grab
> the URI from *Dashboard → Project Settings → Database → Connection string*.

## Model choices (and why)

| Stage | Model (default, override via env) | Why |
|---|---|---|
| ICP fit-check (web research) | `perplexity/sonar` | Must read the company's real website, not just the LinkedIn snippet — needs a browsing model. Sonar is a cheap web-grounded option. |
| Hiring-manager validation | `deepseek/deepseek-chat` | Runs once per candidate person, so it must be cheap and reliable at structured (JSON) output. Used at temperature 0. |

## Supabase schema (ER sketch)

_Documented in detail in [`docs/`](docs/) once finalized._ Three core tables —
`jobs`, `companies`, `contacts` — plus a `contact_jobs` join (one contact can be
surfaced by many jobs) and a DMM-query audit table that prevents re-spending
people-search credits on a `(company, title)` pair already queried.

## Scope — what is and isn't done

_Filled in as the build progresses._

## AI-assisted workflow

Built with Claude Code. At least one feature PR in this repo's history was
opened via Claude Code, per the brief.

## License

[MIT](LICENSE).
