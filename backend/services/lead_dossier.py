"""services/lead_dossier.py — per-lead research dossier + personalised PDF.

Distinct from pitch_research.py, which writes three short sales fields into the
spreadsheet. This produces a STANDALONE DOCUMENT for one lead:

  1. what the business is and what it actually sells
  2. weaknesses visible in its reviews / reputation
  3. what we can pitch it
  4. market position — who its competitors are and where they are beating it
  5. a concrete pitch plan

Two inputs make it specific rather than generic:
  - live page text from the lead's own website (regex already missed most of
    services/owner/team, so the model reads the page directly)
  - a competitor table built from OTHER leads in the same niche + city, with
    their real ratings and review counts

# ponytail: competitors come from leads we already scraped, not a web search.
# Same city, same niche, real numbers — and it costs nothing extra. Swap in a
# search tool only if same-job competitors prove too thin.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a B2B agency strategist preparing a research brief on ONE prospect. "
    "You reason only from the facts supplied. You never invent numbers, owner "
    "names, competitors or claims. Where a fact is absent you say so plainly "
    "instead of guessing. You answer with a single valid JSON object, nothing else."
)

_PROMPT = """Prepare a research dossier on this prospect for a digital-marketing agency.

{vertical_context}

=== THE PROSPECT ===
Business: {name}
Category: {category}
Location: {address}
Website: {website} | platform: {cms} | secure (SSL): {ssl}
Rating: {rating} from {reviews} reviews | open now: {is_open}
Services on record: {services}
Description on record: {description}
Owner: {owner_name} ({owner_title}) | team size: {employees} | founded: {founded}
Socials: {socials}
Third-party platforms they depend on: {platforms}
Marketing/ad tech detected on their site: {stack} | running paid ads: {ads}
Reputation signals already computed: {weaknesses}
Verbatim negative review snippets: {review_texts}

=== THEIR WEBSITE (visible text, truncated) ===
{page_text}

=== COMPETITORS (other {category} businesses in the same area, real scraped data) ===
{competitors}

Return ONLY this JSON object:
{{
  "business_overview": "3-5 sentences: what this business is and what it actually sells, grounded in their website text. Name specific services. If the site gave little away, say what is missing instead of padding.",
  "services": ["specific service", "..."],
  "review_weaknesses": ["each weakness their reputation reveals, tied to the rating/review evidence above", "..."],
  "market_position": "3-5 sentences comparing them to the competitor table: where they stand on rating and review volume, and specifically what the stronger competitors are doing that they are not. Cite the actual numbers.",
  "competitor_edge": ["a concrete thing a named competitor does better, with the number that shows it", "..."],
  "pitch_opportunities": ["a specific service we can sell, each tied to a gap proven above", "..."],
  "pitch_plan": {{
     "primary_offer": "the one service to lead with, and why this business specifically",
     "why_now": "the trigger that makes this urgent for them",
     "opening_line": "a cold-email opener under 25 words referencing a concrete detail. No placeholders, no [brackets] — write the real words.",
     "talking_points": ["point backed by a fact above", "..."],
     "expected_objection": "what they will push back with, and the answer"
  }},
  "confidence_note": "one honest sentence on what you could NOT determine from the supplied facts"
}}

Rules:
- Every claim traces to a fact above. No invented competitors, numbers or names.
- Competitor statements must come from the competitor table, naming the business.
- If the website text was empty, say so in business_overview and confidence_note —
  do not describe services you cannot see.
- opening_line must contain no placeholder of any kind."""


def build_competitor_table(lead, peers: list, limit: int = 6) -> str:
    """Rank same-niche/city peers so the model can compare against real numbers."""
    rows = []
    for p in peers:
        if getattr(p, "id", None) == getattr(lead, "id", None):
            continue
        if not p.name:
            continue
        rows.append(p)
    rows.sort(key=lambda p: ((p.google_rating or 0), (p.google_review_count or 0)),
              reverse=True)
    if not rows:
        return "(no competitor data available for this area)"
    out = []
    for p in rows[:limit]:
        bits = [f"- {p.name}"]
        if p.google_rating:
            bits.append(f"rating {p.google_rating} from {p.google_review_count or 0} reviews")
        else:
            bits.append("no rating on Google Maps")
        bits.append(f"website: {'yes' if p.website else 'NO WEBSITE'}")
        if p.website_cms:
            bits.append(f"platform {p.website_cms}")
        if p.services:
            bits.append(f"services: {str(p.services)[:110]}")
        out.append(" | ".join(bits))
    return "\n".join(out)


def _facts(lead, page_text: str, competitors: str) -> dict:
    socials = ", ".join(s for s, v in (
        ("facebook", lead.social_facebook), ("instagram", lead.social_instagram),
        ("linkedin", lead.social_linkedin), ("twitter", lead.social_twitter)) if v)
    from backend.services.pitch_research import _review_snippets
    return {
        "name": lead.name or "(unknown)",
        "category": lead.business_category or "business",
        "address": lead.address or "(unknown)",
        "website": lead.website or "(none)",
        "cms": lead.website_cms or "(unknown)",
        "ssl": "yes" if lead.has_ssl else "no/unknown",
        "rating": lead.google_rating if lead.google_rating is not None else "(unknown)",
        "reviews": lead.google_review_count or 0,
        "is_open": lead.google_is_open if lead.google_is_open is not None else "(unknown)",
        "services": lead.services or "(none on record)",
        "description": (lead.description_long or lead.description or "(none)")[:600],
        "owner_name": lead.owner_name or "(unknown)",
        "owner_title": lead.owner_title or "(unknown)",
        "employees": lead.employee_count if lead.employee_count else "(unknown)",
        "founded": lead.year_founded if lead.year_founded else "(unknown)",
        "socials": socials or "(none found)",
        "platforms": lead.external_platforms or "(none detected)",
        "stack": lead.marketing_stack or "(none detected)",
        "ads": "yes" if lead.runs_paid_ads else "no/unknown",
        "weaknesses": lead.review_weaknesses or "(none computed)",
        "review_texts": _review_snippets(lead.review_lowest_texts) or "(none scraped)",
        "page_text": page_text[:5000] if page_text else "(website text unavailable)",
        "competitors": competitors,
    }


def fetch_page_text(lead, timeout: int = 12) -> str:
    """Live page text for the lead's own site — the material that makes the
    dossier specific. Empty string when there's nothing to read."""
    if not lead.website:
        return ""
    try:
        import httpx
        from backend.services.business_intel import HEADERS, _visible_text
        from backend.services.contact_finder import _canonicalize_website
        r = httpx.get(_canonicalize_website(lead.website), headers=HEADERS,
                      timeout=timeout, follow_redirects=True)
        return _visible_text(r.text) if r.status_code < 400 else ""
    except Exception as e:
        logger.info("dossier page fetch failed for %s: %s", lead.name, e)
        return ""


def build_dossier(lead, peers: list, *, api_key: str, base_url: str,
                  models: list, page_text: Optional[str] = None) -> dict:
    """One LLM call -> the five dossier sections. Raises on total failure."""
    from backend.services.pitch_research import _call_nvidia, _loads_lenient
    from backend.services.vertical_profiles import match_profile

    if page_text is None:
        page_text = fetch_page_text(lead)
    competitors = build_competitor_table(lead, peers)
    facts = _facts(lead, page_text, competitors)
    prompt = _PROMPT.format(
        vertical_context=match_profile(lead.business_category).prompt_context(),
        **facts)
    raw = _call_nvidia(prompt, api_key=api_key, base_url=base_url, models=models,
                       system=_SYSTEM, max_tokens=2200)
    d = _loads_lenient(raw)

    from backend.services.pitch_research import _clean_text, _PLACEHOLDER_RE

    def _s(v):
        return _clean_text(v) or ""

    def _l(v):
        if isinstance(v, str):
            v = [v]
        return [_clean_text(x) for x in (v or []) if _clean_text(x)]

    plan = d.get("pitch_plan") or {}
    opener = _s(plan.get("opening_line"))
    if opener and _PLACEHOLDER_RE.search(opener):
        logger.info("dossier opener had a placeholder — dropped for %s", lead.name)
        opener = ""

    return {
        "business_overview": _s(d.get("business_overview")),
        "services": _l(d.get("services")),
        "review_weaknesses": _l(d.get("review_weaknesses")),
        "market_position": _s(d.get("market_position")),
        "competitor_edge": _l(d.get("competitor_edge")),
        "pitch_opportunities": _l(d.get("pitch_opportunities")),
        "pitch_plan": {
            "primary_offer": _s(plan.get("primary_offer")),
            "why_now": _s(plan.get("why_now")),
            "opening_line": opener,
            "talking_points": _l(plan.get("talking_points")),
            "expected_objection": _s(plan.get("expected_objection")),
        },
        "confidence_note": _s(d.get("confidence_note")),
        "_had_page_text": bool(page_text),
        "_competitor_count": 0 if competitors.startswith("(no competitor")
                             else competitors.count("\n") + 1,
    }


# ── PDF ───────────────────────────────────────────────────────────────────────

_CSS = """
:root{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--accent:#0f766e;--bg:#f8fafc;
      --warn:#b45309;--good:#16a34a}
*{box-sizing:border-box}
body{font:12.5px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     color:var(--ink);margin:0;padding:32px 38px}
.hdr{border-bottom:3px solid var(--ink);padding-bottom:12px;margin-bottom:18px}
h1{font-size:24px;margin:0 0 3px;letter-spacing:-.3px}
.sub{color:var(--mut);font-size:12px}
.tag{display:inline-block;background:var(--bg);border:1px solid var(--line);
     border-radius:20px;padding:2px 10px;font-size:10.5px;color:var(--mut);margin-right:5px}
.kpis{display:flex;gap:9px;margin:14px 0 6px}
.kpi{flex:1;border:1px solid var(--line);border-radius:8px;padding:9px 11px;background:var(--bg)}
.kpi .n{font-size:18px;font-weight:700;line-height:1.15}
.kpi .l{font-size:9.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px;margin-top:2px}
h2{font-size:12.5px;margin:22px 0 8px;color:var(--accent);text-transform:uppercase;
   letter-spacing:.9px;border-bottom:1px solid var(--line);padding-bottom:4px}
.n{margin:0 0 8px}
ul{margin:4px 0 8px;padding-left:17px}
li{margin:3px 0}
.box{border:1px solid var(--line);border-radius:8px;padding:11px 13px;background:var(--bg);margin:8px 0}
.box.warn{border-left:3px solid var(--warn)}
.box.good{border-left:3px solid var(--good)}
.k{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);
   font-weight:700;margin-bottom:3px}
.opener{font-size:13px;font-style:italic;padding:9px 12px;background:#ecfdf5;
        border-left:3px solid var(--good);border-radius:0 6px 6px 0;margin:6px 0}
table{width:100%;border-collapse:collapse;font-size:11.5px;margin:6px 0}
th{text-align:left;font-size:9.5px;text-transform:uppercase;letter-spacing:.5px;
   color:var(--mut);padding:5px 7px;border-bottom:1px solid var(--line)}
td{padding:5px 7px;border-bottom:1px solid var(--line)}
.me td{background:#fef9c3;font-weight:700}
.foot{margin-top:22px;padding-top:9px;border-top:1px solid var(--line);
      color:var(--mut);font-size:10px}
@page{size:A4;margin:12mm}
"""


def _e(v):
    return (str(v) if v is not None else "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


def _ul(items):
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>" if items else \
           "<p class='n' style='color:#64748b'>None identified from the available facts.</p>"


def dossier_html(lead, d: dict, peers: list) -> str:
    plan = d.get("pitch_plan") or {}
    h = [f"<style>{_CSS}</style>", "<div class='hdr'>",
         f"<h1>{_e(lead.name)}</h1>",
         f"<div class='sub'>{_e(lead.business_category or 'Business')}"
         f" &nbsp;·&nbsp; {_e(lead.address or '')}</div>",
         "<div style='margin-top:7px'>",
         f"<span class='tag'>score {lead.lead_score}</span>",
         f"<span class='tag'>confidence {lead.data_confidence}</span>",
         f"<span class='tag'>{_e(lead.verification_tier)}</span>",
         "</div></div>"]

    h.append("<div class='kpis'>")
    for n, l in ((lead.google_rating or "—", "google rating"),
                 (lead.google_review_count or 0, "reviews"),
                 ("yes" if lead.website else "NO", "website"),
                 (_e(lead.website_cms or "—"), "platform"),
                 (_e(lead.phone_formatted or lead.phone or "—"), "phone")):
        h.append(f"<div class='kpi'><div class='n'>{_e(n)}</div><div class='l'>{_e(l)}</div></div>")
    h.append("</div>")

    h.append("<h2>1 &nbsp;What this business does</h2>")
    h.append(f"<p class='n'>{_e(d.get('business_overview'))}</p>")
    if d.get("services"):
        h.append("<div class='k'>Services identified</div>" + _ul(d["services"]))

    h.append("<h2>2 &nbsp;Weaknesses in their reputation</h2>")
    h.append("<div class='box warn'>" + _ul(d.get("review_weaknesses")) + "</div>")

    h.append("<h2>3 &nbsp;What we can pitch them</h2>")
    h.append(_ul(d.get("pitch_opportunities")))

    h.append("<h2>4 &nbsp;Market position &amp; competitors</h2>")
    h.append(f"<p class='n'>{_e(d.get('market_position'))}</p>")
    if d.get("competitor_edge"):
        h.append("<div class='k'>Where competitors are ahead</div>" + _ul(d["competitor_edge"]))

    rivals = [p for p in peers if getattr(p, "id", None) != lead.id and p.name]
    rivals.sort(key=lambda p: ((p.google_rating or 0), (p.google_review_count or 0)), reverse=True)
    if rivals:
        h.append("<table><tr><th>Competitor</th><th>Rating</th><th>Reviews</th>"
                 "<th>Website</th><th>Platform</th></tr>")
        shown = rivals[:6] + [lead]
        shown.sort(key=lambda p: ((p.google_rating or 0), (p.google_review_count or 0)), reverse=True)
        for p in shown:
            me = " class='me'" if p.id == lead.id else ""
            label = _e(p.name) + (" &nbsp;(this lead)" if p.id == lead.id else "")
            h.append(f"<tr{me}><td>{label}</td><td>{_e(p.google_rating or '—')}</td>"
                     f"<td>{_e(p.google_review_count or 0)}</td>"
                     f"<td>{'yes' if p.website else 'NO'}</td>"
                     f"<td>{_e(p.website_cms or '—')}</td></tr>")
        h.append("</table>")

    h.append("<h2>5 &nbsp;Pitch plan</h2>")
    if plan.get("primary_offer"):
        h.append(f"<div class='box good'><div class='k'>Lead with</div>{_e(plan['primary_offer'])}</div>")
    if plan.get("why_now"):
        h.append(f"<div class='box'><div class='k'>Why now</div>{_e(plan['why_now'])}</div>")
    if plan.get("opening_line"):
        h.append(f"<div class='k'>Cold-email opener</div><div class='opener'>“{_e(plan['opening_line'])}”</div>")
    if plan.get("talking_points"):
        h.append("<div class='k'>Talking points</div>" + _ul(plan["talking_points"]))
    if plan.get("expected_objection"):
        h.append(f"<div class='box'><div class='k'>Expect this objection</div>{_e(plan['expected_objection'])}</div>")

    contact = [(k, v) for k, v in (("Phone", lead.phone_formatted or lead.phone),
                                   ("Email", lead.owner_email),
                                   ("Owner", lead.owner_name),
                                   ("Website", lead.website)) if v]
    if contact:
        h.append("<h2>How to reach them</h2><table>")
        for k, v in contact:
            h.append(f"<tr><td style='width:90px;color:#64748b'>{k}</td><td>{_e(v)}</td></tr>")
        h.append("</table>")

    if d.get("confidence_note"):
        h.append(f"<div class='box'><div class='k'>What we could not determine</div>"
                 f"{_e(d['confidence_note'])}</div>")

    src = []
    src.append("their website" if d.get("_had_page_text") else "NO website text available")
    src.append(f"{d.get('_competitor_count', 0)} scraped competitors")
    h.append(f"<div class='foot'>Researched {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
             f"from {', '.join(src)}, plus this lead's scraped record. Every statement is "
             f"drawn from those sources — nothing inferred beyond them.</div>")
    return "".join(h)


def render_dossier_pdf(lead, d: dict, peers: list, out_dir: Path) -> Optional[Path]:
    from backend.utils.naming import slugify
    from backend.services.pdf_builder import _render_chromium_sync
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slugify(lead.name or 'lead')}_dossier.pdf"
    html = dossier_html(lead, d, peers)
    if _render_chromium_sync(html, out):
        return out
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(str(out))
        return out
    except Exception as e:
        logger.error("dossier PDF failed for %s: %s", lead.name, e)
        out.with_suffix(".html").write_text(html)
        return out.with_suffix(".html")
