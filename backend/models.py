"""
models.py — SQLAlchemy ORM models: Job and Lead.
"""
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime,
    ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship
from backend.database import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


# ── Enums ────────────────────────────────────────────────────────────────────
class JobStatus(str, PyEnum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


class LeadStatus(str, PyEnum):
    SCRAPED           = "scraped"
    MAPS_CHECKED      = "maps_checked"
    WEBSITE_ANALYZED  = "website_analyzed"
    ENRICHED          = "enriched"
    VALIDATED         = "validated"


class OutreachChannel(str, PyEnum):
    PHONE = "phone"
    EMAIL = "email"
    OTHER = "other"


class Disposition(str, PyEnum):
    """Outcome of one contact attempt. Ordered roughly by progress."""
    QUEUED         = "queued"           # selected for calling, not yet attempted
    NO_ANSWER      = "no_answer"        # rang out / voicemail
    GATEKEEPER     = "gatekeeper"       # reached staff, not the decision maker
    SPOKE_OWNER    = "spoke_owner"      # reached the decision maker
    INTERESTED     = "interested"       # wants to hear more
    BOOKED         = "booked"           # meeting scheduled — the goal
    NOT_INTERESTED = "not_interested"   # soft no, may re-approach later
    BAD_NUMBER     = "bad_number"       # number is wrong/disconnected
    DO_NOT_CONTACT = "do_not_contact"   # hard no — never call again


# Dispositions that permanently remove a business from the calling queue.
CLOSED_DISPOSITIONS = {Disposition.BOOKED, Disposition.DO_NOT_CONTACT,
                       Disposition.BAD_NUMBER}


class WebsiteStatus(str, PyEnum):
    ACTIVE = "active"
    DEAD   = "dead"
    NONE   = "none"


class LeadCategory(str, PyEnum):
    NO_WEBSITE  = "no_website"
    HAS_WEBSITE = "has_website"
    UNKNOWN     = "unknown"


# ── Job ──────────────────────────────────────────────────────────────────────
class Job(Base):
    __tablename__ = "jobs"

    id             = Column(String, primary_key=True, default=_uuid)
    niche          = Column(String, nullable=False)
    location       = Column(String, nullable=False)
    country        = Column(String, default="us")
    limit          = Column(Integer, default=100)
    status         = Column(Enum(JobStatus), default=JobStatus.PENDING)
    progress_pct   = Column(Integer, default=0)
    current_stage  = Column(String, default="")
    stage_message  = Column(String, default="")
    total_scraped  = Column(Integer, default=0)
    total_verified = Column(Integer, default=0)
    error_message  = Column(Text, nullable=True)
    export_xlsx_path = Column(String, nullable=True)
    export_pdf_path  = Column(String, nullable=True)
    created_at     = Column(DateTime(timezone=True), default=_now)
    completed_at   = Column(DateTime(timezone=True), nullable=True)
    # Liveness marker touched as the pipeline advances. A RUNNING job whose
    # heartbeat has gone cold is a job whose worker died — acks_late redelivers
    # the task, and reconcile_stale_jobs() fails anything unrecoverable instead of
    # leaving it spinning forever (six such rows were found 16 days old).
    heartbeat_at   = Column(DateTime(timezone=True), nullable=True)

    leads = relationship("Lead", back_populates="job", cascade="all, delete-orphan")


# ── Delivered businesses ─────────────────────────────────────────────────────
class DeliveredBusiness(Base):
    """Permanent record of a business already handed to the user.

    Cross-run dedup used to be derived from `leads`, which cascades away with its
    Job — so deleting a job silently made every business it delivered sellable
    again. This table has NO foreign key to jobs and is never cascaded, so the
    "already delivered" fact outlives the job that produced it.
    """
    __tablename__ = "delivered_businesses"

    id           = Column(String, primary_key=True, default=_uuid)
    phone7       = Column(String, index=True, nullable=True)   # last 7 phone digits
    name_norm    = Column(String, index=True, nullable=True)   # sources._normalize_name
    business_name = Column(String, nullable=True)              # human-readable, for audit
    job_id       = Column(String, nullable=True)               # deliberately NOT a FK
    niche        = Column(String, nullable=True)
    location     = Column(String, nullable=True)
    delivered_at = Column(DateTime(timezone=True), default=_now)


# ── Outreach ─────────────────────────────────────────────────────────────────
class Outreach(Base):
    """One contact attempt against one business.

    Same shape as DeliveredBusiness and for the same reason: NO foreign key to
    leads. Leads cascade away with their job, and outreach history must not —
    "we called them and they said never again" has to survive a job deletion or
    a re-scrape, otherwise the next campaign calls them anyway. Matching is by
    the same keys the deduper uses (last 7 phone digits, normalised name).
    """
    __tablename__ = "outreach"

    id           = Column(String, primary_key=True, default=_uuid)
    phone7       = Column(String, index=True, nullable=True)
    name_norm    = Column(String, index=True, nullable=True)
    business_name = Column(String, nullable=True)
    lead_id      = Column(String, nullable=True)      # convenience only, NOT a FK
    channel      = Column(Enum(OutreachChannel), default=OutreachChannel.PHONE)
    disposition  = Column(Enum(Disposition), default=Disposition.QUEUED)
    notes        = Column(Text, nullable=True)
    attempted_at = Column(DateTime(timezone=True), default=_now)
    next_action_at = Column(DateTime(timezone=True), nullable=True)  # when to retry
    created_at   = Column(DateTime(timezone=True), default=_now)


# ── Lead ─────────────────────────────────────────────────────────────────────
class Lead(Base):
    __tablename__ = "leads"

    id              = Column(String, primary_key=True, default=_uuid)
    job_id          = Column(String, ForeignKey("jobs.id"), nullable=False)
    name            = Column(String)
    address         = Column(Text)
    phone           = Column(String)
    phone_formatted = Column(String)
    description     = Column(Text)                       # short description from Maps/directories
    description_long = Column(Text, nullable=True)        # richer description from website about page

    # Business attributes (enriched from Maps / directories)
    business_category = Column(String, nullable=True)   # e.g. "Plumber", "Dentist"
    hours             = Column(Text, nullable=True)
    price_level       = Column(String, nullable=True)
    plus_code         = Column(String, nullable=True)
    latitude          = Column(Float, nullable=True)
    longitude         = Column(Float, nullable=True)

    # Provenance — which sources contributed to this lead
    sources         = Column(String, nullable=True)     # comma-separated list
    source_url      = Column(String, nullable=True)     # canonical listing URL
    phone_source_count = Column(Integer, default=0)     # # independent sources agreeing on the phone

    # Hotfrog source
    hotfrog_url     = Column(String)
    source_query    = Column(String)

    # Google Maps (scraped via Playwright)
    has_google_maps      = Column(Boolean, default=False)
    google_rating        = Column(Float, nullable=True)
    google_review_count  = Column(Integer, default=0)
    google_maps_url      = Column(String, nullable=True)
    google_is_open       = Column(Boolean, nullable=True)

    # Website
    website            = Column(String, nullable=True)
    website_status     = Column(Enum(WebsiteStatus), default=WebsiteStatus.NONE)
    website_cms        = Column(String, nullable=True)
    has_ssl            = Column(Boolean, default=False)
    website_name_found = Column(Boolean, default=False)  # business name found on page

    # Contact
    owner_name      = Column(String, nullable=True)
    owner_email     = Column(String, nullable=True)
    owner_phone     = Column(String, nullable=True)      # best-effort owner/direct line
    owner_phone_type = Column(String, nullable=True)     # mobile | fixed_line | voip | unknown
    owner_phone_confidence = Column(Float, default=0.0)  # 0-1 confidence it's the owner's
    social_facebook = Column(String, nullable=True)
    social_instagram= Column(String, nullable=True)
    social_twitter  = Column(String, nullable=True)
    social_linkedin = Column(String, nullable=True)

    # Business Intelligence (deep enrichment)
    employee_count      = Column(Integer, nullable=True) # estimated team size
    employee_count_source = Column(String, nullable=True) # "website_regex", "team_page", "schema"
    year_founded        = Column(Integer, nullable=True) # founded year
    partners            = Column(Text, nullable=True)    # comma-separated partners/clients/affiliations
    recent_activity     = Column(Text, nullable=True)    # recent news, blog, updates summary
    last_activity_date  = Column(DateTime(timezone=True), nullable=True)
    services            = Column(Text, nullable=True)    # comma-separated services/products extracted
    team_members        = Column(Text, nullable=True)    # JSON: [{"name":"...", "title":"..."}]
    owner_title         = Column(String, nullable=True)  # decision-maker role (CEO, Founder, etc.)
    review_weaknesses   = Column(Text, nullable=True)    # negative signals from reviews
    review_weakness_count = Column(Integer, default=0)   # count of negative signals
    # Real review depth (Google Maps reviews tab — see review_scraper.py)
    review_lowest_texts = Column(Text, nullable=True)    # JSON: [{"stars","date","text"}] worst-first
    review_themes       = Column(Text, nullable=True)    # LLM-extracted complaint themes (comma-joined)
    last_review_date    = Column(String, nullable=True)  # true recency (e.g. "3 months ago")
    rating_histogram    = Column(Text, nullable=True)    # JSON: {"5":n,...,"1":n}
    owner_responds      = Column(Boolean, nullable=True) # owner replies to negative reviews?

    # Legal / registration data (see registration.py)
    legal_name          = Column(String, nullable=True)
    entity_type         = Column(String, nullable=True)  # LLC, Corp, etc.
    entity_status       = Column(String, nullable=True)  # active | dissolved | inactive | ...
    registration_date   = Column(String, nullable=True)
    registered_agent    = Column(String, nullable=True)
    registry_source     = Column(String, nullable=True)  # sam_gov | opencorporates | sos_<state>
    external_platforms  = Column(Text, nullable=True)    # 3rd-party they rely on (DoorDash/Booking…) — commission-bleed wedge
    has_direct_commerce = Column(Boolean, nullable=True) # own ordering/booking engine detected?
    marketing_stack     = Column(Text, nullable=True)    # ad/marketing pixels detected (FB Pixel, Google Ads…)
    runs_paid_ads       = Column(Boolean, nullable=True) # a retargeting/conversion pixel => paid ad spend

    # Pitch research (local-LLM synthesis over scraped fields; see pitch_research.py)
    pitch_angle         = Column(Text, nullable=True)    # one-sentence positioning
    pain_points         = Column(Text, nullable=True)    # joined list of likely problems
    opener              = Column(Text, nullable=True)    # cold-email opening line

    # Verification flags
    email_verified  = Column(Boolean, default=False)     # RCPT-deliverable or MX-valid
    phone_verified  = Column(Boolean, default=False)     # libphonenumber valid
    data_confidence = Column(Float, default=0.0)         # overall 0-1 confidence
    verification_tier = Column(String, default="unverified")  # verified | corroborated | unverified
    address_valid   = Column(Boolean, nullable=True)     # US Census geocoder matched the address

    # Scoring
    lead_score      = Column(Integer, default=0)
    fuzzy_confidence= Column(Float, default=0.0)
    category        = Column(Enum(LeadCategory), default=LeadCategory.UNKNOWN)
    status          = Column(Enum(LeadStatus), default=LeadStatus.SCRAPED)

    created_at      = Column(DateTime(timezone=True), default=_now)

    job = relationship("Job", back_populates="leads")
