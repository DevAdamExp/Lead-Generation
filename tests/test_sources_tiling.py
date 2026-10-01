"""Tests for tiling, cross-tile merge/dedup, ranking, and cross-run exclude."""
from backend.services import sources as S


def test_build_tiles_with_region():
    tiles = S.build_tiles("Medical clinic", "Los Angeles, CA")
    assert ("Medical clinic", "Los Angeles, CA") in tiles
    assert ("Medical clinic", "Downtown Los Angeles, CA") in tiles
    assert any(q.startswith("best ") for q, _ in tiles)
    assert len(tiles) == len(set(tiles))            # no duplicate tiles


def test_build_tiles_no_region():
    tiles = S.build_tiles("plumber", "Austin")
    assert ("plumber", "Downtown Austin") in tiles
    assert ("plumber", "Austin") in tiles


def test_merge_pool_dedups_and_unions_sources():
    pool = []
    S._merge_pool(pool, [{"name": "Elite Clinic", "phone": "213-555-0142",
                          "sources": ["google_maps"]}])
    S._merge_pool(pool, [{"name": "Elite Clinic", "phone": None,
                          "website": "http://elite.com", "sources": ["npi"]}])
    assert len(pool) == 1
    assert set(pool[0]["sources"]) == {"google_maps", "npi"}
    assert pool[0]["website"] == "http://elite.com"   # blank field filled from dup


def test_merge_sources_counts_phone_corroboration():
    # Same business, same phone from two sources → phone_source_count == 2.
    merged = S.merge_sources({
        "google_maps": [{"name": "Elite Clinic", "phone": "(213) 555-0142"}],
        "npi":         [{"name": "Elite Clinic", "phone": "213-555-0142"}],
    })
    assert len(merged) == 1
    assert merged[0]["phone_source_count"] == 2


def test_merge_sources_single_source_phone_count_one():
    merged = S.merge_sources({
        "google_maps": [{"name": "Solo Biz", "phone": "(213) 555-0142"}],
    })
    assert merged[0]["phone_source_count"] == 1


def test_merge_pool_keeps_distinct_businesses():
    pool = []
    S._merge_pool(pool, [{"name": "Alpha Medical Center", "phone": "111-111-1111",
                          "sources": ["google_maps"]}])
    S._merge_pool(pool, [{"name": "Zeta Family Practice", "phone": "222-222-2222",
                          "sources": ["google_maps"]}])
    assert len(pool) == 2


def test_verify_likelihood_ranking():
    maps = {"sources": ["google_maps"], "has_google_maps": True}
    npi_ = {"sources": ["npi"], "phone": "x"}
    noise = {"sources": ["hotfrog"]}
    ranked = sorted([noise, npi_, maps], key=S._verify_likelihood, reverse=True)
    assert ranked[0] == maps
    assert ranked[-1] == noise


def test_gather_leads_tiled_applies_exclude(monkeypatch):
    def fake_gather(q, loc, country, limit, only=None):
        return [
            {"name": "Keep Clinic", "phone": "111-222-3333", "sources": ["google_maps"]},
            {"name": "Drop Clinic", "phone": "999-888-7777", "sources": ["google_maps"]},
        ]
    monkeypatch.setattr(S, "gather_leads", fake_gather)

    pool = S.gather_leads_tiled(
        "Medical clinic", "Los Angeles, CA", candidate_target=10,
        exclude=lambda r: r.get("name") == "Drop Clinic",
    )
    names = {p["name"] for p in pool}
    assert "Keep Clinic" in names
    assert "Drop Clinic" not in names


def test_gather_leads_tiled_stops_at_target(monkeypatch):
    from threading import Lock
    counter = {"n": 0}
    lock = Lock()

    def fake_gather(q, loc, country, limit, only=None):
        # `only={"google_maps"}` for tiles; the once-call asks for hotfrog/npi/…
        # Each call returns a unique business so the pool grows by 1 per call.
        with lock:
            counter["n"] += 1
            n = counter["n"]
        return [{"name": f"Clinic {n}", "phone": f"555-000-{n:04d}",
                 "sources": ["google_maps"]}]
    monkeypatch.setattr(S, "gather_leads", fake_gather)

    pool = S.gather_leads_tiled("Medical clinic", "LA, CA", candidate_target=3)
    # Stops once target reached; concurrent in-flight tiles may overshoot by up to
    # TILE_CONCURRENCY, but the final pool is sliced to the target.
    assert len(pool) == 3
