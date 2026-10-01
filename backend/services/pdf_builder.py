"""
services/pdf_builder.py — High-fidelity lead report PDF.

Primary renderer: Playwright Chromium print-to-PDF (already a project dependency,
no fragile system libs). Produces crisp, "generated"-quality output with proper
backgrounds, web fonts, and page headers/footers.

Fallback chain: Chromium → WeasyPrint → None (never crashes the pipeline).
"""
import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional

from backend.models import Lead, LeadCategory
from backend.utils.naming import export_filebase

logger = logging.getLogger(__name__)


# ── HTML building ────────────────────────────────────────────────────────────

def _esc(v) -> str:
    if v is None:
        return ""
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _score_badge(score: int) -> str:
    score = score or 0
    if score >= 70:
        cls, label = "badge-high", "HIGH"
    elif score >= 40:
        cls, label = "badge-med", "MED"
    else:
        cls, label = "badge-low", "LOW"
    return f'<span class="badge {cls}">{label} · {score}</span>'


def _conf_bar(conf: float) -> str:
    pct = int(round((conf or 0) * 100))
    return (f'<div class="confbar"><div class="conffill" style="width:{pct}%"></div></div>'
            f'<span class="conftxt">{pct}%</span>')


def _verified_pill(ok: bool) -> str:
    return '<span class="pill ok">✓</span>' if ok else '<span class="pill no">—</span>'


def _pitch_badge(lead: Lead) -> str:
    has_contact = bool(lead.owner_email or lead.owner_phone or lead.phone)
    has_owner = bool(lead.owner_name)
    site_confirmed = bool(getattr(lead, 'website_name_found', False)) or bool(lead.has_google_maps)
    if has_contact and has_owner and site_confirmed and lead.lead_score and lead.lead_score >= 40:
        return '<span class="pitch pitch-ready">Ready</span>'
    elif has_contact and site_confirmed:
        return '<span class="pitch pitch-needs">No Owner</span>'
    else:
        return '<span class="pitch pitch-nope">No Contact</span>'


def _lead_row(lead: Lead) -> str:
    # Contact block
    contact = []
    if lead.phone_formatted or lead.phone:
        contact.append(f'<div class="line"><b>☎</b> {_esc(lead.phone_formatted or lead.phone)} '
                        f'{_verified_pill(lead.phone_verified)}</div>')
    if lead.owner_name:
        contact.append(f'<div class="line owner"><b>Owner:</b> {_esc(lead.owner_name)}</div>')
    if lead.owner_phone:
        ptype = f" ({_esc(lead.owner_phone_type)})" if lead.owner_phone_type else ""
        conf = int(round((lead.owner_phone_confidence or 0) * 100))
        contact.append(f'<div class="line ownerph"><b>📱 Owner #:</b> {_esc(lead.owner_phone)}'
                       f'{ptype} <span class="confchip">{conf}%</span></div>')
    if lead.owner_email:
        contact.append(f'<div class="line"><b>✉</b> <a href="mailto:{_esc(lead.owner_email)}">'
                       f'{_esc(lead.owner_email)}</a> {_verified_pill(lead.email_verified)}</div>')
    contact_html = "".join(contact) or '<span class="muted">—</span>'

    # Web / Maps block
    web = []
    if lead.website:
        web.append(f'<div class="line"><a href="{_esc(lead.website)}">'
                   f'{_esc(lead.website.replace("https://","").replace("http://","")[:34])}</a></div>')
        if lead.website_cms:
            web.append(f'<div class="line muted">CMS: {_esc(lead.website_cms)}</div>')
    else:
        web.append('<span class="muted">No website</span>')
    if lead.has_google_maps:
        r = f'⭐ {lead.google_rating}' if lead.google_rating else '⭐ —'
        web.append(f'<div class="line">{r} <span class="muted">({lead.google_review_count or 0})</span></div>')
    web_html = "".join(web)

    # Category + sources line under name
    meta = []
    if lead.business_category:
        meta.append(f'<span class="cat">{_esc(lead.business_category)}</span>')
    if lead.sources:
        for s in lead.sources.split(","):
            meta.append(f'<span class="src src-{_esc(s)}">{_esc(s)}</span>')
    meta_html = " ".join(meta)

    # Intel block
    intel = []
    if lead.employee_count:
        intel.append(f'<div class="line">👥 {lead.employee_count}<span class="muted"> emp.</span></div>')
    if lead.year_founded:
        intel.append(f'<div class="line">📅 Founded {lead.year_founded}</div>')
    if lead.partners:
        partners_str = lead.partners if isinstance(lead.partners, str) else ", ".join(lead.partners)
        intel.append(f'<div class="line">🤝 {_esc(partners_str[:60])}</div>')
    if lead.recent_activity:
        act = lead.recent_activity[:80]
        intel.append(f'<div class="line muted">{_esc(act)}</div>')
    if lead.services:
        intel.append(f'<div class="line">📋 {_esc(lead.services[:80])}</div>')
    if lead.team_members:
        intel.append(f'<div class="line">👤 {_esc(lead.team_members[:80])}</div>')
    if lead.owner_title:
        intel.append(f'<div class="line">🏷️ {_esc(lead.owner_title)}</div>')
    if lead.review_weakness_count:
        intel.append(f'<div class="line">⚠️ {lead.review_weakness_count} weakness(es)</div>')
    if getattr(lead, "pitch_angle", None):
        intel.append(f'<div class="line">🎯 {_esc(lead.pitch_angle[:120])}</div>')
    if getattr(lead, "pain_points", None):
        intel.append(f'<div class="line muted">Pains: {_esc(str(lead.pain_points)[:120])}</div>')
    if getattr(lead, "opener", None):
        intel.append(f'<div class="line muted">✉ "{_esc(lead.opener[:120])}"</div>')
    intel_html = "".join(intel) or '<span class="muted">—</span>'

    return f"""
    <tr>
      <td class="c-name">
        <div class="bname">{_esc(lead.name or 'Unknown')}</div>
        <div class="row-badges">{_score_badge(lead.lead_score)} {_conf_bar(lead.data_confidence)} {_pitch_badge(lead)}</div>
        <div class="meta">{meta_html}</div>
      </td>
      <td class="c-contact">{contact_html}</td>
      <td class="c-web">{web_html}</td>
      <td class="c-intel">{intel_html}</td>
      <td class="c-addr">{_esc(lead.address) or '—'}</td>
    </tr>"""


def _section(title: str, cls: str, leads: List[Lead]) -> str:
    if leads:
        rows = "".join(_lead_row(l) for l in leads)
    else:
        rows = '<tr><td colspan="5" class="empty">No leads in this category.</td></tr>'
    return f"""
    <div class="section-title {cls}">{title} <span class="count">{len(leads)}</span></div>
    <table class="report">
      <thead><tr>
        <th style="width:26%">Business</th>
        <th style="width:24%">Contact</th>
        <th style="width:15%">Web / Maps</th>
        <th style="width:18%">Business Intel</th>
        <th style="width:17%">Address</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def _html_report(leads: List[Lead], job) -> str:
    no_web  = sorted([l for l in leads if l.category == LeadCategory.NO_WEBSITE],
                     key=lambda l: (l.lead_score or 0), reverse=True)
    has_web = sorted([l for l in leads if l.category == LeadCategory.HAS_WEBSITE],
                     key=lambda l: (l.lead_score or 0), reverse=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    kpis = [
        (len(leads), "Total Leads"),
        (len(no_web), "No Website"),
        (sum(1 for l in leads if l.owner_phone), "Owner Phone"),
        (sum(1 for l in leads if l.email_verified), "Verified Email"),
        (sum(1 for l in leads if l.has_google_maps), "On Maps"),
        (sum(1 for l in leads if l.website_name_found), "Name On Site"),
        (sum(1 for l in leads if l.employee_count), "Emp. Count"),
        (sum(1 for l in leads if l.services), "Services"),
        (sum(1 for l in leads if l.team_members), "Team Roster"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-v">{v}</div><div class="kpi-l">{l}</div></div>'
        for v, l in kpis
    )

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Lead Report — {_esc(job.niche)} — {_esc(job.location)}</title>
<style>
  /* ponytail: solid print-safe palette only. accent #1f2937, neutrals #e2e8f0/#f1f5f9 */
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #1f2937; font-size: 9.5px; line-height: 1.45; }}
  .cover {{ background:#1f2937; color:#fff; padding:24px 26px; border-radius:6px; margin-bottom:18px; }}
  .cover h1 {{ font-size:22px; font-weight:700; letter-spacing:-.3px; }}
  .cover .sub {{ font-size:11px; color:#cbd5e1; margin-top:6px; font-weight:400; }}
  .kpis {{ display:flex; gap:10px; margin-top:18px; }}
  .kpi {{ flex:1; background:#374151; border:1px solid #4b5563; border-radius:4px; padding:11px 10px; }}
  .kpi-v {{ font-size:22px; font-weight:700; color:#fff; }}
  .kpi-l {{ font-size:8px; text-transform:uppercase; letter-spacing:.6px; color:#cbd5e1; margin-top:3px; }}
  .section-title {{ font-size:13px; font-weight:700; margin:16px 0 8px; padding-bottom:5px;
                    color:#1f2937; border-bottom:2px solid #1f2937; text-transform:uppercase; letter-spacing:.5px; }}
  .section-title .count {{ float:right; background:#1f2937; color:#fff; border-radius:3px;
                           font-size:9px; font-weight:700; padding:1px 9px; }}
  .section-title.web {{ page-break-before: always; }}
  table.report {{ width:100%; border-collapse:collapse; margin-bottom:14px; }}
  table.report th {{ background:#1f2937; color:#fff; text-align:left; padding:6px 9px;
                     font-size:8.5px; text-transform:uppercase; letter-spacing:.5px; }}
  table.report td {{ padding:7px 9px; border:1px solid #e2e8f0; vertical-align:top; }}
  table.report tbody tr {{ page-break-inside: avoid; }}
  table.report tbody tr:nth-child(even) {{ background:#f8fafc; }}
  .bname {{ font-weight:700; font-size:11px; color:#111827; }}
  .row-badges {{ display:flex; align-items:center; gap:6px; margin-top:3px; }}
  .meta {{ margin-top:4px; }}
  .badge {{ display:inline-block; padding:1px 6px; border-radius:3px; font-weight:700; font-size:7.5px; color:#fff; }}
  .badge-high {{ background:#1f2937; }}
  .badge-med  {{ background:#64748b; }}
  .badge-low  {{ background:#cbd5e1; color:#475569; }}
  .confbar {{ display:inline-block; width:42px; height:6px; background:#e2e8f0; border-radius:3px; overflow:hidden; vertical-align:middle; }}
  .conffill {{ height:100%; background:#1f2937; }}
  .conftxt {{ font-size:7.5px; color:#64748b; margin-left:3px; }}
  .cat {{ display:inline-block; background:#f1f5f9; color:#334155; border-radius:3px; padding:1px 6px; font-size:7.5px; font-weight:600; }}
  .src {{ display:inline-block; background:#f1f5f9; color:#475569; border:1px solid #e2e8f0;
          border-radius:3px; padding:1px 5px; font-size:7px; font-weight:700; text-transform:uppercase; }}
  .line {{ margin-bottom:2px; }}
  .owner, .ownerph {{ color:#1f2937; }}
  .ownerph {{ background:#f1f5f9; border-left:2px solid #1f2937; padding-left:4px; }}
  .confchip {{ background:#e2e8f0; color:#475569; border-radius:3px; font-size:7px; padding:0 4px; font-weight:700; }}
  .pill {{ display:inline-block; border-radius:50%; width:11px; height:11px; text-align:center; line-height:11px; font-size:7px; font-weight:700; }}
  .pill.ok {{ background:#1f2937; color:#fff; }}
  .pill.no {{ background:#e2e8f0; color:#94a3b8; }}
  .muted {{ color:#94a3b8; }}
  .empty {{ text-align:center; color:#94a3b8; font-style:italic; padding:14px; }}
  a {{ color:#1f2937; text-decoration:none; }}
  .pitch {{ display:inline-block; border-radius:3px; padding:1px 6px; font-size:7.5px; font-weight:700; text-transform:uppercase; color:#fff; }}
  .pitch-ready {{ background:#1f2937; }}
  .pitch-needs {{ background:#64748b; }}
  .pitch-nope {{ background:#cbd5e1; color:#475569; }}
</style></head><body>
  <div class="cover">
    <h1>Lead Generation Report</h1>
    <div class="sub">{_esc(job.niche.title())} &middot; {_esc(job.location.title())} &middot; Generated {now}</div>
    <div class="sub">Sources: Google Maps, Yelp, YellowPages &amp; Hotfrog</div>
    <div class="kpis">{kpi_html}</div>
  </div>
  {_section('Category A — No Website (Priority Leads)', 'hot', no_web)}
  {_section('Category B — Has Website', 'web', has_web)}
</body></html>"""


# ── Rendering ────────────────────────────────────────────────────────────────

async def _render_chromium(html: str, out: Path) -> bool:
    from playwright.async_api import async_playwright
    footer = ('<div style="font-size:8px;color:#64748b;width:100%;padding:0 12mm;'
              'display:flex;justify-content:space-between;">'
              '<span>Lead-Generator Report</span>'
              '<span>Page <span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            await page.pdf(
                path=str(out),
                format="A4",
                landscape=True,
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=footer,
                margin={"top": "10mm", "bottom": "14mm", "left": "10mm", "right": "10mm"},
            )
            return True
        finally:
            await browser.close()


def _render_chromium_sync(html: str, out: Path) -> bool:
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_render_chromium(html, out))
        finally:
            loop.close()
    except Exception as e:
        logger.warning("Chromium PDF render failed: %s", e)
        return False


def build_pdf(leads: List[Lead], export_dir: Path, job) -> Optional[Path]:
    out = export_dir / f"{export_filebase(job)}.pdf"
    html = _html_report(leads, job)

    # 1) Chromium (primary)
    if _render_chromium_sync(html, out):
        logger.info("PDF saved via Chromium: %s", out)
        return out

    # 2) WeasyPrint (fallback)
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(str(out))
        logger.info("PDF saved via WeasyPrint fallback: %s", out)
        return out
    except Exception as e:
        logger.error("PDF generation failed (Chromium + WeasyPrint): %s", e)
        return None


if __name__ == "__main__":
    # ponytail: one runnable check — HTML has no gradients, shows names + KPI summary.
    from types import SimpleNamespace

    def _fake(name, cat, score):
        l = SimpleNamespace()
        for c in Lead.__table__.columns:
            setattr(l, c.name, None)
        l.name, l.category, l.lead_score = name, cat, score
        l.data_confidence, l.sources = 0.8, "google_maps,yelp"
        return l

    leads = [_fake("Acme Plumbing", LeadCategory.NO_WEBSITE, 80),
             _fake("Beta Dental", LeadCategory.HAS_WEBSITE, 30)]
    job = SimpleNamespace(niche="plumbers", location="austin tx")
    html = _html_report(leads, job)

    assert "gradient" not in html, "HTML still contains a gradient"
    assert "Acme Plumbing" in html and "Beta Dental" in html, "business names missing"
    assert "Total Leads" in html, "KPI summary marker missing"
    print("OK: no gradients, names + KPI summary present")
