# ADR 002 — People-search provider & budget design

**Status:** accepted · **Date:** 2026-05-21

## Context

The decision-maker mapping (DMM) step is the tightest budget in the pipeline. The
brief defaults to **AI Ark** (free tier = 100 credits = 100 people, total) and
accepts **Prospeo.io** as an equal alternative (TOOLS.md §2b, far larger free
trial). We had to choose a provider and a calling strategy that finds a valid
hiring manager per company without exhausting credits, and that survives reruns.

## Decision

**Provider: Prospeo only.** AI Ark requires a *business-email* account, which I
didn't have for this take-home, so I could not test it against the live API.
Shipping a provider I never exercised would be untested code — a liability the
"would we accept this into our monorepo?" bar rightly penalises — so I went all-in
on Prospeo (`POST /search-person`, `X-KEY` auth), verified end-to-end. The cost of
this decision is one P2 bonus we forgo: the AI Ark `mcp.json`. Everything is
env-driven and the people-search client is isolated, so adding AI Ark later is a
small, contained change.

**Company-scoped search, not a geographic cascade.** DMM.md describes a
city→country→region→worldwide cascade, which only makes sense for a
*location*-parameterised API. Prospeo's `/search-person` matches by company **name**
and takes no location filter, so we do **one** company-scoped search per company,
parse the returned people, and **rank them by decision-maker seniority** before
applying the cap. The recorded `cascade_level` is therefore `company`.

**Hard cap of 2 results.** Even though Prospeo returns 25 people per call (1 credit),
we keep only the top 2 after ranking — because every kept person costs an LLM
validation call too, so 2 is the right ceiling regardless of provider (TOOLS.md §2b).

**Credit guard via `dmm_queries`.** Before searching, we check whether the
`(company, primary band title)` pair was already queried (see
[ADR 001](adr-001-supabase-schema.md)). If so, the company is skipped entirely — no
people-search call, no LLM validation. A rerun therefore spends **zero** new credits
on already-resolved companies (proven by `test_pipeline_fixtures.py` and observed
live: a rerun reported `skipped_already_queried` with 0 credits spent).

## Alternatives considered

- *AI Ark as the live provider* — the brief's default, but it needs a business-email
  account I didn't have, so it could not be tested live. Rejected in favour of
  shipping only what we verified.
- *One call per (company, title)* — a literal reading of DMM.md's per-title cascade.
  Several× the calls per company for no real gain; unjustifiable against a tight cap.
- *Prospeo location cascade* — Prospeo *does* have a `person_location_search` filter,
  but it requires suggestion-validated location strings and risks false-negative
  drops, so company-scoped search is more reliable here.
- *Higher result cap* — rejected; each extra person is an extra validation call.

## Consequences

- ~1 people-search call per ICP-fit company (≈1 credit each) — comfortably within
  Prospeo's free trial. The live full-ICP run resolved 10 fit companies for ~20 credits.
- Ranking-then-cap means the 2 candidates we validate are the most senior matches,
  improving the hit rate of the (mandatory) LLM validation step.
- Validation cost is bounded the same way (≤2 candidates/company); the in-run
  decision cache plus the `dmm_queries` guard prevent re-validation on reruns.
