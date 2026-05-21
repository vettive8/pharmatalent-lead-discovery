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

**Provider routing: AI Ark primary, Prospeo fallback.** Each `(company, cascade
level)` is searched on AI Ark first; if it returns nothing, Prospeo is tried (when
configured). The provider that produced each hit is logged and stored on the
contact (`provider`), so the routing is auditable. Either provider can run alone
via env (drop the other's key).

**One capped call per cascade level, stop on first hit.** For each company we take
the size-band target titles (DMM.md) and walk the geographic cascade
city → country → EU region → worldwide, making **one** people-search call per
level that passes the *whole* band title list, capped at **2 results**. We stop at
the first level that returns anyone.

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
- *Prospeo only* — simplest and safest on budget, but loses the AI Ark MCP bonus
  and the "primary/fallback" pattern the brief calls clean. We wired both.
- *Higher result cap* — every returned person costs an LLM validation call too, so
  2 is the right ceiling regardless of provider (per TOOLS.md §2b).

## Consequences

- Worst case ≈ (companies × cascade levels) calls × 2 results; in practice most
  companies hit at the city level → ~1 call/company. The fixture run resolves 13
  companies for 13 credits.
- Validation cost is bounded the same way (≤2 candidates/company), and the
  in-run decision cache plus the `dmm_queries` guard prevent re-validation.
