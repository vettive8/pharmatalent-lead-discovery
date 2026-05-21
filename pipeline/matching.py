"""Active-client matching (ACTIVE_CLIENTS.md).

Real LinkedIn names arrive with legal suffixes, country tags, casing drift, and
slug forms. We match a scraped company against the active-client list using the
documented precedence: exact normalized name -> slug -> root domain -> fuzzy
(Levenshtein <= 2 OR similarity >= 90%). Fuzzy is length-guarded so 3-4 letter
names ("GSK", "ICON") only match exactly and don't collide with look-alikes.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein
from rapidfuzz.fuzz import ratio

from .icp import ACTIVE_CLIENTS
from .normalize import normalize_company_name, root_domain

_MIN_FUZZY_LEN = 5
_FUZZY_RATIO = 90.0
_FUZZY_LEVENSHTEIN = 2


@dataclass
class MatchResult:
    client_name: str       # normalized active-client name (for the CSV)
    method: str            # 'exact' | 'slug' | 'domain' | 'fuzzy'


class ActiveClientMatcher:
    def __init__(self, clients: list[str] | None = None) -> None:
        names = clients if clients is not None else ACTIVE_CLIENTS
        # Map normalized -> display(normalized) and dedupe (GSK/GlaxoSmithKline
        # both normalize distinctly, ICON/ICON plc both normalize to 'icon').
        self._normalized: dict[str, str] = {}
        for name in names:
            norm = normalize_company_name(name)
            if norm:
                self._normalized.setdefault(norm, norm)

    def _candidate_strings(self, *, name: str, slug: str | None, domain: str | None) -> list[tuple[str, str]]:
        """(candidate, method) pairs to test, in precedence order."""
        cands: list[tuple[str, str]] = []
        cands.append((normalize_company_name(name), "exact"))
        if slug:
            cands.append((normalize_company_name(slug.replace("-", " ")), "slug"))
        if domain:
            cands.append((root_domain(domain), "domain"))
        return [(c, m) for c, m in cands if c]

    def match(self, *, name: str, slug: str | None = None, domain: str | None = None) -> MatchResult | None:
        candidates = self._candidate_strings(name=name, slug=slug, domain=domain)

        # 1-3: exact / slug / domain — direct hits on the normalized set.
        for cand, method in candidates:
            if cand in self._normalized:
                return MatchResult(self._normalized[cand], method)

        # 4: fuzzy — only for sufficiently long candidates, to avoid collisions.
        for cand, _ in candidates:
            if len(cand) < _MIN_FUZZY_LEN:
                continue
            for norm in self._normalized:
                if len(norm) < _MIN_FUZZY_LEN:
                    continue
                if Levenshtein.distance(cand, norm) <= _FUZZY_LEVENSHTEIN or ratio(cand, norm) >= _FUZZY_RATIO:
                    return MatchResult(self._normalized[norm], "fuzzy")
        return None
