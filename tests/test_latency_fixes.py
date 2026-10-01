"""Checks for the audit's latency fixes — the bits with real branching.

Covers: the shared per-lead page cache, the SMTP port-25 kill switch, and the
Maps stage's plan/fetch/apply split (the split exists so the browser work can run
off-thread without touching the Session, so `apply` must stay a pure function of
what `plan` selected).
"""
import json
from types import SimpleNamespace

import pytest


# ── shared page cache (business_intel._fetch / contact_finder._fetch) ──────────

class _CountingClient:
    """Minimal httpx.Client stand-in that records how many GETs it served."""
    def __init__(self, status=200, body="<html>ok</html>"):
        self.calls = 0
        self._status, self._body = status, body

    def get(self, url, **kw):
        self.calls += 1
        return SimpleNamespace(status_code=self._status, text=self._body)


@pytest.mark.parametrize("module", ["business_intel", "contact_finder"])
def test_fetch_cache_serves_repeat_urls_without_a_second_request(module):
    mod = __import__(f"backend.services.{module}", fromlist=["_fetch"])
    client, cache = _CountingClient(), {}

    first = mod._fetch("https://x.test/about", client, cache)
    second = mod._fetch("https://x.test/about", client, cache)

    assert first == second == "<html>ok</html>"
    assert client.calls == 1, "second fetch of the same URL must hit the cache"


def test_fetch_cache_remembers_failures_too():
    """A dead path is worth remembering — otherwise the second crawler re-pays
    the full timeout on every 404 the first one already found."""
    from backend.services import contact_finder as cf
    client, cache = _CountingClient(status=404), {}

    assert cf._fetch("https://x.test/team", client, cache) is None
    assert cf._fetch("https://x.test/team", client, cache) is None
    assert client.calls == 1


def test_fetch_without_cache_still_refetches():
    """cache=None must preserve the old behaviour exactly."""
    from backend.services import contact_finder as cf
    client = _CountingClient()
    cf._fetch("https://x.test/", client)
    cf._fetch("https://x.test/", client)
    assert client.calls == 2


def test_both_crawlers_share_one_cache_for_an_overlapping_path():
    """The point of the fix: /about is on BOTH path lists, so the second module
    to run must not re-fetch it."""
    from backend.services import business_intel as bi, contact_finder as cf
    cache = {}
    intel_client, contact_client = _CountingClient(), _CountingClient()

    bi._fetch("https://x.test/about", intel_client, cache)
    cf._fetch("https://x.test/about", contact_client, cache)

    assert intel_client.calls == 1
    assert contact_client.calls == 0


# ── SMTP port-25 kill switch (verifier) ───────────────────────────────────────

def test_smtp_probing_stops_after_a_run_of_unreachable_mx(monkeypatch):
    """_SMTP_DEAD_DOMAINS is keyed by domain, so on a host with outbound port 25
    blocked every new lead used to pay a fresh connect timeout. After
    _SMTP_FAIL_LIMIT consecutive unreachable MXs we must stop probing entirely."""
    from backend.services import verifier as v
    v.clear_cache()

    probes = {"n": 0}
    monkeypatch.setattr(v, "_resolve_mx", lambda d, t=5: ["mx.%s" % d])

    def _dead_rcpt(mx_host, rcpt, timeout):
        probes["n"] += 1
        return None                      # unreachable: port 25 blocked
    monkeypatch.setattr(v, "_rcpt_code", _dead_rcpt)

    try:
        for i in range(v._SMTP_FAIL_LIMIT + 5):
            r = v.verify_email(f"a@d{i}.test", timeout=1)
            assert r["tier"] == "mx_only", "must degrade to MX-only, not go invalid"
            assert r["verified"] is True

        assert v._SMTP_UNAVAILABLE is True
        assert probes["n"] == v._SMTP_FAIL_LIMIT, (
            "probing must stop at the limit, not continue once per domain")
    finally:
        v.clear_cache()


def test_clear_cache_resets_the_kill_switch():
    from backend.services import verifier as v
    v._SMTP_UNAVAILABLE, v._SMTP_FAIL_STREAK = True, 99
    v.clear_cache()
    assert v._SMTP_UNAVAILABLE is False and v._SMTP_FAIL_STREAK == 0


# ── Maps stage plan / apply split (pipeline) ──────────────────────────────────

def _lead(**kw):
    base = dict(id="x", name="Acme", address="1 Main St, Springfield", phone=None,
                sources="", has_google_maps=False, google_rating=None,
                google_review_count=0, google_maps_url=None, google_is_open=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_maps_plan_skips_leads_that_do_not_need_a_lookup():
    from backend.workers.pipeline import _stage_maps_plan
    job = SimpleNamespace(location="Springfield, IL")

    leads = [
        _lead(id="need"),                                        # → planned
        _lead(id="already", has_google_maps=True),               # already on Maps
        _lead(id="npi", phone="+15551234567", sources="npi"),    # verified via NPI
        _lead(id="noname", name=None),                           # unusable
    ]
    planned = _stage_maps_plan(job, leads)

    assert [p["id"] for p in planned] == ["need"]
    assert planned[0]["city"] == "1 Main St"   # first address segment, as before


def test_maps_plan_falls_back_to_job_location_without_an_address():
    from backend.workers.pipeline import _stage_maps_plan
    job = SimpleNamespace(location="Springfield, IL")
    planned = _stage_maps_plan(job, [_lead(address=None)])
    assert planned[0]["city"] == "Springfield"


def test_maps_apply_never_blanks_leads_that_were_not_planned():
    """The regression this guards: applying results across the whole batch would
    wipe rating/reviews on Maps-sourced leads that were deliberately skipped."""
    from backend.workers.pipeline import _stage_maps_apply

    planned_lead = _lead(id="need")
    skipped = _lead(id="already", has_google_maps=True, google_rating=4.8,
                    google_review_count=120)
    committed = {"n": 0}
    db = SimpleNamespace(commit=lambda: committed.__setitem__("n", committed["n"] + 1))

    _stage_maps_apply(
        db, [planned_lead, skipped],
        [{"id": "need", "name": "Acme", "city": "Springfield"}],
        {"need": {"has_google_maps": True, "google_rating": 4.1,
                  "google_review_count": 9, "google_maps_url": "u",
                  "google_is_open": True}},
    )

    assert planned_lead.google_rating == 4.1 and planned_lead.has_google_maps
    assert skipped.google_rating == 4.8 and skipped.google_review_count == 120
    assert committed["n"] == 1


def test_maps_apply_is_a_noop_with_nothing_planned():
    from backend.workers.pipeline import _stage_maps_apply
    committed = {"n": 0}
    db = SimpleNamespace(commit=lambda: committed.__setitem__("n", committed["n"] + 1))
    _stage_maps_apply(db, [_lead()], [], {})
    assert committed["n"] == 0


# ── RDAP consolidation (owner_contact) ────────────────────────────────────────

def test_rdap_domain_is_cached_so_a_lead_pays_one_request(monkeypatch):
    """business_intel's domain-age lookup and owner_contact's registrant-phone
    lookup must share one RDAP call per domain."""
    from backend.services import owner_contact as oc
    from backend.services.business_intel import _extract_domain_age

    oc.rdap_domain.cache_clear()
    calls = {"n": 0}

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, **kw):
            calls["n"] += 1
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"events": [{"eventAction": "registration",
                                          "eventDate": "2011-04-05T00:00:00Z"}],
                              "entities": []},
            )

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _C())

    try:
        assert _extract_domain_age("acme.test") == 2011
        assert oc.rdap_registrant_phone("https://www.acme.test/x") is None
        assert calls["n"] == 1, "second consumer must hit the lru_cache"
    finally:
        oc.rdap_domain.cache_clear()


def test_domain_from_url_strips_scheme_www_and_port():
    from backend.services.owner_contact import domain_from_url
    assert domain_from_url("https://www.acme.test:8443/a/b") == "acme.test"
    assert domain_from_url("acme.test") == "acme.test"
    assert domain_from_url("") == ""


# ── owner_name plausibility gate (contact_finder) ─────────────────────────────

def test_person_name_gate_rejects_page_furniture():
    """Every value here is real, pulled from leads.db: 17 of 23 stored
    owner_names were page chrome that OWNER_PATTERNS matched as capitalised word
    runs. Each scored +5 lead points and +0.10 data_confidence, and was addressed
    directly in an LLM cold-email opener ("Hi Our Management Services, ...")."""
    from backend.services.contact_finder import is_plausible_person_name as ok

    real = ["Aharon Ben Elisha", "Evan Peterson", "Joe Vaughn",
            "Mark Wulff", "Michael Grant", "Sam Shukuri"]
    junk = ["About Bill", "About Us Turner", "Business Development",
            "Contact Us Explore Our", "Jon Green Building Testimonials",
            "Managing Partners Ashley", "Needs October",
            "Our Management Services Comprehensive", "Our Services At Vulcan",
            "Our Staff", "Our Team", "Our Team Are",
            "Portal Explore Additions Remodels", "Risk Management",
            "Russell Fuller In", "The Team Sustainability",
            "Tips Remodeling Ideas Jan"]

    assert [n for n in real if not ok(n)] == [], "rejected a real person"
    assert [n for n in junk if ok(n)] == [], "accepted page furniture"


def test_pitch_text_sanitiser_strips_markdown():
    """A live 187-lead run put markdown in 99 of them; XLSX/PDF render it as
    literal asterisks."""
    from backend.services.pitch_research import _clean_text
    assert _clean_text("**Trust Gap**: no SSL") == "Trust Gap: no SSL"
    assert _clean_text("## Heading\nbody") == "Heading body"
    assert _clean_text("`code` and __bold__") == "code and bold"
    assert _clean_text("  spaced   out  ") == "spaced out"
    assert _clean_text(None) is None
    assert _clean_text("   ") is None


def test_opener_with_unfilled_placeholder_is_dropped(monkeypatch):
    """Real failure from the run: the model leaked its own reasoning into the
    opener. Pasting that into an email is worse than having no opener."""
    import backend.services.pitch_research as pr
    monkeypatch.setattr(pr, "_call_ollama", lambda *a, **k: json.dumps({
        "pitch_angle": "**A website** that converts",
        "pain_points": ["no site", "no reviews"],
        "opener": "Curious how many clients in [LOCATION, assumed from context] are missing out?",
        "review_themes": [],
    }))
    out = pr.research_lead({"name": "Acme"}, provider="ollama")
    assert out["opener"] is None, "placeholder opener must be dropped"
    assert out["pitch_angle"] == "A website that converts", "markdown must be stripped"
    assert out["pain_points"] == "no site; no reviews"


def test_good_opener_survives_the_sanitiser(monkeypatch):
    import backend.services.pitch_research as pr
    monkeypatch.setattr(pr, "_call_ollama", lambda *a, **k: json.dumps({
        "pitch_angle": "Portfolio site", "pain_points": ["a", "b"],
        "opener": "Noticed your Instagram work but your site has no SSL.",
        "review_themes": [],
    }))
    out = pr.research_lead({"name": "Acme"}, provider="ollama")
    assert out["opener"] == "Noticed your Instagram work but your site has no SSL."


def test_truncated_llm_json_is_salvaged():
    """max_tokens=700 cut 6 of 175 leads off mid-string. The fields that DID
    land (pitch_angle is emitted first) must survive instead of the whole lead
    being dropped."""
    from backend.services.pitch_research import _loads_lenient

    cut = '{"pain_points":["no website","no reviews"],"pitch_angle":"A portfolio site bec'
    got = _loads_lenient(cut)
    assert got["pain_points"] == ["no website", "no reviews"]
    assert got["pitch_angle"].startswith("A portfolio site")

    # cut inside an array
    got = _loads_lenient('{"pitch_angle":"X","pain_points":["a","b",')
    assert got["pitch_angle"] == "X" and got["pain_points"] == ["a", "b"]

    # cut right after a key, before its value
    got = _loads_lenient('{"pitch_angle":"X","opener":')
    assert got["pitch_angle"] == "X" and "opener" not in got

    # intact and fenced JSON still work unchanged
    assert _loads_lenient('{"a":1}')["a"] == 1
    assert _loads_lenient('```json\n{"a":2}\n```')["a"] == 2

    with pytest.raises(json.JSONDecodeError):
        _loads_lenient("the model just wrote prose")


def test_partial_result_never_erases_existing_pitch_fields(monkeypatch):
    """Salvaged-from-truncation results are partial. A reply that carried
    pain_points but was cut before pitch_angle must not blank the pitch_angle
    the lead already had — that turns a stale value into no value."""
    import backend.services.pitch_research as pr

    lead = SimpleNamespace(name="Acme", pitch_angle="OLD pitch", pain_points="OLD pains",
                           opener="OLD opener", review_themes=None,
                           business_category=None, address=None, website=None,
                           website_cms=None, has_ssl=None, services=None,
                           description=None, google_rating=None, google_review_count=None,
                           google_is_open=None, price_level=None, external_platforms=None,
                           has_direct_commerce=None, marketing_stack=None,
                           runs_paid_ads=None, review_weaknesses=None,
                           review_lowest_texts=None, owner_name=None, owner_title=None,
                           employee_count=None, year_founded=None,
                           social_facebook=None, social_instagram=None,
                           social_linkedin=None, social_twitter=None)

    # research_leads_sync resolves the backend from settings — pin it to the
    # local path so the suite never touches the network.
    monkeypatch.setattr(pr, "_resolve_backend",
                        lambda model, url: (dict(provider="ollama", model=model, url=url), 1))
    # truncated before pitch_angle / opener were emitted
    monkeypatch.setattr(pr, "_call_ollama",
                        lambda *a, **k: '{"pain_points":["fresh pain","second"]')
    pr.research_leads_sync([lead], model="m", url="u")

    assert lead.pain_points == "fresh pain; second", "the field that landed must update"
    assert lead.pitch_angle == "OLD pitch", "missing field must not erase"
    assert lead.opener == "OLD opener", "missing field must not erase"


def test_a_rejected_opener_clears_the_old_bad_one(monkeypatch):
    """Absent != rejected. Re-running on a lead that already had a placeholder
    opener regenerated another placeholder, dropped it, and the never-erase rule
    then preserved the ORIGINAL bad opener — 5 leads survived a full LLM pass
    that way. A deliberately rejected field must be cleared."""
    import backend.services.pitch_research as pr

    lead = SimpleNamespace(name="Acme", pitch_angle="old pitch", pain_points="old pains",
                           opener="Hi [Owner's Name], noticed your rating",
                           review_themes=None)
    for f in ("business_category", "address", "website", "website_cms", "has_ssl",
              "services", "description", "google_rating", "google_review_count",
              "google_is_open", "price_level", "external_platforms",
              "has_direct_commerce", "marketing_stack", "runs_paid_ads",
              "review_weaknesses", "review_lowest_texts", "owner_name", "owner_title",
              "employee_count", "year_founded", "social_facebook", "social_instagram",
              "social_linkedin", "social_twitter"):
        setattr(lead, f, None)

    monkeypatch.setattr(pr, "_resolve_backend",
                        lambda m, u: (dict(provider="ollama", model=m, url=u), 1))
    monkeypatch.setattr(pr, "_call_ollama", lambda *a, **k: json.dumps({
        "pitch_angle": "fresh pitch", "pain_points": ["a", "b"],
        "opener": "Hi [Owner's Name], still a placeholder", "review_themes": []}))
    pr.research_leads_sync([lead], model="m", url="u")

    assert lead.opener is None, "a rejected opener must be cleared, not left stale"
    assert lead.pitch_angle == "fresh pitch"


def test_absent_field_still_keeps_its_old_value(monkeypatch):
    """The truncation guard must survive the change above."""
    import backend.services.pitch_research as pr
    lead = SimpleNamespace(name="Acme", pitch_angle="old pitch", pain_points="old",
                           opener="a perfectly good opener", review_themes=None)
    for f in ("business_category", "address", "website", "website_cms", "has_ssl",
              "services", "description", "google_rating", "google_review_count",
              "google_is_open", "price_level", "external_platforms",
              "has_direct_commerce", "marketing_stack", "runs_paid_ads",
              "review_weaknesses", "review_lowest_texts", "owner_name", "owner_title",
              "employee_count", "year_founded", "social_facebook", "social_instagram",
              "social_linkedin", "social_twitter"):
        setattr(lead, f, None)

    monkeypatch.setattr(pr, "_resolve_backend",
                        lambda m, u: (dict(provider="ollama", model=m, url=u), 1))
    # truncated before opener was emitted — not a rejection
    monkeypatch.setattr(pr, "_call_ollama", lambda *a, **k: '{"pain_points":["x","y"]')
    pr.research_leads_sync([lead], model="m", url="u")

    assert lead.opener == "a perfectly good opener", "absent field must keep its value"
    assert lead.pain_points == "x; y"


def test_placeholder_regex_is_narrow_not_greedy():
    """Regression: a first cut used r"\\[[^\\]]{3,}\\]" and destroyed 62 of 187
    real openers that merely contained a parenthetical bracket."""
    from backend.services.pitch_research import _PLACEHOLDER_RE as R

    for s in ["clients in [LOCATION, assumed from 'Local business'] are missing out",
              "reputation in [Location, assumed from context]",
              "Hello [INSERT NAME], quick question",
              "Reach out at {{email}} soon"]:
        assert R.search(s), f"should be flagged: {s}"

    for s in ["Noticed your Instagram work (great photos) but no SSL.",
              "Saw your 4.8 rating [43 reviews] and wondered about booking.",
              "Your team's restoration work stood out - can we talk?",
              "With 12 years in business, how are you generating reviews?"]:
        assert not R.search(s), f"should NOT be flagged: {s}"


def test_export_selection_gates_and_fallbacks():
    """select_export_leads decides what a client actually receives. Every gate
    must have a never-deliver-nothing fallback."""
    from backend.workers.pipeline import select_export_leads
    from backend.models import LeadStatus

    def L(**kw):
        base = dict(lead_score=0, data_confidence=0.0, phone_verified=False,
                    email_verified=False, has_google_maps=False, sources="",
                    phone=None, owner_email=None, website=None,
                    status=LeadStatus.VALIDATED)
        base.update(kw)
        return SimpleNamespace(**base)

    # Ranked best-first and capped at target.
    good = [L(lead_score=90, data_confidence=0.9, phone_verified=True, phone="1"),
            L(lead_score=70, data_confidence=0.8, phone_verified=True, phone="2"),
            L(lead_score=50, data_confidence=0.7, phone_verified=True, phone="3")]
    picked = select_export_leads(good, target=2)
    assert [l.lead_score for l in picked] == [90, 70]

    # Nothing verified -> falls back to VALIDATED rather than delivering nothing.
    weak = [L(lead_score=1, phone="x"), L(lead_score=2, phone="y")]
    assert len(select_export_leads(weak, target=10)) == 2

    # Uncontactable leads are dropped when contactable ones exist...
    mixed = [L(lead_score=80, data_confidence=0.9, phone_verified=True, phone="p"),
             L(lead_score=95, data_confidence=0.9, phone_verified=True)]  # no contact path
    assert [l.lead_score for l in select_export_leads(mixed, target=10)] == [80]

    # ...but never to the point of an empty file.
    none_contactable = [L(lead_score=95, data_confidence=0.9, phone_verified=True)]
    assert len(select_export_leads(none_contactable, target=10)) == 1

    assert select_export_leads([], target=5) == []


def test_defects_found_in_the_first_live_run():
    """Three things a real Boise roofing run actually delivered. Each shipped into
    a client-facing export before being caught."""
    from backend.services.contact_finder import is_plausible_person_name, _is_junk_email
    from backend.services.pitch_research import _PLACEHOLDER_RE

    # 1. OWNER_PATTERNS matched a capitalised word run mid-sentence; neither word
    #    is site chrome, so the original gate let it through as a person.
    assert not is_plausible_person_name("Form Whether")

    # 2. The email regex ran over minified JS and produced a "contact" that
    #    scored +10 and pushed the lead to 94.
    assert _is_junk_email("n.d@a.length")

    # 3. A mail-merge slot the model left for a human to fill.
    assert _PLACEHOLDER_RE.search("Hi [Owner's Name], noticed Point Roofing's rating")

    # 4. Second live run: a Google Fonts URL split across the "@" and shipped as
    #    a business contact. A local part is a mailbox, never a hostname fragment.
    assert _is_junk_email("fonts.gst@ic.com")
    assert _is_junk_email("cdn.assets@x.com")

    # 5. Third live run: emails harvested out of URLs keep URL shape.
    from backend.services.contact_finder import _normalize_email, _extract_emails_from_html
    assert _is_junk_email("b@.bing.com"), "leading-dot domain"
    assert _normalize_email("info@www.quillridgeroofworks.com") == "info@quillridgeroofworks.com", \
        "www. is a web host prefix, never part of a mail domain — MX fails on it"
    # and the repair must not leave the original alongside the fixed form
    out = _extract_emails_from_html(
        'mailto:info@www.quillridgeroofworks.com b@.bing.com jeff@tamarackroofworksco.com')
    assert out == ["info@quillridgeroofworks.com", "jeff@tamarackroofworksco.com"], out

    # 6. Eugene run: page text split at an "@" yields a plausible shape with an
    #    impossible suffix. Any 2-letter ccTLD is fine; ".installed" is not a TLD.
    assert _is_junk_email("insul@ion.installed")
    for good in ("a@x.io", "hi@studio.design", "b@shop.store", "x@firm.online",
                 "s@acme.co.uk"):
        assert not _is_junk_email(good), good

    # 7. Eugene run: "[Named Nearby Competitor]" — \bname\b could never match
    #    "Named", so placeholder words are matched as stems.
    assert _PLACEHOLDER_RE.search("against [Named Nearby Competitor] who dominates")
    assert not _PLACEHOLDER_RE.search("Saw your 4.8 rating [43 reviews] and wondered")


def test_live_run_fixes_do_not_reject_real_data():
    """Guards the fixes above against over-correction — every value here is real."""
    from backend.services.contact_finder import is_plausible_person_name, _is_junk_email
    from backend.services.pitch_research import _PLACEHOLDER_RE

    for n in ("Michael Ortega", "Maria Gonzalez", "Aharon Ben Elisha", "Mary O'Brien"):
        assert is_plausible_person_name(n), n
    # single-letter domains are real (x.io); an early single-char-label rule broke them
    for e in ("jeff@tamarackroofworksco.com", "info@larkspurpipeworksco.com", "a@x.io", "sales@acme.co.uk"):
        assert not _is_junk_email(e), e
    for s in ("Saw your 4.8 rating [43 reviews] and wondered about booking.",
              "Noticed your work (great photos) but your site has no SSL."):
        assert not _PLACEHOLDER_RE.search(s), s


def test_person_name_gate_edge_cases():
    from backend.services.contact_finder import is_plausible_person_name as ok
    assert ok("Mary O'Brien")
    assert ok("Anne-Marie Smith")
    assert not ok("")
    assert not ok("Smith")                       # needs 2-4 tokens
    assert not ok("A B")                         # initials only
    assert not ok("John Smith Contact Us Now")   # too long, and chrome
