# Research: B2B Lead Generation Agency — Data Sources & Infrastructure

_Compiled: 2026-06-29_

---

## 1. US Government Business APIs

### SAM.gov (System for Award Management)
- **What it provides**: Federal contract opportunities, entity registrations (vendors doing business with US gov), exclusions database, federal hierarchy (agencies), award notices, PSC codes.
- **Data includes**: Title, agency, posted date, deadline, solicitation numbers, set-aside info, point-of-contact, place of performance, NAICS/PSC codes.
- **Cost**: Free. API key from SAM.gov account required.
- **Rate limits**: Basic account = 10 req/day. Entity-registered user = 1,000/day. Federal system user = 10,000/day.
- **Endpoint**: `https://api.sam.gov/prod/opportunities/v2/search`
- **Notes**: Full entity registration can take 2-3 weeks. DUNS number replaced by UEI (Unique Entity ID).
- **URL**: https://open.gsa.gov/api/entity-api/

### IRS Business Master File
- **What it provides**: ~1.97M tax-exempt organizations. EIN, name, address, subsection code (501c3, etc.), asset/income codes, filing requirement, tax period.
- **Availability**: Free monthly CSV downloads by state/region on IRS.gov.
- **API**: No official REST API. Bulk CSV download only.
- **Limitation**: Only exempt orgs (nonprofits). For-profit businesses NOT included. Churches and self-declared orgs excluded.
- **URL**: https://www.irs.gov/charities-non-profits/exempt-organizations-business-master-file-extract-eo-bmf

### SBA (Small Business Administration)
- **What it provides**: Small business size standards (by NAICS), disaster loan data, 7(a) & 504 loan data, PPP FOIA data, state/metro small business statistics, resource partner locations (SBDC, SCORE).
- **Cost**: Free. ~42 datasets on data.sba.gov.
- **Legacy API**: `api.sba.gov` existed for licenses/permits lookup but appears stale. Current datasets mostly XLSX/CSV downloads via CKAN, some API endpoints.
- **Notable**: SBA Size Standards API gives NAICS-based small business classification thresholds (useful for lead qualification).
- **URL**: https://data.sba.gov/

### US Census Bureau Business API
- **What it provides**: County Business Patterns (CBP), Nonemployer Statistics, Economic Census, International Trade (exports by NAICS), population estimates, construction spending.
- **Cost**: Free. API key required (register at api.census.gov).
- **Key endpoints**:
  - County Business Patterns: `https://api.census.gov/data/<year>/cbp` - establishments, employment, payroll by NAICS + geography. No API key needed for basic queries.
  - International Trade by NAICS: `https://api.census.gov/data/timeseries/intltrade/exports/naics`
- **Notes**: Disclosure avoidance means some cells suppressed (return 0). ZIP-level queries can return large results. Best for market-sizing and territory analysis.
- **Alternatives**: BLS QCEW (Quarterly Census of Employment and Wages) also free — employment/wage data by NAICS at county level.
- **URLs**: https://api.census.gov/data/key_signup.html / https://www.census.gov/data/developers/data-sets.html

### OpenCorporates / Secretary of State Business Registrations
- **What it provides**: Company name, registration number, jurisdiction, incorporation/dissolution dates, company type, officers/directors, filings. 200M+ companies across 170+ jurisdictions.
- **Pricing**: Free tier with API key (rate-limited). Paid plans start at **£2,250/year** (Essentials: 500 calls/month). Starter: £6,600/year (2,500 calls/mo). Basic: £12,000/year (5,000 calls/mo).
- **US Coverage**: ~30 US states via direct feeds. Not all SOS databases are covered.
- **Alternative approach**: Scrape state SOS databases individually. Many states offer free bulk CSV downloads (Delaware, California, New York, etc.) or cheap API access.
- **URL**: https://opencorporates.com/pricing/

### Dun & Bradstreet Alternatives
- **D&B** is the market leader but expensive (~$35K/year reported). Acquired by Clearlake Capital in 2025.
- **Key alternatives**:
  - **ZoomInfo**: ~$15K+/year. 320M+ contacts. Strong US coverage. 15%+ bounce rate reported.
  - **Clearbit**: ~$99-499/mo. 40M+ companies. Real-time enrichment API. API-first.
  - **UpLead**: ~$99/mo. 95% accuracy guarantee. Real-time email verification at export.
  - **Apollo.io**: Free tier available. 275M+ contacts. Built-in engagement tools.
  - **Cognism**: ~$5K+/year. Strong EMEA coverage (GDPR compliant). Diamond Data phone-verified.
  - **SalesIntel**: Human-verified records. High accuracy on covered contacts.
  - **Coresignal**: ~$0.01/API call. Public web data aggregation (LinkedIn, Crunchbase, etc.).
  - **Zephira.ai**: Starting at $0.03/API call. Registry-sourced, pay-as-you-go.
  - **LeadIQ**: ~$36/seat/mo. LinkedIn prospecting + verification.

### Better Business Bureau API
- **Official API**: BBB does NOT offer a free official public API. They have an accreditation application API for partners.
- **Unofficial/Third-party**: RapidAPI has a "BBB - Better Business Bureau API" ($0 free tier, $25/mo Pro, $100/mo Ultra, $500/mo Mega). Also Apify scraper actors (pay-per-use).
- **Scraping approach**: BBB.org embeds JSON-LD schema data on every profile page (name, address, phone, rating, accreditation status, complaint count, years in business, owner names). Can be extracted without heavy anti-scraping countermeasures.
- **Data available**: Business name, rating (A+ through F), accreditation status, complaint count/history, address, phone, website, year founded, owner/officer names.
- **URL**: https://www.bbb.org/

---

## 2. Residential Proxies

### How they unblock restricted sources
YP/Yelp/Hotfrog use Cloudflare/PerimeterX which detects datacenter IPs and Tor exit nodes. Residential proxies route through real ISP-assigned home IPs (invisible to bot detection).

### Provider Comparison (2026 Pricing)

| Provider | Starting Price | Entry Plan | At 100GB/mo | Best For |
|---|---|---|---|---|
| **Smartproxy/Decodo** | $2.00/GB | $11.25/mo (3GB) | $275/mo ($2.75/GB) | Best value SMB |
| **Oxylabs** | $2.50/GB | $30/mo (5GB) | ~Custom ($5/GB tier) | Enterprise reliability |
| **Bright Data** | $2.50/GB* | $499/mo (141GB)* | $499/mo ($3.50/GB)* | Largest IP pool |
| **SOAX** | $2.00/GB | ~$10/mo (5GB) | ~$200/mo | EU-focused |
| **IPRoyal** | $4.55/GB | Pay-as-you-go | $4.55/GB | No commitment needed |
| **GProxy** | $0.49/GB | Pay-as-you-go | $49/mo | Budget (smaller pool) |

_* Bright Data has a current 50% promo. Prices change frequently._

- **Smartproxy (now Decodo)**: Best overall value for 10-500GB/mo. 55M+ ethically-sourced IPs. 99.68% success rate. <0.5s response. Country/city/ZIP targeting. No contract.
- **Oxylabs**: 175M+ IPs, 195+ countries. Enterprise SLA. SOC1/SOC2 compliant. Better for large-scale operations needing reliability guarantees.
- **Bright Data**: 72M+ residential IPs + 7M mobile. Most advanced tooling (Scraping Browser, Dataset Marketplace). Steeper learning curve.
- **Practical advice**: For a lead gen agency scraping US Yelp/YP/Hotfrog, start with Decodo (Smartproxy) at 100GB/mo ($275). If IP blocks persist, escalate to Oxylabs enterprise tier with dedicated support.

### Monthly volume estimation for lead gen
- Google Maps search (100 queries) = ~5-15MB
- Yelp list (200 businesses) = ~10-30MB  
- Hotfrog/YellowPages list (200 businesses) = ~5-20MB
- Website crawl per business (1-2 pages) = ~100-500KB each
- Total for 200 delivered leads with enrichment: **~1-3GB/month**

**Verdict**: Even Smartproxy's $35/mo (10GB) plan covers a lot of lead gen volume.

---

## 3. Phone/Email Verification Services

### Email Verification

| Service | Starting Price | Per-Email Cost (bulk) | Accuracy Claim | Notes |
|---|---|---|---|---|
| **NeverBounce** | $0.008/email (1K) | $0.002 (1M credits) | ~99% | ZoomInfo subsidiary. Credits never expire. API + sync plans available. |
| **ZeroBounce** | $0.0195/email (2K) | $0.0032 (1M) | Up to 99.6% | Money-back guarantee. Free 100/mo. $99/mo subscription. |
| **BriteVerify** | ~$0.01/email | $0.004 (volume) | ~99% | Acquired by Validity. Good API. |
| **Kickbox** | $0.01/email | $0.003 (volume) | ~99.5% | Free 100/mo. Strong fraud detection. |
| **Abstract API** | $0.001/email | $0.0005 (volume) | ~99% | Cheapest option at scale. |
| **mailfloss** | $29/mo (10K) | $0.0029 | ~99% | "Set-and-forget" automation. |

- **Recommendation for B2B lead gen**: ZeroBounce or NeverBounce. Both have real-time API + bulk upload + good accuracy. Budget $100-500/mo depending on volume.
- **Important**: Pre-verification at point-of-capture (webhook/API on form submit) reduces list decay.
- **All verification services** use SMTP handshake + pattern analysis. None can guarantee 100% (some servers accept-all).

### Phone Verification

| Service | Starting Price | Per-Lookup Cost | Data Provided |
|---|---|---|---|
| **Twilio Lookup** | $0.01/request (Basic) | $0.01-0.05 (line type, carrier, CNAM, caller name) | Line type, carrier, country, formatting |
| **Trestle (CoreLogic)** | $0.015/query | Custom enterprise pricing (or $0.015 self-serve) | Validation, line type, carrier, DNC status, prepaid flag |
| **Abstract Phone Validation** | $0.001/request | $0.0005 (volume) | Line type, carrier, location |
| **Numverify** | $0.015/request | $0.002 (volume) | Line type, carrier, location |
| **Searchbug** | $0.05/lookup | Volume discounts | CNAM, line type, carrier |

- **Twilio Lookup V2** packages: Basic (free, formatting/validation), Line Type Intelligence ($0.01), Caller Name (CNAM, $0.01), SIM Swap ($0.02), Reassigned Number ($0.02).
- **Recommendation**: For B2B lead gen, Twilio Lookup is the easiest to integrate (already might have Twilio account). Trestle offers richer data (DNC checks, litigator checks). Abstract is cheapest for simple validation.

---

## 4. Professional Dialer Systems

### Components of a Power/Preview Dialer

A professional dialer needs:

1. **Telephony Infrastructure** — SIP trunking or CPaaS (Twilio, Telnyx, AWS Connect, RingCentral)
2. **Dialer Logic** — Power dialer (dial next when agent available), Preview dialer (show lead info first), Predictive dialer (AI-calculates dial timing)
3. **Call Routing & Queuing** — Skills-based routing, ACD, wait queues
4. **WebRTC Softphone** — Browser-based dialing (no physical phone)
5. **CRM Integration** — Screen pop, disposition codes, call logging, note-taking
6. **Call Recording** — Compliance (TCPA, state laws), quality assurance
7. **Reporting** — Agent stats, connect rate, talk time, conversion
8. **Compliance** — DNC list scrubbing, TCPA consent, time-zone restrictions, 3% abandonment rate (predictive)

### Twilio Voice / Flex

- **Twilio Flex** (full contact center): $1/active user hour or $150/seat/mo (named user). 5,000 free hours trial.
- **Twilio Programmable Voice** (build your own): $0.014/min outbound, $0.0085/min inbound. US phone numbers ~$1/mo.
- **Twilio Voice JS SDK**: `@twilio/voice-sdk` — browser WebRTC softphone. npm package. Handles audio streams, mute, DTMF, call state.
- **Additional costs**: Agent Copilot AI ($0.035/min voice, $0.005/message). TaskRouter ($0.06/task). Studio flow builder.
- **Power dialer build**: You'd build the auto-dial logic on top of Twilio's REST API + Voice SDK. No built-in power/preview dialer in base Voice SDK — that's custom logic.

### Alternatives

- **Telnyx**: ~$0.005-0.007/min. Full Voice API + WebRTC SDK. 80% cheaper than Twilio for voice. Better migration docs from Twilio. 
- **AWS Connect**: $0.018/min + $0.25/contact. Built-in power dialer (outbound campaigns). Integrated with AWS ecosystem.
- **RingCentral**: $20-60/seat/mo. Full-featured, hosted. Less customizable than Twilio.
- **Five9 / NICE CXone**: Enterprise. $100-200/seat/mo. Predictive dialer built-in.

### WebRTC Browser Dialer Architecture

```
Browser (JS SDK) ←→ Twilio Voice ←→ PSTN (phone numbers)
    ↕                    ↕
 Microphone/speaker     Webhook → Your Backend (TwiML)
```

- **Open source**: There are open-source Twilio WebRTC dialers (GitHub: `bulkvs-webrtc-dialer`, etc.) that can serve as starting points.
- **Disposition codes**: Customizable per campaign. Common: Converted, Follow-up, Not Interested, Wrong Number, No Answer, Busy, Voicemail, DNC.
- **Call recording**: Twilio stores as WAV/MP3; costs $0.0025/min for storage + retrieval.
- **Compliance critical**: TCPA requires prior express consent for autodialed calls. Preview dialer (human initiates each call) is safer than predictive/power (auto-dial) in regulated industries.

### Recommendation for B2B Lead Gen Agency

Build on **Twilio Programmable Voice + Voice JS SDK** (not Flex) if you want maximum control. Use Telnyx if voice cost is the priority. Buy a white-label dialer (Kixie, Aircall, PhoneBurner) if you want off-the-shelf.

---

## 5. Construction Industry Data Sources

### Paid Sources (Best Quality)

#### BuildZoom
- **Data**: 350M+ building permits, 25+ years history, 90% US population coverage. 6M+ licensed contractors. 70K new permits/day + 500K status updates daily.
- **Products**: Bulk Data (weekly/monthly exports), Data API (on-demand), Data Explorer (web app), Permit Map (visual).
- **Coverage**: 2,400 jurisdictions. Emphasis on largest markets.
- **Pricing**: Custom (contact sales). Not publicly listed.
- **URL**: https://www.buildzoomdata.com/

#### Dodge Construction Network (formerly Dodge Data & Analytics)
- **Data**: 636K+ projects tracked annually. 55+ years of data. Preferred data provider of US Census Bureau. Covers from first planning permit through construction.
- **Products**: API (REST, JSON, OAuth2.0), Data Feeds, Dodge One platform, CRM integrations (Salesforce, HubSpot, MS Dynamics).
- **Features**: Human-verified projects (500+ field researchers). Project stage tracking, specs, plans, bid docs, contact info for decision-makers.
- **Pricing**: Custom (expensive, enterprise-tier). Demo required.
- **URL**: https://www.construction.com/apis/

#### Construction Monitor
- **Data**: Building permits nationwide. 3M+ current leads. Updated daily. REST API available.
- **Products**: Weekly Editions, Powersearch (web search), Top Company Reports, REST API, FTP data dumps, Real-time leads, Permit mapping.
- **API**: Elasticsearch-based, JSON, authentication key. Documented at api.constructionmonitor.com.
- **Pricing**: Not public. Contact for quote.
- **URL**: https://www.constructionmonitor.com/data

### Free / Public Sources

#### State Contractor License Databases
- **Every state** has a public lookup for contractor licenses (handled by different agencies — CSLB in CA, WVCLB in WV, etc.).
- **What's available**: License number, status (active/expired/suspended), classification, bond info, complaint history, expiration date.
- **Format**: Most are web forms, not APIs. Would need scraping or per-state integration.
- **Challenge**: 50 different systems, different schemas, different update frequencies. High engineering cost to normalize.
- **Examples**:
  - California CSLB: https://www.cslb.ca.gov/onlineservices/checklicenseII/checklicense.aspx
  - Louisiana LSLBC: https://arlspublic.lslbc.louisiana.gov/
  - Texas TDLR: https://www.tdlr.texas.gov/

#### Building Permit Data (Free/Low-Cost)
- Many municipalities publish permit data via open data portals (Socrata, CKAN). Requires per-city integration.
- National aggregators like **BuildingEye** and **MangoTech** scrape and sell but offer limited free access.

### Construction Data vs Lead Gen Context

For a B2B lead gen agency targeting construction firms, the most efficient approach is:
1. Scrape **Google Maps** for contractors (free, works now)
2. Enrich via **state license databases** (free, but 50x integration effort)
3. Cross-reference with **BBB** for ratings/years in business
4. For premium: **BuildZoom Data API** or **Dodge API**

---

## 6. Data Accuracy Benchmarks

### Email Deliverability Rates for Scraped Data

| Data Source | Typical Bounce Rate | Agency-Grade Target |
|---|---|---|
| Manual-scraped (Maps, directories) | 15-35% | Should verify before use |
| B2B data provider (ZoomInfo, Apollo) | 15-30% on raw data | |
| B2B data provider (verified exports) | 3-10% | |
| After email verification service | 1-5% | **<3% is agency-grade** |
| With real-time verification API | <2% | Ideal for transactional |

- **Email decay rate**: B2B data decays 2-3% per month. After 12 months, 22.5-70.3% of records have decayed (industry dependent).
- **2026 cold email benchmarks**: Average open rate 28-45%, average bounce rate 2%, top-10% bounce rate <0.8%.
- **Key insight**: Scraped emails WITHOUT verification should NEVER be used for cold campaigns. Always pass through NeverBounce/ZeroBounce first.
- **SMTP verification** is the gold standard — does a real handshake with the receiving mail server. Adds $0.002-0.008/email to cost but essential for deliverability.

### Phone Number Accuracy

| Data Source | Connect Rate | Notes |
|---|---|---|
| Scraped from directories | 50-70% | Often disconnected or wrong |
| After Twilio Lookup validation | 70-85% | Filters disconnected, invalid formats |
| After DNC + live call verification | 85-95% | Most expensive but best |
| **Agency-grade target** | **>80% connect rate** | Post-verification |

- **Phone verification services** (Twilio Lookup, Trestle) can confirm line type (mobile/landline/VoIP) and whether the number is in service, but CANNOT 100% confirm it reaches the right business.
- **Best practice**: Twilio Lookup → filter disconnected/not-in-service → score by line type (mobile > landline). Then monitor call connect rates by source.

### What Constitutes "Agency-Grade" Lead Data

| Metric | Acceptable | Good | Premium |
|---|---|---|---|
| Email bounce rate | <5% | <3% | <1% |
| Phone connect rate | >65% | >75% | >85% |
| Data completeness (all fields) | >60% | >80% | >95% |
| Accuracy (name/title correct) | >80% | >90% | >98% |
| Freshness (last verified) | <6 months | <3 months | <30 days |
| NAICS/industry correct | >70% | >85% | >95% |

**For a B2B lead gen agency selling to clients**:
- **Minimum viable**: All data verified at least once. Bounce rate <5%. Connect rate >65%. Price ~$50-200/mo for 500 leads.
- **Premium product**: Real-time verified at point-of-export. Bounce rate <2%. Connect rate >80%. Full firmographics + tech stack + decision-maker names. Price $500-2,000/mo.
- **White-glove**: Human-validated contacts. On-demand refresh. Direct-dial phone numbers. Price $5,000+/mo.
