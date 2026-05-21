"""Stage 4-5: ICP fit-check (with website research) and persist companies.

Order of checks, cheapest first to protect the OpenRouter budget:
1. Size band (LinkedIn employee count) — a hard ICP rule (50-2000). Out-of-band
   companies are dropped here as not_fit WITHOUT a web call. Genmab (2,500) is the
   canonical example.
2. For size-eligible companies, the real fit-check: a web-browsing LLM reads the
   company website and decides industry / geography / disqualifier fit.

Every company (fit and not_fit) is persisted with its rationale so the keep/drop
reasoning is auditable (ICP.md "Output of the fit-check").

Offline mode (no OPENROUTER_API_KEY, e.g. fixture dev runs) substitutes a clearly
labelled heuristic over the LinkedIn metadata so the pipeline still runs end to
end without spending credits. The live path always uses the web model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import icp
from ..clients.openrouter import OpenRouterClient
from ..clients.store import Store
from ..config import Settings
from ..logging_setup import get_logger
from ..models import Company, FitDecision

log = get_logger("fitcheck")


@dataclass
class FitCheckResult:
    fit_companies: list[Company] = field(default_factory=list)
    decision_rows: list[dict] = field(default_factory=list)   # audit CSV rows (fit + not_fit)


def run_fitcheck(companies: list[Company], settings: Settings, store: Store) -> FitCheckResult:
    client = None if settings.llm_offline else OpenRouterClient(settings)
    result = FitCheckResult()

    for company in companies:
        band = icp.size_band_for(company.employees)
        if band is None:
            decision = FitDecision(
                decision="not_fit",
                rationale=(f"{company.employees} employees is outside the ICP 50-2000 band; "
                           "dropped on size before website research."),
                confidence="high", fit_score=0, source="rule",
            )
        else:
            company.size_band = band[0]
            decision = _offline_fit(company) if client is None else _safe_llm_fit(client, company)

        _apply_and_persist(company, decision, store)
        result.decision_rows.append(_decision_row(company))
        if company.decision == "fit":
            result.fit_companies.append(company)

        log.info("fit-check decision", extra={"event": "fitcheck.decision", "company": company.name,
                 "decision": company.decision, "confidence": company.confidence,
                 "score": company.fit_score, "source": decision.source})

    log.info("fit-check complete", extra={"event": "fitcheck.done",
             "fit": len(result.fit_companies),
             "not_fit": len(result.decision_rows) - len(result.fit_companies)})
    return result


def _safe_llm_fit(client: OpenRouterClient, company: Company) -> FitDecision:
    """LLM fit-check, degrading to the heuristic if the call/parse fails so one
    bad company can't abort the whole run."""
    try:
        return client.fit_check(company)
    except Exception as exc:  # noqa: BLE001 - boundary: log and fall back
        log.warning("fit-check llm failed, using heuristic", extra={"event": "fitcheck.llm_error",
                    "company": company.name, "error": str(exc)})
        decision = _offline_fit(company)
        decision.confidence = "low"
        return decision


def _apply_and_persist(company: Company, decision: FitDecision, store: Store) -> None:
    company.decision = decision.decision
    company.rationale = decision.rationale
    company.confidence = decision.confidence
    company.fit_score = decision.fit_score
    company.checked_at = decision.checked_at
    if decision.domain:
        company.domain = decision.domain
    company.id = store.upsert_company(company)


def _decision_row(company: Company) -> dict:
    return {
        "company_name": company.name,
        "company_domain": company.domain or "",
        "linkedin_slug": company.linkedin_slug,
        "employees": company.employees or "",
        "size_band": company.size_band or "",
        "decision": company.decision,
        "confidence": company.confidence or "",
        "fit_score": company.fit_score if company.fit_score is not None else "",
        "rationale": company.rationale or "",
        "checked_at": company.checked_at.isoformat() if company.checked_at else "",
    }


def _offline_fit(company: Company) -> FitDecision:
    """Heuristic ICP decision over LinkedIn metadata (offline/fixture mode only).

    Cannot read the website, so it reasons over the scraped industry, specialties,
    description, and job-location countries. Clearly tagged source so a reviewer
    never mistakes a fixture run for a real web-grounded verdict.
    """
    blob = " ".join(filter(None, [
        company.industry, company.description, " ".join(company.specialties),
    ])).lower()
    target_hits = [k for k in icp.TARGET_INDUSTRY_KEYWORDS if k in blob]
    disq_hits = [k for k in icp.DISQUALIFIER_KEYWORDS if k in blob]
    in_scope = any((c or "").strip().lower() in icp.IN_SCOPE_COUNTRIES for c in company.countries)

    if disq_hits and not target_hits:
        decision, score, conf = "not_fit", 10, "medium"
        why = f"LinkedIn signals a disqualified category ({', '.join(disq_hits)})."
    elif target_hits and in_scope:
        decision, score, conf = "fit", min(90, 60 + 10 * len(target_hits)), "medium"
        why = (f"LinkedIn industry/specialties match ICP biotech/pharma signals "
               f"({', '.join(target_hits[:3])}); hiring in-scope EU locations "
               f"({', '.join(company.countries) or 'EU'}).")
    elif target_hits and not in_scope:
        decision, score, conf = "not_fit", 30, "low"
        why = "Industry matches ICP but no in-scope EU/EEA/UK/CH/Norway hiring location was seen."
    else:
        decision, score, conf = "not_fit", 20, "low"
        why = "LinkedIn metadata does not clearly match the ICP target industries."

    return FitDecision(decision=decision, rationale=f"[offline heuristic] {why}",
                       confidence=conf, fit_score=score, source="offline_heuristic")
