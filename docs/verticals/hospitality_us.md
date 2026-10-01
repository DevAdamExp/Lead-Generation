# US Hospitality (Lodging) — How the Business Works, Compliance & Pain Points

> Independent hotels, motels, B&Bs, resorts, event venues, short-term rentals.
> Reference for the pitch engine. **State/local-variable items flagged `[STATE]`.**

## 1. How the money actually works
- Revenue = **occupancy × ADR** (→ **RevPAR**). **High fixed costs**; **an empty room
  tonight is lost forever** (perishable inventory) → urgency to fill.
- **OTA-dependent**: Booking.com/Expedia take **15–25%** commission; Airbnb for STRs. Low
  **direct-booking share** = bleeding margin on every stay.
- Capital-intensive; financing/refi sensitive. Decision-maker = owner (small motels/B&Bs)
  or **GM/revenue manager** (larger); marketing director at scale. Independents are the
  buyers; **franchises have corporate marketing** (different/no pitch).

## 2. Licensing & permits
- Business license, **lodging/hotel license**, **food/health permit** (if F&B), **liquor
  license** (if bar/restaurant), pool/spa permits, elevator certs, fire/life-safety,
  **ADA** accessibility compliance, **Transient Occupancy Tax registration**.
- **Short-term rentals**: many cities now require **STR registration/permits + caps** `[CITY]`.

## 3. Taxes (the burden)
- **Transient Occupancy Tax / hotel/bed/lodging tax** — **local, often 10–17%** `[CITY]`,
  collected from guests and remitted; **major compliance + audit** area (also now enforced
  on Airbnb/STRs).
- Sales tax (on F&B, retail), income, payroll, **tip reporting** (F&B/spa staff).

## 4. Mandatory recordkeeping & data retention (what they MUST save)
| Record | Why / who | Typical retention |
|---|---|---|
| **Guest register / folios**, occupancy & ADR | TOT filing, audits | 3–7 yr |
| **TOT filings** & exemption certs | local tax | per locality |
| Payroll, W-4, I-9, tip records | IRS/DOL | 3 yr+ |
| **Card data** (PCI-DSS) | card brands/law | ongoing |
| **ADA** compliance records | DOJ/lawsuits | ongoing |
| Safety/incident logs, pool/spa health logs | liability/health | varies |
| OTA contracts, rate agreements | revenue/legal | term + |
| Franchise reporting (if branded) | franchisor | per agreement |

## 5. Compliance hot spots
- **ADA accessibility** — a very common **lawsuit/demand-letter** target (website + physical).
- **PCI-DSS** data security for stored guest cards (breach liability).
- Fire/life-safety codes, pool/spa health codes, food safety (if dining), labor laws,
  franchise brand standards.

## 6. Operational process flow (end to end)
`Demand/marketing → reservation (OTA or direct) → check-in → housekeeping ops → guest
services/upsell → check-out → review/reputation → revenue management (rate/inventory) →
repeat/loyalty`.
Leak points: **OTA commission, low direct share, occupancy gaps/seasonality, weak booking
engine, under-used guest email list, reputation, no upsell**.

## 7. Pain points → where an agency sells
| Pain | Sell |
|---|---|
| **OTA commissions 15–25%** | **direct-booking site + booking engine** (lead with reclaimed $) |
| Low direct-booking share | Google Hotel Ads / metasearch, retargeting |
| Reputation (TripAdvisor/Google) | reputation management + response |
| Under-used guest list | **email/CRM** for repeat stays |
| No upsell (spa, late checkout, dining) | upsell tooling |
| Occupancy gaps / seasonality | shoulder-season campaigns, packages |
| ADA/PCI exposure | compliant site rebuild (risk-reduction angle) |

## 8. Lead signals to capture (ties to pipeline)
**OTA presence (Booking/Expedia/Airbnb)** + own-site **booking engine?** (the wedge),
**room count / property size** (qualification), **ADR/price band**, Google + TripAdvisor
rating/count/**recency**, **independent vs franchise**, amenities (spa/restaurant/pool/
**events/weddings**), location type + seasonality, running ads (Meta Ad Library).
