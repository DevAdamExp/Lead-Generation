"""Outreach: the greeting safety gate, the call queue, and disposition state.

Phone-first because that is what the data supports — of 237 real leads, 195 had
a verified phone and 17 a verified email.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.models import Disposition, Outreach, OutreachChannel


def L(**kw):
    base = dict(id="l1", name="Acme Roofing", owner_name=None, phone="(555) 010-2030",
                phone_formatted="(555) 010-2030", phone_verified=True, address=None,
                lead_score=80, data_confidence=0.9, website=None, website_status=None,
                has_ssl=False, google_rating=None, google_review_count=0,
                external_platforms=None, marketing_stack=None, owner_email=None,
                pitch_angle=None, pain_points=None, opener=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── the greeting gate ─────────────────────────────────────────────────────────

def test_invented_person_is_rejected():
    """Three live openers greeted "Peter", "Rick" and "Alex" at businesses with
    no owner on record. Addressing a prospect by an invented name reads as spam
    and burns the contact permanently."""
    from backend.services.outreach import greeting_name_is_real
    lead = L(name="Sellen Construction", owner_name=None)
    assert not greeting_name_is_real(lead, "Hi Peter, I noticed Sellen's team")


def test_greeting_the_business_by_name_is_fine():
    """The over-fire this guards against: checking only owner_name flagged 54
    perfectly good openers as fake and mangled them into "Construction Team,
    noticed..." — greeting a COMPANY by its name is correct and common."""
    from backend.services.outreach import greeting_name_is_real
    for name, opener in [
        ("Ortega Construction Company", "Hi Ortega Construction Team, noticed your work"),
        ("Moser Roofing", "Hi Moser Roofing, noticed your strong local reputation"),
        ("Red Rock Construction", "Hi Red Rock Construction, I noticed your Newark site"),
    ]:
        assert greeting_name_is_real(L(name=name), opener), name


def test_real_owner_name_is_accepted():
    from backend.services.outreach import greeting_name_is_real
    lead = L(name="Coastal Construction LLC", owner_name="Mark Wulff")
    assert greeting_name_is_real(lead, "Hi Mark, I noticed your site has no SSL")


def test_gate_matches_every_greeting_form():
    """Written lowercase-only at first, the regex matched nothing at all — every
    real opener starts with a capital "Hi", so the gate silently passed
    everything it was built to catch."""
    from backend.services.outreach import greeting_name_is_real
    lead = L(name="Acme Roofing", owner_name=None)
    for opener in ("Hi Peter, hello", "Hello Rick — noticed", "Hey Alex, saw",
                   "Good morning Sarah, I noticed"):
        assert not greeting_name_is_real(lead, opener), opener


def test_gate_ignores_openers_with_no_greeting():
    from backend.services.outreach import greeting_name_is_real
    lead = L(owner_name=None)
    assert greeting_name_is_real(lead, "Noticed your 4.8 rating with only 12 reviews")
    assert greeting_name_is_real(lead, None)


def test_scrub_keeps_the_sentence():
    from backend.services.outreach import scrub_unverified_greeting
    lead = L(name="Sellen Construction", owner_name=None)
    out = scrub_unverified_greeting(lead, "Hi Peter, I noticed your 284-person team")
    assert out and "Peter" not in out and "284-person team" in out
    # a legitimate greeting is returned untouched
    ok = "Hi Moser Roofing, noticed your reputation"
    assert scrub_unverified_greeting(L(name="Moser Roofing"), ok) == ok


# ── call script ───────────────────────────────────────────────────────────────

def test_script_asks_for_the_owner_by_name_only_when_known():
    from backend.services.outreach import call_script
    named = call_script(L(owner_name="Mark Wulff"))
    assert "Mark Wulff" in named["gatekeeper"]
    anon = call_script(L(owner_name=None))
    assert "owner" in anon["gatekeeper"].lower() and "None" not in anon["gatekeeper"]


def test_script_hooks_are_facts_we_actually_hold():
    from backend.services.outreach import call_script, hook_facts
    no_site = hook_facts(L(website=None))
    assert any("no website" in f for f in no_site)
    hidden_gem = hook_facts(L(website="x", has_ssl=True, google_rating=4.9,
                              google_review_count=11))
    assert any("11" in f and "4.9" in f for f in hidden_gem)
    # nothing known -> no invented hooks
    assert hook_facts(L(website="x", has_ssl=True, marketing_stack="GA")) == []


# ── queue + dispositions ──────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.database import Base
    engine = create_engine(f"sqlite:///{tmp_path/'o.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_outreach_has_no_fk_so_history_outlives_the_lead():
    """Same lesson as delivered_businesses: leads cascade away with their job, and
    "they told us never to call again" must not vanish with them."""
    assert Outreach.__table__.foreign_keys == set()


def test_record_schedules_a_follow_up(db):
    from backend.services.outreach import record
    row = record(db, L(), Disposition.NO_ANSWER, notes="rang out")
    assert row.disposition == Disposition.NO_ANSWER
    assert row.next_action_at is not None, "a no-answer must come back around"
    assert row.channel == OutreachChannel.PHONE
    assert row.phone7 == "0102030"


def test_closed_dispositions_never_return(db):
    from backend.services.outreach import record
    for d in (Disposition.BOOKED, Disposition.DO_NOT_CONTACT, Disposition.BAD_NUMBER):
        row = record(db, L(phone=f"555010{d.value[:4]}"), d)
        assert row.next_action_at is None, f"{d.value} must not be rescheduled"


def test_queue_excludes_do_not_contact_and_unripe_followups(db):
    from backend.models import Lead
    from backend.services.outreach import next_calls, record

    keep = Lead(id="a", job_id="j", name="Keep Me", phone="(555) 111-2222",
                phone_verified=True, lead_score=90)
    dnc = Lead(id="b", job_id="j", name="Never Again", phone="(555) 333-4444",
               phone_verified=True, lead_score=95)
    soon = Lead(id="c", job_id="j", name="Call Later", phone="(555) 555-6666",
                phone_verified=True, lead_score=99)
    db.add_all([keep, dnc, soon])
    db.commit()

    record(db, dnc, Disposition.DO_NOT_CONTACT)
    record(db, soon, Disposition.NO_ANSWER)          # schedules a future retry

    names = [l.name for l in next_calls(db, limit=10)]
    assert "Keep Me" in names
    assert "Never Again" not in names, "a hard no must never resurface"
    assert "Call Later" not in names, "a scheduled follow-up is not due yet"


def test_queue_requires_a_verified_phone(db):
    from backend.models import Lead
    from backend.services.outreach import next_calls
    db.add(Lead(id="x", job_id="j", name="No Phone", phone=None,
                phone_verified=False, lead_score=100))
    db.commit()
    assert next_calls(db, limit=10) == []


def test_stats_report_the_funnel(db):
    from backend.models import Lead
    from backend.services.outreach import record, stats
    a = Lead(id="a", job_id="j", name="A", phone="(555) 111-0001", phone_verified=True)
    b = Lead(id="b", job_id="j", name="B", phone="(555) 111-0002", phone_verified=True)
    db.add_all([a, b])
    db.commit()
    record(db, a, Disposition.BOOKED)
    record(db, b, Disposition.NO_ANSWER)

    st = stats(db)
    assert st["attempts"] == 2 and st["businesses_contacted"] == 2
    assert st["booked"] == 1 and st["conversion_pct"] == 50.0


def test_latest_disposition_wins(db):
    """A business called twice counts once, by its most recent outcome."""
    from backend.models import Lead
    from backend.services.outreach import record, stats
    lead = Lead(id="a", job_id="j", name="A", phone="(555) 111-0001", phone_verified=True)
    db.add(lead)
    db.commit()
    record(db, lead, Disposition.NO_ANSWER)
    record(db, lead, Disposition.BOOKED)
    st = stats(db)
    assert st["attempts"] == 2
    assert st["businesses_contacted"] == 1
    assert st["booked"] == 1
