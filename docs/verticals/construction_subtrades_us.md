# US Construction — Sub-Trade Deep Dive

> "Construction" is not one business. Ticket size, **recurring vs one-shot revenue**,
> seasonality, sales motion, and pain differ sharply by trade — so the pitch engine should
> branch on the specific trade, not "contractor." Pairs with [construction_us.md](construction_us.md).
> **State-variable items flagged `[STATE]`.**

## The two strategic axes (read this first)
1. **Recurring vs one-shot revenue** — this changes the entire pitch:
   - **Recurring** (HVAC service plans, landscaping/lawn, pest, pool): sell **LTV / retention
     / maintenance-plan growth + route density**. Each customer is worth years.
   - **One-shot high-ticket** (roofing, remodel, solar, concrete): sell **lead VOLUME +
     speed-to-lead + close rate**. They need a constant flow of *new* jobs.
2. **Insurance/storm-driven vs homeowner-paid** — roofing, restoration, some exteriors run
   on **insurance claims** (storm events = demand spikes, claim-supplement expertise,
   door-knocking). Homeowner-paid (remodel, landscaping) is design/trust/financing-driven.

## Per-trade table
| Trade | Ticket | Revenue type | Peak season | Primary channel | Sharpest pain | Pitch wedge |
|---|---|---|---|---|---|---|
| **Roofing** | $8k–$30k+ | One-shot, often **insurance** | Post-storm / spring–fall | Door-knock, Google, storm chasing, Angi | Lead-hungry, commoditized, financing on big tickets, **speed-to-lead** | Local SEO + reviews + instant lead response + financing display; **storm-triggered campaigns** |
| **HVAC** | $5k–$15k install; $150–$500 service | **Recurring** (maintenance plans) + install | Summer (AC) / winter (heat) **peaks** | "AC repair near me", service contracts | Seasonal swings, idle techs off-season, low **maintenance-plan** attach | **Maintenance-plan growth + LTV**, off-season demand campaigns, review velocity |
| **Plumbing** | $200–$2k repair; $5k–$20k repipe/remodel | Mostly **emergency** + project | Year-round; winter freeze spikes | **"plumber near me"** (high intent), 24/7 | Emergency response, missed after-hours calls, price competition | **Speed-to-lead / call capture**, map pack for "near me", reviews |
| **Electrical** | $150–$2k svc; $3k–$15k panel/EV/solar | Service + project | Year-round | Google, GC referrals, **EV/solar tie-in** | Licensing-gated, commercial vs resi split | Panel-upgrade / **EV-charger** demand campaigns, GC partnership funnel |
| **Remodeling / GC (kitchen & bath)** | $25k–$150k+ | One-shot, **long cycle** | Spring–fall | **Houzz, portfolio, referrals**, Instagram | Long sales cycle, design trust, **financing**, lead quality | Portfolio site + galleries, **lead nurturing** for long cycle, financing, Houzz/IG presence |
| **Concrete / masonry / paving** | $3k–$30k | One-shot | Warm/dry months `[STATE]` | Google, referrals | **Weather-dependent**, visual proof, seasonal | Before/after galleries, off-season booking, reviews |
| **Landscaping / hardscape / lawn** | $50–$300/mo maint; $5k–$50k design-build | **Recurring** (maint) + project | Spring–fall; snow in winter `[STATE]` | Google, neighborhood/**route density** | Maintenance churn, **route density**, seasonal labor | Recurring-plan retention, neighborhood density campaigns, design-build upsell |
| **Painting** | $2k–$8k | One-shot, **high volume** | Spring–fall | Google, Angi, reviews | Low ticket → needs **volume**, commoditized | Reviews + before/after, fast quoting, volume lead gen |
| **Solar** | $15k–$40k | One-shot, **very long cycle** | Year-round (incentive-driven) | Paid lead gen, door-knock, referrals | **Heavy regulation/permitting**, financing, **federal ITC** incentive timing `[STATE]`, lead cost | Incentive-deadline urgency campaigns, financing, long-cycle nurture, reputation |
| **Restoration (water/fire/mold)** | $3k–$50k | **Insurance**, emergency | Event-driven | 24/7 emergency, insurance/agent referrals, Google | Must be **first call**, insurance billing, 24/7 | Emergency call capture + speed-to-lead, agent-referral funnel, reviews |
| **Windows/doors/siding/fencing** | $5k–$30k | One-shot | Spring–fall | Google, Angi, home shows | Big-ticket close, financing | Financing display, galleries, retargeting |
| **Pools** | $40k–$100k+ build; service recurring | Build (one-shot) + **service** | Spring (build); summer | Houzz, referrals, Google | Seasonal, very long build cycle | Long-cycle nurture + service-plan recurring |

## How this changes the data we capture & the opener
- **Recurring trades** → also capture: does the site **advertise a maintenance/service plan?**
  (if not → the #1 pitch). Opener: *"You're great at installs, but I don't see a service-plan
  signup anywhere — that's the recurring revenue most [HVAC] shops your size are missing."*
- **One-shot high-ticket** → capture **financing offered?** + **review velocity** + **gallery
  present?**. Opener leads with map-pack/review gap or a missing financing/quote path.
- **Insurance/storm trades** → capture **24/7?**, **"insurance claims welcome"** language,
  and tie campaigns to **recent storm events** in the area (timing wedge).
- **Long-cycle trades (remodel/solar/pools)** → the pitch is **lead nurturing**, not just lead
  gen — capture whether they have any follow-up/CRM signal (usually none).

## Trade-specific licensing/compliance notes
- **Electrical, plumbing, HVAC**: almost always **state license + exam** + continuing ed `[STATE]`.
- **Roofing/solar**: licensing varies widely `[STATE]`; solar adds **utility interconnection +
  permitting + incentive paperwork** (a real ops burden = automation angle).
- **Restoration**: **IICRC certifications**; insurance billing (Xactimate) literacy.
- All: trade-class **workers' comp** rates differ (roofing is among the highest-risk/most
  expensive comp classes — a cost pressure worth knowing).
