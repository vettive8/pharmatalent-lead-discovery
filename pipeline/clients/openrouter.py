"""OpenRouter client — the two LLM stages.

Two stages, two models (TOOLS.md §3):

* **ICP fit-check** uses a web-browsing model (default ``perplexity/sonar``) so
  the verdict is grounded in the company's real website, not the LinkedIn
  snippet. This is the expensive call, so it runs once per qualifying company.
* **Hiring-manager validation** uses a cheap model (default
  ``deepseek/deepseek-chat``) at temperature 0 — it runs once per candidate
  person, so cost per call matters more than reasoning depth.

This module is the API boundary only: it builds prompts, calls the endpoint, and
parses JSON out. The offline heuristics live in the stage modules.
"""

from __future__ import annotations

import json
import re

import httpx

from .. import icp
from ..config import Settings
from ..logging_setup import get_logger
from ..models import Company, FitDecision, ValidationResult
from .http import with_retry

log = get_logger("openrouter")

_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# ---- ICP fit-check prompt ---------------------------------------------------
_FITCHECK_SYSTEM = """\
You are an analyst qualifying companies against an Ideal Customer Profile (ICP)
for PharmaTalent Europe, a recruitment agency that places PhD-level pharma/biotech
talent. You have web access: look up the company's official website and recent
public information before deciding. Base your verdict on what the website actually
says, not on the company name alone.

ICP — a company is a FIT only if ALL hold:
- Industry is one of: biotech (drug discovery/development, gene/cell therapy, mRNA,
  immunotherapy, oncology, rare disease), small-to-mid clinical/commercial-stage
  pharma, a CRO, or a CDMO that runs clinical trials in-house.
- It has at least one operational/hiring location in the EU/EEA/UK/Switzerland/Norway
  (HQ may be anywhere — a US/Asian biotech with a European office is in scope).
- Company size is 50–2000 employees GLOBALLY. The headcount you are given may be a
  single local/subsidiary entity; if the company is a division, affiliate, or
  subsidiary of a parent/group whose GLOBAL headcount exceeds ~2000 (e.g. a Big
  Pharma local affiliate such as an "MSD"/Merck, Pfizer, Novartis, Roche country
  office), mark it not_fit even though the local entity looks small.

Disqualify (decision = not_fit) if ANY apply: pure academia/universities/research
institutes; hospitals/clinics; generic-drug-only makers under 50 staff; fully
remote with no EU legal entity; staffing/recruitment/consulting agencies;
medical-device firms with no drug-development arm; cosmetic/nutraceutical/food-
supplement companies.

Respond with STRICT JSON only, no prose around it:
{"decision": "fit" | "not_fit",
 "rationale": "1-3 sentences that reference what you found on the company website",
 "confidence": "high" | "medium" | "low",
 "fit_score": 0-100,
 "domain": "the company's primary website domain, e.g. example.com"}"""

# ---- Hiring-manager validation prompt (improved from the fixture starter) ----
_VALIDATION_SYSTEM = """\
You are an expert at deciding whether a person could plausibly be the hiring
manager or final decision-maker for a specific open job.

Rules:
- Hiring managers are the role-level owner or the function head one to two levels
  above the open role.
- Talent / People / HR leaders qualify ONLY if the open role is junior/mid-level
  AND the company is under ~200 employees. For senior roles or larger companies,
  the functional decision-maker (e.g. Director Regulatory Affairs for an RA Manager
  role) outranks a generic Talent leader.
- Geography: a global/EU Head of Talent based abroad can still own a small-biotech
  role; a junior local recruiter cannot own a senior global role.
- A same-company peer at the SAME level as the open role is NOT the decision-maker.
- When in doubt, answer "no" — a false positive wastes outreach budget.

Answer with STRICT JSON only: {"decision": "yes" | "no", "reason": "<one sentence>"}"""


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            # Optional attribution headers OpenRouter recommends.
            "HTTP-Referer": "https://github.com/vettive8/pharmatalent-lead-discovery",
            "X-Title": "PharmaTalent Lead Pipeline",
        }

    @with_retry
    def _chat(self, model: str, system: str, user: str, *, json_mode: bool = True) -> str:
        body: dict = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Not every model honors response_format; harmless when ignored.
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(_BASE_URL, headers=self._headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -- fit-check ----------------------------------------------------------
    def fit_check(self, company: Company) -> FitDecision:
        # Web models (perplexity/sonar) reject response_format=json_object, so we
        # don't request it here; the prompt demands strict JSON and we parse it out.
        user = _build_fitcheck_user(company)
        raw = self._chat(self.settings.fitcheck_model, _FITCHECK_SYSTEM, user, json_mode=False)
        return _parse_fit_decision(raw)

    # -- validation ---------------------------------------------------------
    def validate(self, payload: dict) -> ValidationResult:
        user = render_validation_user(payload)
        raw = self._chat(self.settings.validation_model, _VALIDATION_SYSTEM, user)
        return _parse_validation(raw)


def _build_fitcheck_user(company: Company) -> str:
    return (
        f"Company: {company.name}\n"
        f"LinkedIn industry: {company.industry or 'unknown'}\n"
        f"Employees (LinkedIn, may be a LOCAL/subsidiary entity not the global group): "
        f"{company.employees or 'unknown'}\n"
        f"Headquarters: {company.headquarters or 'unknown'}\n"
        f"Specialties: {', '.join(company.specialties) or 'unknown'}\n"
        f"LinkedIn description: {company.description or 'n/a'}\n"
        f"Job locations seen in scrape: {', '.join(company.cities + company.countries) or 'unknown'}\n\n"
        "Research this company's website and decide ICP fit. Return the JSON object."
    )


def render_validation_user(p: dict) -> str:
    """Render the hiring-manager validation user prompt (DMM.md inputs)."""
    return (
        "SCRAPED JOB\n-----------\n"
        f"Title:        {p.get('scraped_job_title', '')}\n"
        f"Location:     {p.get('scraped_job_location', '')}\n"
        f"Company:      {p.get('company_name', '')}\n"
        f"Company size: {p.get('company_size_band', '')}\n"
        "Description (first ~500 chars):\n"
        f"{p.get('scraped_job_description_snippet', '')}\n\n"
        "CANDIDATE PERSON\n----------------\n"
        f"Full name:     {p.get('person_full_name', '')}\n"
        f"Current title: {p.get('person_title', '')}\n"
        f"Location:      {p.get('person_location', '')}\n"
        f"About:         {p.get('person_about_snippet', '')}\n\n"
        "Could this person plausibly be the hiring manager or final decision-maker "
        "for this specific role?"
    )


# ---- JSON parsing helpers ---------------------------------------------------
def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response (handles code fences)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _parse_fit_decision(raw: str) -> FitDecision:
    data = _extract_json(raw)
    decision = "fit" if str(data.get("decision", "")).lower().strip() in {"fit", "yes", "true"} else "not_fit"
    score = data.get("fit_score")
    try:
        score = max(0, min(100, int(score))) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return FitDecision(
        decision=decision,
        rationale=str(data.get("rationale", "")).strip() or "No rationale returned.",
        confidence=str(data.get("confidence", "medium")).lower().strip() or "medium",
        fit_score=score,
        domain=(str(data.get("domain")).strip().lower() or None) if data.get("domain") else None,
        source="llm_web",
    )


def _parse_validation(raw: str) -> ValidationResult:
    data = _extract_json(raw)
    decision = "yes" if str(data.get("decision", "")).lower().strip() in {"yes", "true"} else "no"
    return ValidationResult(decision=decision, reason=str(data.get("reason", "")).strip() or "No reason returned.")
