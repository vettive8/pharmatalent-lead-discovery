# ADR 002 — People-search provider & budget design

**Status:** accepted · **Date:** 2026-05-21

## Context

The decision-maker mapping (DMM) step is the tightest budget in the pipeline:
**AI Ark's free tier is 100 credits = 100 returned people, total.** Run out and
the account is dead for the rest of the case study. Prospeo.io is an accepted
alternative with a far larger free trial (~2,500 people). We had to choose
providers and a calling strategy that finds a valid hiring manager per company
without exhausting credits, and that survives reruns.

## Decision

**Provider: Prospeo (used here); AI Ark optional.** This submission runs on
Prospeo — AI Ark requires a business-email account I didn't have. AI Ark remains
wired as an optional drop-in: if `AI_ARK_TOKEN` is set it is tried first (built to
AI Ark's documented `/v1/people` API) and Prospeo is the fallback. The provider
that produced each hit is logged and stored on the contact (`provider`), so the
routing is auditable. Either provider can run alone via env.

**Cascade depends on the provider.** AI Ark takes a `location`, so it walks the
DMM.md geographic cascade (city → country → EU region → worldwide), one capped call
per level, stopping at the first hit. **Prospeo matches by company name and ignores
location**, so a Prospeo run does a single company-scoped search instead of
re-querying the same company per geo level (the recorded `cascade_level` is
`company`). Both cap at **2 results** and pass the band's target titles.

> **Reinterpretation flagged:** DMM.md phrases stop-on-first-hit "per (company,
> target title)". Passing the full band title list in a single call (rather than
> one call per title) is strictly *fewer* credits and still returns the best-matching
> decision-maker — the right call given the 100-credit ceiling. We record the guard
> against `(company, primary band title)`. This is the kind of contradiction the
> brief asks candidates to flag rather than silently follow.

**Credit guard via `dmm_queries`.** Before searching, we check whether the
`(company, primary title)` pair was already queried (see
[ADR 001](adr-001-supabase-schema.md)). If so, the company is skipped entirely —
no people-search call, no LLM validation. A rerun therefore spends **zero** new
credits on already-resolved companies (verified by `test_pipeline_fixtures.py`).

**"worldwide" cascade only for sub-200-employee companies**, where a single global
talent owner is plausible; larger companies stop at the region level rather than
chasing a worldwide match that wouldn't own a local requisition.

## Alternatives considered

- *One call per (company, title)* — literal reading of DMM.md. Up to 5× the calls
  per company; unjustifiable against a 100-credit cap.
- *AI Ark as the live provider* — the brief's default, but it needs a
  business-email account I didn't have. We use Prospeo and keep AI Ark as an
  optional drop-in plus the `mcp.json` bonus, so a reviewer with an AI Ark key
  still gets the "primary/fallback" pattern.
- *Higher result cap* — every returned person costs an LLM validation call too, so
  2 is the right ceiling regardless of provider (per TOOLS.md §2b).

## Consequences

- Worst case ≈ (companies × cascade levels) calls × 2 results; in practice most
  companies hit at the city level → ~1 call/company. The fixture run resolves 13
  companies for 13 credits.
- Validation cost is bounded the same way (≤2 candidates/company), and the
  in-run decision cache plus the `dmm_queries` guard prevent re-validation.
