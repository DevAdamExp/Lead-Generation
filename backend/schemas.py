"""
schemas.py — Pydantic v2 request/response models.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ── Job ──────────────────────────────────────────────────────────────────────
class JobCreate(BaseModel):
    niche: str = Field(..., min_length=1, examples=["plumbers"])
    location: str = Field(..., min_length=1, examples=["Austin, TX"])
    country: str = Field(default="us", min_length=2, max_length=2)
    limit: int = Field(default=100, ge=5, le=500)


class JobResponse(BaseModel):
    id: str
    niche: str
    location: str
    country: str
    limit: int
    status: str
    progress_pct: int
    current_stage: str
    stage_message: str
    total_scraped: int
    total_verified: int
    error_message: Optional[str]
    export_xlsx_path: Optional[str]
    export_pdf_path: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ── Lead ─────────────────────────────────────────────────────────────────────
class LeadResponse(BaseModel):
    id: str
    job_id: str
    name: Optional[str]
    address: Optional[str]
    phone: Optional[str]
    phone_formatted: Optional[str]
    phone_verified: bool = False
    description: Optional[str]
    business_category: Optional[str] = None
    hours: Optional[str] = None
    price_level: Optional[str] = None
    plus_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    sources: Optional[str] = None
    source_url: Optional[str] = None
    hotfrog_url: Optional[str]
    has_google_maps: bool
    google_rating: Optional[float]
    google_review_count: int
    google_maps_url: Optional[str]
    website: Optional[str]
    website_status: str
    website_cms: Optional[str]
    has_ssl: bool
    website_name_found: bool = False
    owner_name: Optional[str]
    owner_email: Optional[str]
    email_verified: bool = False
    verification_tier: str = "unverified"
    address_valid: Optional[bool] = None
    owner_phone: Optional[str] = None
    owner_phone_type: Optional[str] = None
    owner_phone_confidence: Optional[float] = 0.0
    social_facebook: Optional[str]
    social_instagram: Optional[str]
    social_twitter: Optional[str]
    social_linkedin: Optional[str]
    description_long: Optional[str] = None
    employee_count: Optional[int] = None
    employee_count_source: Optional[str] = None
    year_founded: Optional[int] = None
    partners: Optional[str] = None
    recent_activity: Optional[str] = None
    last_activity_date: Optional[datetime] = None
    services: Optional[str] = None
    team_members: Optional[str] = None
    owner_title: Optional[str] = None
    review_weaknesses: Optional[str] = None
    review_weakness_count: int = 0
    # Real review depth (review_scraper)
    review_themes: Optional[str] = None
    last_review_date: Optional[str] = None
    rating_histogram: Optional[str] = None
    owner_responds: Optional[bool] = None
    # Commerce / marketing signals (business_intel)
    external_platforms: Optional[str] = None
    has_direct_commerce: Optional[bool] = None
    marketing_stack: Optional[str] = None
    runs_paid_ads: Optional[bool] = None
    # Legal entity (registration)
    legal_name: Optional[str] = None
    entity_type: Optional[str] = None
    entity_status: Optional[str] = None
    registration_date: Optional[str] = None
    registered_agent: Optional[str] = None
    registry_source: Optional[str] = None
    # LLM pitch research — the whole point of the enrichment, and it was invisible
    # to the API until now (LeadResponse exposed 54 of 77 Lead columns).
    pitch_angle: Optional[str] = None
    pain_points: Optional[str] = None
    opener: Optional[str] = None
    # Provenance / scoring internals
    google_is_open: Optional[bool] = None
    phone_source_count: int = 0
    source_query: Optional[str] = None
    review_lowest_texts: Optional[str] = None   # JSON: worst-first review snippets
    # Deprecated: nothing writes this — data_confidence superseded it. Kept only
    # because frontend/src/types/index.ts still declares it as required.
    fuzzy_confidence: Optional[float] = 0.0
    lead_score: int
    data_confidence: Optional[float] = 0.0
    category: str
    status: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @classmethod
    def _normalize_enum(cls, v):
        """Convert SQLAlchemy enum instances to their string value."""
        if hasattr(v, 'value'):
            return v.value
        return v if v is not None else ''

    def model_post_init(self, __context):
        # Ensure enum fields are plain strings, not enum instances
        for field in ('website_status', 'category', 'status'):
            val = getattr(self, field, None)
            if hasattr(val, 'value'):
                object.__setattr__(self, field, val.value)
            elif val is None:
                object.__setattr__(self, field, '')


class LeadsPage(BaseModel):
    total: int
    page: int
    per_page: int
    items: List[LeadResponse]
