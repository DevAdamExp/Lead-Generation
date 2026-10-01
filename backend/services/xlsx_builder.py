"""
services/xlsx_builder.py — Polished, analyst-grade XLSX export.

Features:
  - "All Leads" master sheet + two category sheets (No Website / Has Website).
  - Clickable hyperlinks for website, email, phone (tel:), and Google Maps.
  - Verification badges (✓ verified email/phone) and owner-phone confidence.
  - AutoFilter, frozen header, alternating row shading.
  - Conditional-format colour scale on Lead Score and Data Confidence.
  - Summary sheet with per-source counts.
"""
import logging
from pathlib import Path
from datetime import datetime, timezone

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule

from backend.models import Lead, LeadCategory
from backend.utils.naming import export_filebase

logger = logging.getLogger(__name__)

COLOR_HEADER_BG  = "0F172A"
COLOR_HEADER_FG  = "F8FAFC"
COLOR_CAT_A_BG   = "0F766E"
COLOR_CAT_B_BG   = "1D4ED8"
COLOR_ALL_BG     = "0F172A"
COLOR_ROW_ALT    = "F1F5F9"
COLOR_LINK       = "2563EB"
COLOR_SCORE_HIGH = "16A34A"
COLOR_SCORE_MED  = "D97706"
COLOR_SCORE_LOW  = "DC2626"

# (label, attribute, width, kind)  kind ∈ {text, website, email, tel, maps, bool, score, pct}
COLUMNS = [
    ("Business Name",   "name",                   30, "text"),
    ("Category",        "business_category",      18, "text"),
    ("Lead Score",      "lead_score",             11, "score"),
    ("Confidence",      "data_confidence",        12, "pct"),
    ("Verification",    "verification_tier",      14, "tier"),
    ("Phone",           "phone_formatted",        16, "tel"),
    ("Phone ✓",         "phone_verified",          8, "bool"),
    ("Owner Name",      "owner_name",             20, "text"),
    ("Owner Phone",     "owner_phone",            16, "tel"),
    ("Owner Ph. Type",  "owner_phone_type",       13, "text"),
    ("Owner Ph. Conf.", "owner_phone_confidence", 12, "pct"),
    ("Owner Email",     "owner_email",            28, "email"),
    ("Email ✓",         "email_verified",          8, "bool"),
    ("Pitch Ready",     "pitch_ready",            13, "pitch"),
    ("Website",         "website",                32, "website"),
    ("Website CMS",     "website_cms",            14, "text"),
    ("SSL",             "has_ssl",                 7, "bool"),
    ("Name On Site",    "website_name_found",     13, "bool"),
    ("Google Maps",     "google_maps_url",        12, "maps"),
    ("Rating",          "google_rating",           8, "text"),
    ("Reviews",         "google_review_count",    10, "text"),
    ("Hours",           "hours",                  26, "text"),
    ("Address",         "address",                36, "text"),
    ("Facebook",        "social_facebook",        26, "website"),
    ("Instagram",       "social_instagram",       26, "website"),
    ("Sources",         "sources",                20, "text"),
    ("Description",     "description",            50, "text"),
    ("Employee Count",  "employee_count",         16, "text"),
    ("Year Founded",    "year_founded",           14, "text"),
    ("Partners",        "partners",               30, "text"),
    ("Recent Activity", "recent_activity",        40, "text"),
    ("Services",        "services",               35, "text"),
    ("Owner Title",     "owner_title",            16, "text"),
    ("Review Weakness", "review_weaknesses",      30, "text"),
    ("Weakness Count",  "review_weakness_count",  15, "text"),
    ("Review Themes",   "review_themes",          30, "text"),
    ("Last Review",     "last_review_date",       14, "text"),
    ("Owner Responds",  "owner_responds",          8, "bool"),
    ("Legal Name",      "legal_name",             28, "text"),
    ("Entity Type",     "entity_type",            14, "text"),
    ("Entity Status",   "entity_status",          13, "text"),
    ("Registered",      "registration_date",      14, "text"),
    ("Registry",        "registry_source",        14, "text"),
    ("Relies On (3rd-party)", "external_platforms", 24, "text"),
    ("Marketing Stack", "marketing_stack",          24, "text"),
    ("Pitch Angle",     "pitch_angle",            40, "text"),
    ("Pain Points",     "pain_points",            40, "text"),
    ("Email Opener",    "opener",                 45, "text"),
]


def _fill(color: str):
    return PatternFill("solid", fgColor=color)


def _border():
    s = Side(style="thin", color="CBD5E1")
    return Border(left=s, right=s, top=s, bottom=s)


def _score_color(score) -> str:
    score = score or 0
    if score >= 70:
        return COLOR_SCORE_HIGH
    if score >= 40:
        return COLOR_SCORE_MED
    return COLOR_SCORE_LOW


def _set_link(cell, url: str, display: str):
    cell.value = display
    cell.hyperlink = url
    cell.font = Font(color=COLOR_LINK, underline="single", size=10)


def _write_sheet(ws, leads: list, header_color: str, sheet_title: str):
    ncols = len(COLUMNS)
    last_col = get_column_letter(ncols)

    # Title
    ws.merge_cells(f"A1:{last_col}1")
    c = ws["A1"]
    c.value = sheet_title
    c.font = Font(bold=True, size=13, color="FFFFFF")
    c.fill = _fill(header_color)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Sub-header
    ws.merge_cells(f"A2:{last_col}2")
    c = ws["A2"]
    c.value = (f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
               f"   ·   {len(leads)} leads")
    c.font = Font(italic=True, size=9, color="64748B")
    c.alignment = Alignment(horizontal="center")

    # Column headers
    hr = 3
    for i, (label, _f, width, _k) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=hr, column=i, value=label)
        cell.font = Font(bold=True, color=COLOR_HEADER_FG, size=10)
        cell.fill = _fill(COLOR_HEADER_BG)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _border()
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[hr].height = 24

    # Pre-compute pitch readiness for each lead
    for lead in leads:
        has_contact = bool(lead.owner_email or lead.owner_phone or lead.phone)
        has_owner = bool(lead.owner_name)
        site_confirmed = bool(lead.website_name_found) or bool(lead.has_google_maps)
        if has_contact and has_owner and site_confirmed and lead.lead_score and lead.lead_score >= 40:
            lead._pitch_tier = "Ready"
        elif has_contact and site_confirmed:
            lead._pitch_tier = "Needs Owner"
        elif has_contact:
            lead._pitch_tier = "Needs Site"
        else:
            lead._pitch_tier = "Needs Contact"

    # Data rows
    for r, lead in enumerate(leads, hr + 1):
        bg = _fill(COLOR_ROW_ALT) if (r % 2 == 0) else None
        for i, (_label, field, _w, kind) in enumerate(COLUMNS, 1):
            cell = ws.cell(row=r, column=i)
            cell.border = _border()
            cell.alignment = Alignment(vertical="center",
                                       wrap_text=(field == "description"))
            if bg:
                cell.fill = bg

            # Compute pitch_ready on the fly
            if field == "pitch_ready":
                tier = getattr(lead, "_pitch_tier", "Needs Contact")
                cell.value = tier
                if tier == "Ready":
                    cell.font = Font(color="166534", bold=True)
                    if bg:
                        cell.fill = PatternFill("solid", fgColor="C6F4D6")
                    else:
                        cell.fill = PatternFill("solid", fgColor="DCFCE7")
                elif tier == "Needs Owner":
                    cell.font = Font(color="92400E", bold=False)
                    if bg:
                        cell.fill = PatternFill("solid", fgColor="FEF0C7")
                    else:
                        cell.fill = PatternFill("solid", fgColor="FEF3C7")
                else:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                continue

            val = getattr(lead, field, None)

            if kind == "bool":
                cell.value = "✓" if val else "—"
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if val:
                    cell.font = Font(color=COLOR_SCORE_HIGH, bold=True)
            elif kind == "score":
                cell.value = val or 0
                cell.font = Font(bold=True, color=_score_color(val))
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif kind == "pct":
                cell.value = float(val or 0)
                cell.number_format = "0%"
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif kind == "website" and val:
                disp = val.replace("https://", "").replace("http://", "")[:38]
                _set_link(cell, val, disp)
            elif kind == "email" and val:
                _set_link(cell, f"mailto:{val}", val)
            elif kind == "tel" and val:
                _set_link(cell, f"tel:{val}", val)
            elif kind == "maps" and val:
                _set_link(cell, val, "View on Maps")
            elif kind == "tier":
                tier = val or "unverified"
                cell.value = tier.capitalize()
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if tier == "verified":
                    cell.font = Font(color="166534", bold=True)
                    cell.fill = PatternFill("solid", fgColor="C6F4D6" if bg else "DCFCE7")
                elif tier == "corroborated":
                    cell.font = Font(color="92400E")
                    cell.fill = PatternFill("solid", fgColor="FEF0C7" if bg else "FEF3C7")
                else:
                    cell.font = Font(color="991B1B")
                    cell.fill = PatternFill("solid", fgColor="FEE2E2")
            else:
                cell.value = "" if val is None else val

    # AutoFilter + freeze
    ws.auto_filter.ref = f"A{hr}:{last_col}{hr + len(leads)}"
    ws.freeze_panes = f"A{hr + 1}"

    # Conditional colour scales on Score + Confidence
    if leads:
        score_col = get_column_letter(_col_index("lead_score"))
        conf_col = get_column_letter(_col_index("data_confidence"))
        rng_score = f"{score_col}{hr + 1}:{score_col}{hr + len(leads)}"
        rng_conf = f"{conf_col}{hr + 1}:{conf_col}{hr + len(leads)}"
        ws.conditional_formatting.add(rng_score, ColorScaleRule(
            start_type="num", start_value=0, start_color="FCA5A5",
            mid_type="num", mid_value=50, mid_color="FDE68A",
            end_type="num", end_value=100, end_color="86EFAC"))
        ws.conditional_formatting.add(rng_conf, ColorScaleRule(
            start_type="num", start_value=0, start_color="FCA5A5",
            mid_type="num", mid_value=0.5, mid_color="FDE68A",
            end_type="num", end_value=1, end_color="86EFAC"))


def _col_index(field: str) -> int:
    for i, (_l, f, _w, _k) in enumerate(COLUMNS, 1):
        if f == field:
            return i
    return 1


def build_xlsx(leads: list, export_dir: Path, job) -> Path:
    wb = openpyxl.Workbook()

    no_web  = [l for l in leads if l.category == LeadCategory.NO_WEBSITE]
    has_web = [l for l in leads if l.category == LeadCategory.HAS_WEBSITE]
    ordered = sorted(leads, key=lambda l: (l.lead_score or 0), reverse=True)

    ws_all = wb.active
    ws_all.title = "All Leads"
    _write_sheet(ws_all, ordered, COLOR_ALL_BG,
                 f"ALL LEADS  ·  {job.niche.title()}  ·  {job.location}")

    ws1 = wb.create_sheet("No Website (Hot)")
    _write_sheet(ws1, sorted(no_web, key=lambda l: (l.lead_score or 0), reverse=True),
                 COLOR_CAT_A_BG, f"NO WEBSITE — HOT LEADS  ·  {job.niche.title()}  ·  {job.location}")

    ws2 = wb.create_sheet("Has Website")
    _write_sheet(ws2, sorted(has_web, key=lambda l: (l.lead_score or 0), reverse=True),
                 COLOR_CAT_B_BG, f"HAS WEBSITE  ·  {job.niche.title()}  ·  {job.location}")

    # Summary
    ws3 = wb.create_sheet("Summary")
    _summary(ws3, leads, no_web, has_web, job)

    out = export_dir / f"{export_filebase(job)}.xlsx"
    wb.save(str(out))
    logger.info("XLSX saved: %s", out)
    return out


def _summary(ws, leads, no_web, has_web, job):
    def src_count(name):
        return sum(1 for l in leads if l.sources and name in l.sources)

    rows = [
        ("Metric", "Value"),
        ("Niche", job.niche),
        ("Location", job.location),
        ("Total Leads", len(leads)),
        ("No Website (Hot)", len(no_web)),
        ("Has Website", len(has_web)),
        ("With Owner Name", sum(1 for l in leads if l.owner_name)),
        ("With Owner Phone", sum(1 for l in leads if l.owner_phone)),
        ("With Owner Title", sum(1 for l in leads if l.owner_title)),
        ("With Email", sum(1 for l in leads if l.owner_email)),
        ("Verified Email", sum(1 for l in leads if l.email_verified)),
        ("Verified Phone", sum(1 for l in leads if l.phone_verified)),
        ("Tier: Verified", sum(1 for l in leads if getattr(l, "verification_tier", "") == "verified")),
        ("Tier: Corroborated", sum(1 for l in leads if getattr(l, "verification_tier", "") == "corroborated")),
        ("Tier: Unverified", sum(1 for l in leads if getattr(l, "verification_tier", "") == "unverified")),
        ("On Google Maps", sum(1 for l in leads if l.has_google_maps)),
        ("Name On Website", sum(1 for l in leads if l.website_name_found)),
        ("Pitch Ready", sum(1 for l in leads if getattr(l, '_pitch_tier', '') == 'Ready')),
        ("With Services", sum(1 for l in leads if l.services)),
        ("With Team Roster", sum(1 for l in leads if l.team_members)),
        ("Avg Lead Score", round(sum((l.lead_score or 0) for l in leads) / max(len(leads), 1), 1)),
        ("Avg Confidence", round(sum((l.data_confidence or 0) for l in leads) / max(len(leads), 1), 2)),
        ("Count Emp. Count", sum(1 for l in leads if l.employee_count)),
        ("Count Year Founded", sum(1 for l in leads if l.year_founded)),
        ("Count Partners", sum(1 for l in leads if l.partners)),
        ("— Sources —", ""),
        ("From Google Maps", src_count("google_maps")),
        ("From YellowPages", src_count("yellowpages")),
        ("From Yelp", src_count("yelp")),
        ("From Hotfrog", src_count("hotfrog")),
        ("Generated At", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
    ]
    for r, (k, v) in enumerate(rows, 1):
        kc = ws.cell(row=r, column=1, value=k)
        vc = ws.cell(row=r, column=2, value=v)
        if r == 1:
            kc.font = vc.font = Font(bold=True, color="FFFFFF")
            kc.fill = vc.fill = _fill(COLOR_HEADER_BG)
        elif k.startswith("—"):
            kc.font = Font(bold=True, italic=True, color="64748B")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 30
    ws.freeze_panes = "A2"
