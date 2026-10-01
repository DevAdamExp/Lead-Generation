"""
api/leads.py — Paginated leads query endpoint.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.database import get_async_db
from backend.models import Lead, LeadCategory
from backend.schemas import LeadsPage, LeadResponse

router = APIRouter()


@router.get("/jobs/{job_id}/leads", response_model=LeadsPage)
async def get_leads(
    job_id: str,
    category: str | None = Query(None, description="no_website | has_website"),
    min_score: int = Query(0, ge=0, le=100),
    has_email: bool | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
):
    q = select(Lead).where(Lead.job_id == job_id)

    # `category` doubles as the UI tab filter.
    if category and category != "all":
        if category in ("no_website", "has_website"):
            q = q.where(Lead.category == category)
        elif category == "has_gmaps":
            q = q.where(Lead.has_google_maps.is_(True))
        elif category == "owner_found":
            q = q.where(Lead.owner_name.isnot(None) | Lead.owner_phone.isnot(None))
        elif category == "high_value":
            q = q.where(Lead.lead_score >= 70)
    if min_score > 0:
        q = q.where(Lead.lead_score >= min_score)
    if has_email is True:
        q = q.where(Lead.owner_email.isnot(None))
    elif has_email is False:
        q = q.where(Lead.owner_email.is_(None))

    count_q = select(func.count()).select_from(q.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar_one()

    q = q.order_by(Lead.lead_score.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(q)
    items = result.scalars().all()

    return LeadsPage(total=total, page=page, per_page=per_page, items=items)
