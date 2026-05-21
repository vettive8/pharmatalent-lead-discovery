import json
from pathlib import Path

from pipeline.models import PersonCandidate
from pipeline.stages.validate import _offline_validate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_functional_head_is_kept():
    res = _offline_validate("Regulatory Affairs Manager", "Director Regulatory Affairs", "201-1000")
    assert res.decision == "yes"


def test_peer_level_associate_is_dropped():
    res = _offline_validate("Clinical Trial Manager", "Senior Clinical Research Associate", "201-1000")
    assert res.decision == "no"


def test_talent_leader_only_qualifies_at_small_company():
    assert _offline_validate("Clinical Trial Manager", "Head of Talent Acquisition EU", "50-200").decision == "yes"


def test_people_search_fixture_parses_and_filters():
    """The real AI Ark fixture: the CRA candidate must be dropped, the leaders kept."""
    data = json.loads((FIXTURES / "sample_people_search.json").read_text(encoding="utf-8"))
    candidates = [PersonCandidate.from_result(r, "ai_ark") for r in data["results"]]
    assert len(candidates) == 3
    decisions = {c.full_name: _offline_validate("Senior Regulatory Affairs Manager", c.title or "", "201-1000").decision
                 for c in candidates}
    assert decisions["Sandra Müller"] == "yes"      # Head of Talent Acquisition EU
    assert decisions["Markus Frei"] == "yes"        # Director Regulatory Affairs
    assert decisions["Elena Rossi"] == "no"         # Senior Clinical Research Associate (peer)
