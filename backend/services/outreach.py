"""services/outreach.py — turn scored leads into a call queue, and record what happened.

The pipeline produces leads and pitch copy but nothing ever recorded an ATTEMPT,
so there was no way to answer "who have we called, what happened, who is due a
follow-up". That gap is what separates a lead list from client acquisition.

Phone-first by evidence, not preference: of 237 real leads, 195 (82%) had a
verified phone and only 17 (7%) a verified email. 142 had a verified phone and
no email at all. The LLM writes cold-EMAIL openers, which target the channel we
can barely reach — so the call script here is built from facts we already
verified rather than from that copy.

# ponytail: the script is composed deterministically from stored fields, NOT a
# second LLM call. Every line traces to a verified number, so it cannot
# hallucinate — which is exactly how "Hi Peter" reached a business with no owner
# on record. Add a model here only if the deterministic script measurably
# underperforms on live calls.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.models import (CLOSED_DISPOSITIONS, Disposition, Lead, Outreach,
                            OutreachChannel)

logger = logging.getLogger(__name__)

# How long before a lead that didn't answer comes back around.
RETRY_DAYS = {
    Disposition.NO_ANSWER: 2,
    Disposition.GATEKEEPER: 3,
    Disposition.SPOKE_OWNER: 7,
    Disposition.INTERESTED: 2,
    Disposition.NOT_INTERESTED: 90,
    Disposition.QUEUED: 0,
}
DEFAULT_RETRY_DAYS = 7


# ── identity (mirrors the deduper so history survives a re-scrape) ────────────

def keys(lead) -> tuple[Optional[str], Optional[str]]:
    """(phone7, name_norm) — how a business is recognised across jobs."""
    from backend.services.sources import _digits, _normalize_name
    d = _digits(getattr(lead, "phone", None))
    phone7 = d[-7:] if len(d) >= 7 else None
    name_norm = _normalize_name(getattr(lead, "name", "") or "") or None
    return phone7, name_norm


# ── safety gate ───────────────────────────────────────────────────────────────

# Case variants written out rather than re.IGNORECASE: the NAME group must stay
# case-sensitive (a capitalised first name is the signal), and IGNORECASE would
# apply to it too. Written lowercase-only first, this silently matched nothing —
# every real opener starts "Hi ", so the gate passed everything.
_GREETING_RE = re.compile(r"^\s*(?:[Hh]i|[Hh]ello|[Hh]ey|[Gg]ood\s+\w+)[\s,]+([A-Z][a-z]+)\b")


def greeting_name_is_real(lead, text: Optional[str]) -> bool:
    """Does a "Hi <Name>," greeting name someone (or something) we actually know?

    Three live openers greeted "Peter", "Rick" and "Alex" at businesses with no
    owner_name on record — the model invented them. Addressing a prospect by the
    wrong name is not cosmetic in outreach; it reads as spam and burns the
    contact.

    Crucially the greeted word must be checked against the BUSINESS name too.
    "Hi Ortega Construction Team," is correct and common; an owner_name-only
    check flagged 54 such openers as fake and mangled them. Only a name present
    in neither place is invented.
    """
    if not text:
        return True
    m = _GREETING_RE.match(text)
    if not m:
        return True                      # no personal greeting to verify
    first = m.group(1).lower()
    known = " ".join(str(getattr(lead, f, "") or "")
                     for f in ("owner_name", "name")).lower()
    return first in known


def scrub_unverified_greeting(lead, text: Optional[str]) -> Optional[str]:
    """Strip an invented greeting, keeping the rest of the line."""
    if text is None or greeting_name_is_real(lead, text):
        return text
    stripped = _GREETING_RE.sub("", text).lstrip(" ,-—")
    return (stripped[:1].upper() + stripped[1:]) if stripped else None


# ── the call script ───────────────────────────────────────────────────────────

def hook_facts(lead) -> list[str]:
    """Concrete, verified observations to open with — strongest first."""
    out: list[str] = []
    rating, reviews = lead.google_rating, (lead.google_review_count or 0)
    if not lead.website:
        out.append("they have no website at all")
    elif getattr(lead.website_status, "value", lead.website_status) == "dead":
        out.append("their website is down")
    elif not lead.has_ssl:
        out.append("their site has no SSL padlock — browsers flag it")
    if rating and reviews:
        if rating >= 4.5 and reviews < 25:
            out.append(f"a {rating} rating but only {reviews} reviews — great service, invisible")
        elif rating < 4.0:
            out.append(f"a {rating} rating from {reviews} reviews")
        else:
            out.append(f"{reviews} reviews at {rating}")
    if lead.external_platforms:
        out.append(f"they rely on {lead.external_platforms} and pay commission")
    if not lead.marketing_stack:
        out.append("no tracking on their site, so they can't see where leads come from")
    return out[:3]


def call_script(lead) -> dict:
    """A phone-shaped script for one lead. Every claim traces to a stored fact."""
    biz = lead.name or "the business"
    facts = hook_facts(lead)
    owner = lead.owner_name if greeting_name_is_real(lead, f"Hi {lead.owner_name}") else None

    ask = (f"Could I grab {owner} for two minutes?" if owner
           else "Is the owner around, or whoever looks after your marketing?")
    hook = (f"I had a look at {biz} before calling — " + facts[0] + "."
            if facts else f"I had a look at {biz} before calling.")

    return {
        "business": biz,
        "phone": lead.phone_formatted or lead.phone,
        "gatekeeper": f"Hi, this is <you> calling for {biz}. {ask}",
        "opener": hook,
        "reason": (lead.pitch_angle or "").strip()[:220] or None,
        "facts": facts,
        "pain_points": [p.strip() for p in (lead.pain_points or "").split(";") if p.strip()][:3],
        "close": ("Worth a 15-minute look next week — would Tuesday or Thursday "
                  "suit you better?"),
        "objection": ("\"Send me an email\" -> \"Happy to. What's the best address? "
                      "And so I send something useful rather than a brochure — "
                      "what's your biggest source of new work right now?\""),
        "email_followup": lead.owner_email or None,
    }


# ── queue ─────────────────────────────────────────────────────────────────────

def last_attempts(db) -> dict:
    """(phone7, name_norm) -> most recent Outreach row."""
    latest: dict = {}
    for o in db.query(Outreach).order_by(Outreach.attempted_at).all():
        latest[(o.phone7, o.name_norm)] = o          # later rows overwrite
    return latest


def next_calls(db, limit: int = 50, include_due: bool = True) -> list:
    """Leads worth calling now, best-first.

    Excludes anything closed out (booked / do-not-contact / bad number) and
    anything whose follow-up date has not arrived. Ranked by lead_score then
    confidence — the same ordering the export gate uses.
    """
    now = datetime.now(timezone.utc)
    seen = last_attempts(db)

    rows = (db.query(Lead)
              .filter(Lead.phone_verified.is_(True))
              .order_by(Lead.lead_score.desc())
              .all())

    out, used = [], set()
    for lead in rows:
        k = keys(lead)
        if k in used:                      # same business scraped by two jobs
            continue
        prior = seen.get(k)
        if prior is not None:
            if prior.disposition in CLOSED_DISPOSITIONS:
                continue
            nxt = prior.next_action_at
            if nxt is not None:
                if nxt.tzinfo is None:
                    nxt = nxt.replace(tzinfo=timezone.utc)
                if nxt > now and not include_due:
                    continue
                if nxt > now:
                    continue               # not due yet
        used.add(k)
        out.append(lead)
        if len(out) >= limit:
            break
    return out


def record(db, lead, disposition: Disposition, notes: str = "",
           channel: OutreachChannel = OutreachChannel.PHONE,
           retry_days: Optional[int] = None) -> Outreach:
    """Log one attempt and schedule the follow-up."""
    phone7, name_norm = keys(lead)
    days = retry_days if retry_days is not None else RETRY_DAYS.get(
        disposition, DEFAULT_RETRY_DAYS)
    nxt = (datetime.now(timezone.utc) + timedelta(days=days)
           if disposition not in CLOSED_DISPOSITIONS and days else None)

    row = Outreach(phone7=phone7, name_norm=name_norm,
                   business_name=getattr(lead, "name", None),
                   lead_id=getattr(lead, "id", None), channel=channel,
                   disposition=disposition, notes=notes or None,
                   attempted_at=datetime.now(timezone.utc), next_action_at=nxt)
    db.add(row)
    db.commit()
    logger.info("outreach: %s -> %s (next %s)", row.business_name,
                disposition.value, nxt.date() if nxt else "never")
    return row


def stats(db) -> dict:
    """Funnel counts, for `--stats` and for knowing whether this is working."""
    from collections import Counter
    latest = last_attempts(db)
    by = Counter(o.disposition.value for o in latest.values())
    attempts = db.query(Outreach).count()
    contacted = len(latest)
    return {
        "attempts": attempts,
        "businesses_contacted": contacted,
        "by_disposition": dict(by),
        "booked": by.get(Disposition.BOOKED.value, 0),
        "interested": by.get(Disposition.INTERESTED.value, 0),
        "conversion_pct": round(100 * by.get(Disposition.BOOKED.value, 0) / contacted, 1)
                          if contacted else 0.0,
    }
