"""ICP, active-client, and DMM constants — the single source of truth.

Everything here is transcribed from ICP.md, ACTIVE_CLIENTS.md, and DMM.md so the
business rules live in one auditable place rather than scattered through stages.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Job scrape — titleSearch (ICP.md, Half 1), OR-combined.
# ---------------------------------------------------------------------------
TITLE_SEARCH = [
    # Regulatory Affairs
    "Regulatory Affairs Manager",
    "Senior Regulatory Affairs Manager",
    "Director Regulatory Affairs",
    "Head of Regulatory Affairs",
    "Regulatory Affairs Specialist",
    # Clinical Operations & Research
    "Clinical Operations Manager",
    "Director Clinical Operations",
    "Head of Clinical Operations",
    "Senior Clinical Research Associate",
    "Clinical Trial Manager",
    "Clinical Project Manager",
    # Pharmacovigilance & Drug Safety
    "Pharmacovigilance Manager",
    "Drug Safety Officer",
    "Qualified Person for Pharmacovigilance",
    # Medical Affairs
    "Medical Affairs Lead",
    "Medical Science Liaison",
    "Senior Medical Advisor",
]

EMPLOYMENT_TYPES = ["FULL_TIME", "CONTRACTOR"]

# ---------------------------------------------------------------------------
# Company size band (ICP.md, Half 2). Outside 50–2000 employees => not a fit.
# ---------------------------------------------------------------------------
ICP_MIN_EMPLOYEES = 50
ICP_MAX_EMPLOYEES = 2000


# ---------------------------------------------------------------------------
# DMM target titles by size band (DMM.md), in priority order.
# ---------------------------------------------------------------------------
SIZE_BANDS: list[tuple[int, int, str, list[str]]] = [
    (50, 200, "50-200", [
        "Head of Talent", "Head of People", "Head of HR",
        "Director Regulatory Affairs", "Director Clinical Operations",
    ]),
    (201, 1000, "201-1000", [
        "VP People", "VP Talent Acquisition",
        "Senior Director Regulatory Affairs", "Senior Director Clinical Operations",
        "Director Talent Acquisition Europe",
    ]),
    (1001, 2000, "1001-2000", [
        "Global Head of Talent", "EU Head of Talent Acquisition",
        "VP Regulatory Affairs EU", "VP Clinical Operations EU",
        "Senior Director Talent Acquisition",
    ]),
]


def size_band_for(employees: int | None) -> tuple[str, list[str]] | None:
    """Return ``(band_label, target_titles)`` for an employee count, or None if
    the count falls outside the ICP 50–2000 band (i.e. not a fit on size)."""
    if employees is None:
        return None
    for low, high, label, titles in SIZE_BANDS:
        if low <= employees <= high:
            return label, titles
    return None


# ---------------------------------------------------------------------------
# ICP industry signal + disqualifiers (ICP.md, Half 2). Used to build the LLM
# fit-check prompt context and to drive the offline heuristic fallback.
# ---------------------------------------------------------------------------
TARGET_INDUSTRY_KEYWORDS = [
    "biotech", "biotechnology", "pharma", "pharmaceutical", "drug discovery",
    "drug development", "gene therapy", "cell therapy", "mrna", "immunotherapy",
    "oncology", "rare disease", "clinical-stage", "clinical stage",
    "contract research", "cro", "cdmo",
]

DISQUALIFIER_KEYWORDS = [
    "university", "universities", "academic", "research institute", "institut",
    "hospital", "clinic", "klinik", "staffing", "recruitment", "recruiting",
    "consulting", "consultancy", "medical device", "medical devices",
    "cosmetic", "cosmetics", "nutraceutical", "food supplement", "supplements",
]


# ---------------------------------------------------------------------------
# Geography (ICP.md, Half 2): EU / EEA / UK / CH / Norway are in scope.
# A company with at least one operational/hiring location here qualifies even
# if HQ is elsewhere. Names and ISO-2 codes both included for matching.
# ---------------------------------------------------------------------------
IN_SCOPE_COUNTRIES = {
    "germany", "switzerland", "netherlands", "belgium", "denmark", "sweden",
    "ireland", "france", "united kingdom", "spain", "italy", "austria",
    "finland", "norway", "iceland", "luxembourg", "portugal", "greece",
    "poland", "czechia", "czech republic", "slovakia", "slovenia", "hungary",
    "romania", "bulgaria", "croatia", "estonia", "latvia", "lithuania",
    "liechtenstein", "malta", "cyprus",
}
IN_SCOPE_COUNTRY_CODES = {
    "de", "ch", "nl", "be", "dk", "se", "ie", "fr", "gb", "uk", "es", "it",
    "at", "fi", "no", "is", "lu", "pt", "gr", "pl", "cz", "sk", "si", "hu",
    "ro", "bg", "hr", "ee", "lv", "lt", "li", "mt", "cy",
}

# EU region buckets for the people-search cascade (DMM.md step 3).
EU_REGIONS: dict[str, set[str]] = {
    "DACH": {"germany", "austria", "switzerland"},
    "Benelux": {"belgium", "netherlands", "luxembourg"},
    "Nordics": {"denmark", "sweden", "norway", "finland", "iceland"},
    "UKI": {"united kingdom", "ireland"},
    "Southern Europe": {"spain", "italy", "portugal", "greece"},
    "Western Europe": {"france"},
}


def eu_region_for(country: str | None) -> str | None:
    if not country:
        return None
    key = country.strip().lower()
    for region, members in EU_REGIONS.items():
        if key in members:
            return region
    return None


# ---------------------------------------------------------------------------
# Active clients (ACTIVE_CLIENTS.md). These must never reach companies/contacts.
# ---------------------------------------------------------------------------
ACTIVE_CLIENTS = [
    # Big pharma
    "Pfizer", "Bayer", "Novartis", "Roche", "Sanofi",
    "GSK", "GlaxoSmithKline", "AstraZeneca", "Merck KGaA", "Boehringer Ingelheim",
    # Mid biotech
    "BioNTech", "CureVac", "MorphoSys", "Evotec",
    # CROs
    "ICON", "ICON plc", "IQVIA",
]
