# Vertical Research — US Business Knowledge Base

Domain reference for the **pitch engine** (`backend/services/pitch_research.py`). Each doc
captures how a niche actually operates in the US — money flow, licensing, taxes, mandatory
recordkeeping/data retention, labor/safety compliance, the operational process, and the
**pain points that map to what an agency sells**. The point: so outreach references a
business's *real* headaches (lien waivers, tip reporting, OTA commissions) and sounds like
an insider, not a vendor.

## Docs
- [construction_us.md](construction_us.md) — GCs, remodelers, roofers, HVAC, plumbing,
  electrical, concrete, landscaping (the shared compliance/tax/process spine).
- [construction_subtrades_us.md](construction_subtrades_us.md) — **per-trade** deep dive:
  ticket size, recurring vs one-shot revenue, seasonality, channel, pain, and pitch wedge
  for roofing / HVAC / plumbing / electrical / remodel / concrete / landscaping / painting /
  solar / restoration / windows / pools.
- [restaurants_us.md](restaurants_us.md) — independents & small groups; QSR → fine dining,
  cafés, bars.
- [hospitality_us.md](hospitality_us.md) — independent hotels, motels, B&Bs, resorts,
  venues, short-term rentals.

## How this feeds the product
Per-lead `business_category` → select the matching vertical's **pain points + lead signals
+ pitch angles** → frame the `pitch_research` LLM prompt for that niche. Next build step:
a `vertical_profiles.py` that turns these docs into a `category → {priority_signals,
pitch_angles, opener_framing}` config the prompt branches on.

## ⚠️ Accuracy / maintenance
Tax rates, license thresholds, and OSHA/health/ADA specifics **vary by state/locality and
change yearly** — items marked `[STATE]`/`[CITY]` are placeholders to verify against the
current state/local source before relying on a number. Treat these as the durable
*structure* of each business, with a calibration knob for jurisdiction-specific values.
