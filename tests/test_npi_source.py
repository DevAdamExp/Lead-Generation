"""Tests for the NPPES NPI Registry source (network-free)."""
from backend.services import npi_source as npi


def test_is_medical_niche():
    assert npi.is_medical_niche("Medical clinic")
    assert npi.is_medical_niche("Dentist")
    assert npi.is_medical_niche("Pediatric Urgent Care")
    assert not npi.is_medical_niche("plumber")
    assert not npi.is_medical_niche("restaurant")
    assert not npi.is_medical_niche("")


def test_parse_location():
    assert npi._parse_location("Los Angeles, CA") == ("Los Angeles", "CA")
    assert npi._parse_location("Austin, Texas") == ("Austin", "TX")


def test_parse_location_strips_directional():
    city, state = npi._parse_location("Downtown Los Angeles, CA")
    assert city == "Los Angeles" and state == "CA"


def test_taxonomy_term():
    assert npi._taxonomy_term("Medical clinic") == "clinic"
    assert npi._taxonomy_term("dentist") == "dentist"
    assert npi._taxonomy_term("pediatric clinic") == "clinic"


def test_norm_full_record():
    res = {
        "number": "123",
        "basic": {"organization_name": "Acme Health"},
        "addresses": [{
            "address_purpose": "LOCATION", "address_1": "1 Main St",
            "city": "Los Angeles", "state": "CA", "postal_code": "90001",
            "telephone_number": "213-555-1212",
        }],
        "taxonomies": [{"primary": True, "desc": "Clinic/Center"}],
    }
    r = npi._norm(res)
    assert r["name"] == "Acme Health"
    assert r["phone"] == "213-555-1212"
    assert r["category"] == "Clinic/Center"
    assert r["source"] == "npi"
    assert "1 Main St" in r["address"] and "90001" in r["address"]
    assert "123" in r["source_url"]


def test_norm_rejects_nameless():
    assert npi._norm({"basic": {}, "addresses": []}) is None


def test_search_gates_nonmedical():
    assert npi.search_npi("plumber", "Austin, TX") == []


def test_search_gates_non_us():
    assert npi.search_npi("dentist", "London", country="uk") == []


def test_search_paginates_and_dedups(monkeypatch):
    calls = {"n": 0}

    def fake_query(params):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{
                "number": str(i),
                "basic": {"organization_name": f"Org {i}"},
                "addresses": [{"address_purpose": "LOCATION",
                               "telephone_number": f"2130000{i:03d}"}],
                "taxonomies": [{"primary": True, "desc": "Clinic/Center"}],
            } for i in range(5)]
        return []

    monkeypatch.setattr(npi, "_query", fake_query)
    r = npi.search_npi("dentist", "Austin, TX", limit=50)
    assert len(r) == 5
    assert all(x["source"] == "npi" for x in r)


def test_search_caches_repeat_city(monkeypatch):
    """Directional tiles parse to the same city — NPI must serve them from cache,
    not re-hit the API."""
    npi._CACHE.clear()
    calls = {"n": 0}

    def fake_query(params):
        calls["n"] += 1
        return [{"number": "1", "basic": {"organization_name": "Org 1"},
                 "addresses": [{"address_purpose": "LOCATION",
                                "telephone_number": "2135551212"}],
                 "taxonomies": [{"primary": True, "desc": "Clinic/Center"}]}]

    monkeypatch.setattr(npi, "_query", fake_query)
    r1 = npi.search_npi("Medical clinic", "Los Angeles, CA", limit=40)
    after_first = calls["n"]
    # A directional variant parses to the same city -> should be a cache hit.
    r2 = npi.search_npi("Medical clinic", "Downtown Los Angeles, CA", limit=40)
    assert calls["n"] == after_first          # no new API calls
    assert [x["name"] for x in r1] == [x["name"] for x in r2]
    # cached result is copied, not the same object (callers mutate safely)
    assert r1 is not r2
