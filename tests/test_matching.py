from pipeline.matching import ActiveClientMatcher


def test_exact_and_suffix_match():
    m = ActiveClientMatcher()
    assert m.match(name="BioNTech SE").client_name == "biontech"
    assert m.match(name="Bayer AG").client_name == "bayer"
    assert m.match(name="MorphoSys AG").client_name == "morphosys"


def test_slug_match_for_subsidiary_naming():
    m = ActiveClientMatcher()
    # "Roche Diagnostics GmbH" doesn't normalize to "roche", but the slug does.
    res = m.match(name="Roche Diagnostics GmbH", slug="roche")
    assert res is not None and res.client_name == "roche" and res.method == "slug"


def test_icon_plc_matches_via_normalized_name():
    m = ActiveClientMatcher()
    res = m.match(name="ICON plc", slug="icon-plc")
    assert res is not None and res.client_name == "icon"


def test_non_client_does_not_match():
    m = ActiveClientMatcher()
    for name in ["Idorsia Pharmaceuticals Ltd", "argenx", "Genmab", "Tubulis GmbH"]:
        assert m.match(name=name) is None


def test_short_names_only_match_exactly_not_fuzzily():
    m = ActiveClientMatcher()
    # 'ican' must not fuzzy-match the 4-letter client 'icon'.
    assert m.match(name="Ican Biotech") is None
