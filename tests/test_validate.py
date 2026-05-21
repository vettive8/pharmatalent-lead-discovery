import json
from pathlib import Path

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


def test_validation_against_provided_people_fixture():
    """Drive the validation heuristic with the people from the provided fixture:
    the CRA peer must be dropped; the talent + functional leaders kept."""
    data = json.loads((FIXTURES / "sample_people_search.json").read_text(encoding="utf-8"))
    people = data["results"]
    assert len(people) == 3
    decisions = {p["full_name"]: _offline_validate("Senior Regulatory Affairs Manager",
                                                   p.get("title") or "", "201-1000").decision
                 for p in people}
    assert decisions["Sandra Müller"] == "yes"      # Head of Talent Acquisition EU
    assert decisions["Markus Frei"] == "yes"        # Director Regulatory Affairs
    assert decisions["Elena Rossi"] == "no"         # Senior Clinical Research Associate (peer)
