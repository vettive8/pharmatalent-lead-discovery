"""Normalization helpers for matching and deduplication.

Company-name rules come from ACTIVE_CLIENTS.md ("Required normalization"); the
LinkedIn-URL and full-name canonicalization come from DMM.md ("Deduplication").
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

# Legal suffixes to strip (ACTIVE_CLIENTS.md) plus a few common forms that show
# up in real LinkedIn data (NV, AB, Group, Corporation...). Stored WITHOUT dots
# because normalize() strips dots first, so "N.V." -> "nv", "S.p.A." -> "spa".
# Longest-first so multi-token suffixes ("& co kg") are removed before "co".
_LEGAL_SUFFIXES = [
    "& co kg", "sas", "spa", "sa", "nv", "gmbh", "ag", "se", "inc", "ltd",
    "llc", "plc", "ab", "oyj", "oy", "a/s", "holding", "holdings", "group",
    "corporation", "corp", "co", "company", "international",
]
_LEGAL_SUFFIXES.sort(key=len, reverse=True)

# Multi-part public suffixes we must keep together when finding the root label.
_MULTI_TLDS = {"co.uk", "ac.uk", "org.uk", "com.au", "co.jp", "com.br"}


def strip_accents(text: str) -> str:
    """Drop diacritics: 'Müller' -> 'Muller', 'Île-de-France' -> 'Ile-de-France'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_company_name(name: str | None) -> str:
    """Normalize a company name for matching (ACTIVE_CLIENTS.md rules).

    1. lowercase  2. strip parenthetical country/region tags
    3. strip legal suffixes  4. collapse whitespace.
    """
    if not name:
        return ""
    text = strip_accents(name).lower()
    # 2. Remove parenthetical tags, e.g. "roche (switzerland)" -> "roche".
    text = re.sub(r"\([^)]*\)", " ", text)
    # Drop dots so dotted suffixes collapse ("N.V." -> "nv"); commas -> space.
    # Hyphens and slashes are kept (e.g. "sobi - ...", Danish "a/s").
    text = text.replace(".", "")
    text = text.replace(",", " ")
    text = _collapse_ws(text)
    # 3. Strip a trailing legal suffix (repeat to catch stacked suffixes).
    changed = True
    while changed:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            if text == suffix:
                continue
            if text.endswith(" " + suffix):
                text = text[: -(len(suffix) + 1)]
                changed = True
                break
    return _collapse_ws(text)


def normalize_full_name(name: str | None) -> str:
    """Lowercase, accent-stripped, single-spaced person name (DMM.md dedup)."""
    if not name:
        return ""
    return _collapse_ws(strip_accents(name).lower())


def canonicalize_linkedin_url(url: str | None) -> str:
    """Canonicalize a LinkedIn profile URL for dedup (DMM.md): drop query params,
    trailing slash, and language prefixes; lowercase host + path.
    """
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    # Strip language sub-paths like /in/foo?... and locale hosts (de.linkedin.com).
    if host.endswith("linkedin.com"):
        host = "www.linkedin.com"
    path = parts.path.rstrip("/").lower()
    # Drop a leading locale segment such as /en/in/... -> /in/...
    path = re.sub(r"^/[a-z]{2}(?=/in/)", "", path)
    return f"https://{host}{path}" if path else f"https://{host}"


def root_domain(value: str | None) -> str:
    """Extract a registrable root label from a URL or domain.

    'https://www.biontech.de/careers' -> 'biontech'; 'roche.com' -> 'roche'.
    Used for domain-based active-client matching and contact dedup fallback.
    """
    if not value:
        return ""
    value = value.strip().lower()
    if "://" in value:
        value = urlsplit(value).netloc or urlsplit(value).path
    value = value.split("/")[0]
    if value.startswith("www."):
        value = value[4:]
    if not value or "." not in value:
        return value
    # Keep multi-part TLDs together (biontech.co.uk -> biontech).
    for tld in _MULTI_TLDS:
        if value.endswith("." + tld):
            labels = value[: -(len(tld) + 1)].split(".")
            return labels[-1] if labels else value
    labels = value.split(".")
    return labels[-2] if len(labels) >= 2 else labels[0]
