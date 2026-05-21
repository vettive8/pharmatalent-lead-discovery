from pipeline import normalize


def test_company_name_strips_suffixes_and_tags():
    cases = {
        "Roche (Switzerland)": "roche",
        "Roche Diagnostics GmbH": "roche diagnostics",
        "BioNTech SE": "biontech",
        "Galapagos NV": "galapagos",
        "InflaRx N.V.": "inflarx",
        "ICON plc": "icon",
        "Idorsia Pharmaceuticals Ltd": "idorsia pharmaceuticals",
        "Basilea Pharmaceutica International AG": "basilea pharmaceutica",
    }
    for raw, expected in cases.items():
        assert normalize.normalize_company_name(raw) == expected


def test_full_name_accent_stripped_and_spaced():
    assert normalize.normalize_full_name("Sandra  Müller") == "sandra muller"
    assert normalize.normalize_full_name("Élena   Rossi") == "elena rossi"


def test_linkedin_url_canonicalization():
    a = normalize.canonicalize_linkedin_url("https://www.linkedin.com/in/Sandra-Mueller/?trk=x")
    b = normalize.canonicalize_linkedin_url("http://de.linkedin.com/in/sandra-mueller/")
    assert a == b == "https://www.linkedin.com/in/sandra-mueller"


def test_root_domain():
    assert normalize.root_domain("https://www.biontech.de/careers") == "biontech"
    assert normalize.root_domain("roche.com") == "roche"
    assert normalize.root_domain("mereo.co.uk") == "mereo"
