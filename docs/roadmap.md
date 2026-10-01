# Lead-Generator: Agency-Grade Roadmap (Zero-Cost)

> End-to-end plan to evolve from a working prototype into a professional B2B
> lead generation system — with zero paid services. Everything is free (Tor,
> government APIs, open-source tools, local LLM). Quality comes through
> better algorithms, multi-source corroboration, and free data sources.

---

## Table of Contents

1. [Current-State Assessment](#1-current-state-assessment)
2. [Accuracy & Field Enhancement](#2-accuracy--field-enhancement)
3. [Multi-Source Integration Plan (Free)](#3-multi-source-integration-plan-free)
4. [Zero-Cost Verification Pipeline](#4-zero-cost-verification-pipeline)
5. [Dialer & Outreach System](#5-dialer--outreach-system)
6. [Infrastructure & Operations](#6-infrastructure--operations)
7. [Phased Timeline](#7-phased-timeline)
8. [Cost: $0/mo](#8-cost-0mo)

---

## 1. Current-State Assessment

### 1.1 What Works Today

| Component | Status | Cost |
|-----------|--------|------|
| Google Maps scraping | ✅ Working | Free (Tor) |
| NPI registry (medical) | ✅ Working | Free |
| Hotfrog scraper | ⚠️ 403 over Tor | Free (needs workaround) |
| YellowPages/Yelp | 🔴 Off (Cloudflare) | Free (needs workaround) |
| Website detection | ✅ Working | Free (httpx) |
| Contact finding | ✅ Working | Free |
| Business intel (CMS/SSL/marketing) | ✅ Working | Free |
| Scoring/rubric | ✅ Working | Free |
| LLM pitch research | ✅ Working | Free (Ollama local) |
| XLSX/PDF export | ✅ Working | Free |

### 1.2 Critical Gaps (Free Fixes)

| Gap | Impact | Free Solution |
|-----|--------|---------------|
| No residential proxies | Hotfrog/YP/Yelp blocked | Rotate more Tor IPs; use free proxy lists for low-risk sources; enhance direct-connection sources |
| No email verification | 15-35% bounce | MX record DNS check + SMTP handshake (self-hosted) + format/pattern validation |
| No phone verification | 30-50% wrong numbers | libphonenumber validation + number pattern analysis + multi-source cross-check |
| No government data APIs | Missing entity verification | SAM.gov (free 1000/day), Census Bureau (free, no key), state SOS databases (free) |
| No CRM/dialer | Leads sit in spreadsheets | Free WebRTC dialer via Twilio trial + open-source CRM (Twenty/Espo); or manual spreadsheet workflow |
| Single Tor identity | Many sites block | 5+ Tor instances with shorter circuit rotation (already scripted) |
| SQLite under Celery | Write contention | Enable WAL mode + busy_timeout (config change, free) |

### 1.3 Philosophy: Zero-Cost Quality

We achieve quality through **algorithmic rigor and free data**, not paid APIs:

```
More sources × better dedup × smarter scoring = quality leads
                      ⤷ no paid verification needed
```

Every paid service has a free alternative:
| Paid Service | Free Alternative |
|-------------|------------------|
| Smartproxy ($35/mo) | More Tor instances + free proxy rotator + direct API sources |
| NeverBounce ($80/mo) | Built-in MX + SMTP verification + format heuristics |
| Twilio Lookup ($100/mo) | libphonenumber + carrier pattern DB + cross-source phone match |
| Twilio Voice ($7/mo) | Free WebRTC (Twillo trial $15 credit) or manual call list |
| PostgreSQL RDS ($15/mo) | SQLite with WAL mode (or local PostgreSQL) |
| Dun & Bradstreet | SAM.gov + Census + state SOS — all free |
| OpenCorporates (£2250/yr) | State SOS direct scraper (free per-state) |

---

## 2. Accuracy & Field Enhancement

### 2.1 Current Fields vs Target (Free Sources Only)

| Field | Current | Target | Free Source |
|-------|---------|--------|-------------|
| Business name | ✅ Scraped | ✅ Same | — |
| Phone | ✅ Scraped | ✅ Cross-source verified | Maps + Hotfrog + YP + website |
| Email | ✅ Scraped | ✅ Verified | MX DNS check + SMTP probe |
| Website | ✅ Scraped | ✅ Same | — |
| Address | ✅ Scraped | ✅ Geocoded (free Nominatim) | OpenStreetMap/Nominatim |
| NAICS Code | ❌ Missing | ✅ From SAM/Census | SAM.gov API (free) |
| License Status | ❌ Missing | ✅ State contractor board | Free per-state scrape |
| Entity Type | ❌ Missing | ✅ SAM.gov | Free API |
| Employee Count | ✅ Regex | ✅ Schema.org + multiple signals | Website scrape |
| Year Founded | ✅ Regex/header | ✅ WHOIS + header + SAM | WHOIS RDAP (free) |
| Decision-Maker | ✅ Owner name | ✅ + title from website | Website text extraction |
| BBB Rating | ❌ Missing | ✅ JSON-LD extraction | BBB profile scrape |

### 2.2 Zero-Cost Verification Pipeline

```
Scrape → Merge → Enrich → VERIFY (free) → Score → LLM → Export

Free verification steps:
  ⤷ email: MX DNS query + SMTP banner check + format validation
  ⤷ phone: libphonenumber format + pattern analysis
  ⤷ entity: SAM.gov API + state SOS lookup
  ⤷ address: Nominatim geocode (free, 1 req/sec)
  ⤷ multi-source: same phone/email from 2+ sources = higher confidence
```

**Email verification** (`services/verifier.py`, free):
- MX record via `dns.resolver` (Python stdlib): domain has mail servers
- SMTP banner check via `smtplib` (no send): server responds
- Format validation via regex: syntactically valid
- Domain age via WHOIS RDAP: recently registered = higher risk
- No third-party API calls. All self-contained, zero cost.

**Phone verification** (`services/verifier.py`, free):
- `libphonenumber` (Google OSS) validation: format, region, type
- Multi-source cross-check: same number from Maps + Hotfrog + website
- Number pattern analysis: toll-free, premium-rate detection
- No Twilio. No cost.

**Entity verification** (`services/gov_api.py`, free):
- SAM.gov API: free key, 1,000 requests/day, entity lookup by name/zip
- State SOS: free web lookups per state
- Census Bureau: free, no key required
- All zero cost.

### 2.3 New Model Fields (Free Data)

```python
# Phase 1 - Verification fields (free)
email_mx_valid     = Column(Boolean)     # Domain has mail servers
email_smtp_valid   = Column(Boolean)     # SMTP banner received
email_format_valid = Column(Boolean)     # Regex-valid format
email_confidence   = Column(Float)       # 0-1 combined from all checks

phone_valid_format = Column(Boolean)     # libphonenumber valid
phone_type         = Column(String)      # MOBILE | LANDLINE | VOIP | UNKNOWN
phone_cross_sources = Column(Integer)    # How many sources had this number

# Phase 2 - Government data (free)
naics_code         = Column(String)      # 6-digit NAICS from SAM/Census
naics_description  = Column(String)
uei                = Column(String)      # SAM.gov entity ID (free)
entity_type        = Column(String)      # LLC | Corp | Sole Prop
incorporation_state = Column(String)
good_standing      = Column(Boolean)     # Active status
license_number     = Column(String)      # State contractor license
license_status     = Column(String)      # Active | Expired
license_class      = Column(String)      # A | B | C

# Phase 3 - Deeper enrichment (free)
domain_age         = Column(Integer)     # Years since WHOIS registration
domain_registrant  = Column(String)      # WHOIS registrant name
revenue_estimate   = Column(String)      # Range from size/industry heuristics
bbb_rating         = Column(String)      # BBB free scrape
```

---

## 3. Multi-Source Integration Plan (Free)

### 3.1 Source Priority (All Free)

| Source | Priority | Yield | Anti-Bot | Free Path |
|--------|----------|-------|----------|-----------|
| Google Maps | 🔴 P0 | High | Moderate | ✅ Tor (working) |
| SAM.gov | 🔴 P0 | Entity data | None | ✅ Free API key |
| Census Bureau | 🔴 P0 | Market data | None | ✅ No key needed |
| Hotfrog | 🔴 P0 | High | Moderate | ⚠️ Needs more Tor instances |
| State SOS DBs | 🟡 P1 | Entity verification | None | ✅ Free web lookup |
| State License DBs | 🟡 P1 | Construction data | Varies | ✅ Free scrape |
| NPI/NPPES | 🟡 P1 | Medical only | None | ✅ Working |
| BBB | 🟢 P2 | Reputation | None | ✅ Free scrape |
| Free Proxy Lists | 🟢 P2 | Low | Low | ✅ Public lists (unreliable) |

### 3.2 Tor Multi-IP Strategy (Free)

We already have `scripts/start_tor_instances.sh`. Scale it:

```
Current: 3 instances (9050, 9150, 9250)
Target:  10 instances (9050-9950)
Effect:  10× exit IPs → 10× Google Maps rate limit headroom
         More IPs for Hotfrog rotation
```

**Config changes needed:**
```
TOR_SOCKS_PORTS=9050,9150,9250,9350,9450,9550,9650,9750,9850,9950
TOR_CONTROL_PORTS=9051,9151,9251,9351,9451,9551,9651,9751,9851,9951
IP_ROTATE_EVERY_N_REQUESTS=3
```

**Hotfrog with more Tor IPs:**
- Current: 3 IPs, Hotfrog quickly 403s all 3
- With 10 IPs: each IP handles ~5 pages before being rotated out
- Combined: 10 IPs × 5 pages × 12 listings = 600 listings before rotation
- Add 120s circuit rotation = IPs recycle every 2 minutes
- Estimated yield: 50-100 per query (better than 0 today)

### 3.3 Government APIs (Free, P0)

#### SAM.gov API
- Sign up: https://open.gsa.gov/api/
- **Free**: 1,000 requests/day (basic), with entity registration
- **Data**: Unique Entity ID (UEI), CAGE code, business type, NAICS, entity status
- **Integration**: `backend/services/gov_api.py::lookup_entity(name, zip)`
- **Value**: Verifies business exists, provides NAICS code, confirms active status

#### Census Bureau Business Patterns API
- **Free**: No API key required, unlimited
- **Data**: Establishments, employment, payroll by NAICS × state/county
- **Integration**: `backend/services/market_intel.py::market_size(naics, zip)`
- **Value**: Market sizing before running a job; informs pool factor

#### State SOS Entity Search
- **Free**: Each state has a free business entity search
- **Integration**: Per-state scraper (start with top 5: DE, CA, TX, FL, NY)
- **Data**: Incorporation status, filing date, good standing
- **Value**: Confirms business is legitimate and active

### 3.4 Hotfrog Unblock (Free, P0)

**Current**: 403 on every request over Tor.

**Free fix — Multi-instance Tor rotation:**
1. Increase to 10 Tor instances (up from 3)
2. Shorten circuit to 120s (already configured)
3. Route each Hotfrog page through a different exit IP
4. Add random delays (2-5s) between pages
5. Rotate user-agent per request (already partially done)

In `_gather_hotfrog()`:
```python
# Changed rotation: page-per-IP instead of N-pages-per-IP
rotate = True  # Rotate every single page
```

**Hotfrog yield target**: With 10 rotating IPs → 50-200 listings per query (vs 0).

### 3.5 YellowPages + Yelp (Free, Workaround)

Both are blocked by PerimeterX/Cloudflare which Tor cannot bypass. Free approaches:

**Path A — Google cache / cached pages:**
- Use `webcache.googleusercontent.com` to fetch cached YP/Yelp pages
- Lower quality (may be stale) but free and not rate-limited
- Yield: ~20-40 cached listings per query

**Path B — Accept the gap:**
- Google Maps + Hotfrog + SAM.gov + NPI already cover most niches
- YP/Yelp are supplementary — without residential proxies this is a known limitation
- Focus on making the other sources excellent instead

---

## 4. Zero-Cost Verification Pipeline

### 4.1 Architecture (No Paid Services)

```
                  ┌──────────────────────────┐
                  │   Verified Lead Output    │
                  │   (score >= 40, checked)  │
                  └──────────┬───────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
  ┌─────────────┐     ┌─────────────┐     ┌──────────────┐
  │ Email Check  │     │ Phone Check │     │ Entity Check  │
  │ MX + SMTP   │     │ libphone +  │     │ SAM + SOS     │
  │ (Python std)│     │ cross-src   │     │ (free APIs)   │
  └──────┬──────┘     └──────┬──────┘     └──────┬───────┘
         │                   │                   │
         ▼                   ▼                   ▼
  ┌────────────────────────────────────────────────────────┐
  │                   Enriched Lead                         │
  │  email_status, phone_type, entity_status, NAICS, etc   │
  └─────────────────────┬──────────────────────────────────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
  ┌────────────┐ ┌────────────┐ ┌──────────────┐
  │Google Maps │ │  Hotfrog   │ │  SAM.gov +   │
  │  (Tor)     │ │  (Tor+rot) │ │  Census (dir)│
  └────────────┘ └────────────┘ └──────────────┘
```

### 4.2 Free Email Verification

```python
# backend/services/verifier.py
import dns.resolver
import smtplib
import re

def verify_email(email: str) -> dict:
    """Free email verification: MX check + SMTP banner + format."""
    result = {"format_valid": False, "mx_valid": False, "smtp_valid": False}

    # 1. Format (regex)
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        result["format_valid"] = True

    domain = email.split("@")[1]

    # 2. MX record
    try:
        mx_records = dns.resolver.resolve(domain, "MX")
        result["mx_valid"] = len(mx_records) > 0
    except Exception:
        result["mx_valid"] = False

    # 3. SMTP banner (connect, no send)
    if result["mx_valid"]:
        try:
            mx_host = str(mx_records[0].exchange)
            with smtplib.SMTP(mx_host, timeout=5) as smtp:
                code = smtp.ehlo()[0]
                result["smtp_valid"] = code == 250
        except Exception:
            result["smtp_valid"] = False

    return result
```

**Cost: $0.** Uses Python standard library (`dns.resolver` needs `dnspython` package).

### 4.3 Free Phone Verification

```python
# backend/services/verifier.py
import phonenumbers

def verify_phone(phone: str, region: str = "US") -> dict:
    """Free phone verification via libphonenumber + heuristic checks."""
    result = {"valid": False, "type": "unknown", "possible": False}

    try:
        parsed = phonenumbers.parse(phone, region)
        result["valid"] = phonenumbers.is_valid_number(parsed)
        result["possible"] = phonenumbers.is_possible_number(parsed)

        num_type = phonenumbers.number_type(parsed)
        type_map = {
            0: "fixed_line", 1: "mobile", 2: "fixed_line_or_mobile",
            3: "toll_free", 4: "premium_rate", 5: "shared_cost",
            6: "voip", 7: "personal_number", 8: "pager",
            9: "uan", 10: "voicemail",
        }
        result["type"] = type_map.get(num_type, "unknown")
    except Exception:
        pass

    return result
```

**Cost: $0.** `pip install phonenumbers` (Google open source).

### 4.4 Free Entity Verification

```python
# backend/services/gov_api.py
import httpx

async def lookup_sam(name: str, zip_code: str = "") -> dict:
    """Free SAM.gov API entity lookup."""
    url = "https://api.sam.gov/entity-information/v3/entities"
    params = {
        "api_key": SAM_API_KEY,  # Free key from open.gsa.gov
        "q": name,
        "zip": zip_code,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        return resp.json()
```

**Cost: $0.** Free API key from SAM.gov.

### 4.5 Accuracy Targets (Zero-Cost)

| Metric | Current | Target | How (Free) |
|--------|---------|--------|------------|
| Email bounce rate | 15-35% | <8% | MX + SMTP + format (no paid API) |
| Phone valid rate | 50-70% | >75% | libphonenumber + cross-source check |
| Entity verified | Unknown | >85% | SAM.gov (free) + SOS lookup |
| Duplicate rate | <5% | <3% | Better fuzzy dedup |
| Multi-source corroboration | 1.5 avg | >2.5 avg | More Tor instances |

---

## 5. Dialer & Outreach System

### 5.1 Architecture (Free Options)

Three tiers, zero to low cost:

**Tier 1 — Spreadsheet + Manual (Free, Immediate):**
- Export XLSX is already produced per job
- Sales rep imports into Google Sheets
- Tracks outreach manually
- Cost: $0

**Tier 2 — Open-Source CRM (Free, Phase 3):**
- Twenty CRM (https://twenty.com, open-source MIT)
- Or EspoCRM (open-source, GPL)
- Self-host on same machine
- Import leads via API or CSV
- Track calls, emails, meetings
- Cost: $0

**Tier 3 — Browser-Based Click-to-Call (Free Tier, Phase 3-4):**
- Twilio free trial credit ($15)
- WebRTC click-to-call from frontend
- Or use free SIP provider (VoIP.ms minimal cost)
- Or Google Voice (free US calls)
- Cost: ~$0-$5/mo

### 5.2 Niche-Specific Script Templates (Free)

These are text templates — no cost, just content strategy.

| Niche | Opener Angle | Pain Hook | Value Prop |
|-------|-------------|-----------|------------|
| General Contractor | "Noticed your {city} projects" | "Permitting delays eating margins?" | "We handle license compliance + permit expediting" |
| HVAC | "Saw you serve {counties}" | "Seasonal slowdown in winter?" | "Maintenance contract outreach system" |
| Roofer | "Noticed {review_weakness} reviews" | "Insurance claim work too unpredictable?" | "Referral reactivation campaign" |
| Plumber | "Saw you've been in {city} since {year}" | "After-hours calls overwhelming?" | "Automated dispatch + follow-up" |
| Electrician | "Impressed by {projects}" | "Big jobs tying up crew for weeks?" | "Small-job retainer for cashflow" |
| Restaurant | "Noticed you're on {delivery_platform}" | "Commission bleed from DoorDash/Uber?" | "Direct ordering + loyalty program" |
| Hospitality | "Saw your {property} listed" | "OTA commissions eating 20%?" | "Direct booking engine" |

### 5.3 Script Engine (Free, Phase 3)

```python
# backend/services/script_engine.py
class ScriptEngine:
    """Generate per-lead outreach scripts from free data fields."""

    NICHE_PROFILES = {
        "construction": {
            "pain_hooks": [
                "permitting delays",
                "material cost volatility",
                "labor shortage",
                "bid win rate",
            ],
            "value_props": [
                "automated permit tracking",
                "supplier price comparison",
                "crew scheduling optimization",
            ],
        },
        # ... more niches
    }

    def generate(self, lead) -> dict:
        profile = self._match_profile(lead.business_category)
        return {
            "opener": f"Hi {lead.owner_name or 'there'}, "
                      f"I saw {lead.name} has been in {lead.address} since "
                      f"{lead.year_founded or 'a while'}...",
            "pain_hook": self._pick(profile["pain_hooks"]),
            "value_prop": self._pick(profile["value_props"]),
            "close": "Free 10-minute consultation this Thursday?"
        }
```

---

## 6. Infrastructure & Operations

### 6.1 Current Infrastructure (All Free)

| Component | Current | Stays Free? |
|-----------|---------|-------------|
| Database | SQLite | ✅ WAL mode + busy_timeout |
| Queue | Redis | ✅ Local Redis (free) |
| Workers | Celery | ✅ Free |
| API | FastAPI | ✅ Free |
| Frontend | Next.js | ✅ Free |
| Storage | Local filesystem | ✅ Free |
| Monitoring | Celery logs | ✅ Free |
| LLM | Ollama (local) | ✅ Free |

### 6.2 Free Production Hardening

**SQLite WAL mode** (critical for Celery concurrency):
```python
# backend/database.py
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
```

**Free proxy rotation enhancement** (`scripts/start_tor_instances.sh`):
- Add 5 more Tor instances (10 total)
- Shorten circuit: `NewCircuitPeriod 60` and `MaxCircuitDirtiness 60`
- Each instance rotates exit IP every 60 seconds

**All other infrastructure stays as-is** — it's already free.

---

## 7. Phased Timeline (Free)

### Phase 0: Foundation — Right Now (Week 1)

| Task | Effort | Cost |
|------|--------|------|
| Scale Tor to 10 instances | 1h | $0 |
| Route Hotfrog through rotating Tor IPs | 2h | $0 |
| Wire free email verification (MX + SMTP) | 4h | $0 |
| Wire free phone verification (libphonenumber) | 2h | $0 |
| Add verification fields to Lead model | 1h | $0 |
| Add verification stage to pipeline | 4h | $0 |
| Enable SQLite WAL mode | 0.5h | $0 |
| **Total** | **~3 days** | **$0** |

### Phase 1: Government Data (Week 2-3)

| Task | Effort | Cost |
|------|--------|------|
| SAM.gov free API key + integration | 4h | $0 |
| Census Bureau market data integration | 3h | $0 |
| WHOIS RDAP domain age lookups | 3h | $0 |
| Add NAICS, UEI, entity fields to model | 1h | $0 |
| State SOS entity search — top 5 states | 15h | $0 |
| State contractor license DB — top 5 states | 20h | $0 |
| Update scoring with entity verification | 2h | $0 |
| BBB JSON-LD scraper | 2h | $0 |
| **Total** | **~6 days** | **$0** |

### Phase 2: Accuracy & Quality (Week 4-5)

| Task | Effort | Cost |
|------|--------|------|
| Free Nominatim geocoding | 3h | $0 |
| Cross-source fuzzy dedup improvements | 4h | $0 |
| Score calibration (weight tuning) | 4h | $0 |
| Free proxy list integration (supplementary) | 3h | $0 |
| Hotfrog yield optimization (IP rotation tuning) | 4h | $0 |
| Hotfrog +10 instances pagination deep-scan | 4h | $0 |
| Field coverage report per niche | 2h | $0 |
| Add Google Maps tile tuning (more directional variants) | 2h | $0 |
| **Total** | **~4 days** | **$0** |

### Phase 3: Dialer & Outreach (Week 6-7)

| Task | Effort | Cost |
|------|--------|------|
| Script engine (per-niche text templates) | 4h | $0 |
| TwentyCRM self-hosted setup + import pipeline | 6h | $0 (open-source) |
| Lead outreach stage tracking in DB | 3h | $0 |
| Export enriched CSV for CRM import | 2h | $0 |
| Call disposition schema + tracking | 3h | $0 |
| Simple lead queue web page (Next.js) | 8h | $0 |
| Google Voice/SMS integration (free) | 4h | $0 |
| **Total** | **~6 days** | **$0** |

### Phase 4: Scale & Polish (Week 8-10)

| Task | Effort | Cost |
|------|--------|------|
| State SOS — remaining 45 states (light) | 40h | $0 |
| State license DBs — next 10 states | 40h | $0 |
| Niche-specific tile optimization (10 niches) | 10h | $0 |
| Automated cross-source quality scoring | 6h | $0 |
| Frontend auth (simple JWT) | 6h | $0 |
| Job scheduler (cron for recurring runs) | 4h | $0 |
| Export enhancements (custom columns per niche) | 6h | $0 |
| Performance tuning (concurrency per source) | 4h | $0 |
| **Total** | **~14 days** | **$0** |

---

## 8. Cost: $0/mo

| Item | Paid Alternative | Our Free Alternative |
|------|-----------------|---------------------|
| Proxies | Smartproxy $35/mo | Tor 10 instances (free) |
| Email verification | NeverBounce $80/mo | MX + SMTP (Python stdlib) |
| Phone verification | Twilio Lookup $100/mo | libphonenumber + cross-source |
| Voice calls | Twilio $7/mo+ | Google Voice (free) + spreadsheet |
| Database | RDS $15/mo | SQLite WAL (free) |
| Monitoring | Sentry $26/mo | Celery logs (free) |
| LLM | GPT-4 API $200/mo | Ollama local (free) |
| CRM | Salesforce $150/seat | TwentyCRM self-hosted (free) |
| **Total** | **~$613/mo** | **$0/mo** |

### What We Accept as Trade-Off

| Limitation | Impact | Workaround |
|------------|--------|------------|
| No residential proxies | YP/Yelp blocked, Hotfrog flaky | Optimize Maps + NPI + Gov + Hotfrog (rotating) |
| Email verification via SMTP | Less reliable than paid API | Combine MX + SMTP + format — still catches 70% of bad emails |
| Phone verification heuristics | Can't detect disconnected | Cross-source match + libphonenumber validation |
| No premium business data | No D&B credit scores | SAM.gov entity data is authoritative (free) |
| Manual/CRM-based dialer | Slower than auto-dialer | Export CSV → import TwentyCRM → manual call |

---

## 9. Success Metrics (Free)

| Metric | Current | 1 Month | 3 Months |
|--------|---------|---------|----------|
| Email MX-valid rate | N/A (not checked) | >80% | >90% |
| Phone valid format rate | ~60% (estimated) | >75% | >85% |
| Entity match rate (SAM) | N/A | >60% | >80% |
| Hotfrog yield per query | 0 (403) | >30 | >80 |
| Multi-source leads (2+ sources) | ~30% | >50% | >70% |
| Lead score > 60 proportion | ~30% | >45% | >60% |
| Field fill rate (all fields) | ~35% | >50% | >70% |
| Pipeline runtime (200 leads) | ~60min | <45min | <30min |
| Monthly lead capacity | ~500 | ~1,500 | ~3,000 |
| Duplicate rate | <5% | <3% | <2% |
| Export lead count (of 200 target) | 50-180 | >120 avg | >150 avg |

---

## Appendix: Zero-Cost Toolchain

| Tool | Purpose | License | URL |
|------|---------|---------|-----|
| Python 3.11+ | Runtime | PSF | python.org |
| FastAPI | API framework | MIT | fastapi.tiangolo.com |
| Celery | Task queue | BSD | docs.celeryq.dev |
| Redis | Message broker | BSD | redis.io |
| SQLite | Database | Public domain | sqlite.org |
| Playwright | Browser automation | Apache 2.0 | playwright.dev |
| Ollama | Local LLM | MIT | ollama.ai |
| libphonenumber | Phone validation | Apache 2.0 | github.com/google/libphonenumber |
| dnspython | DNS queries | ISC | dnspython.org |
| httpx | HTTP client | BSD | httpx.readthedocs.io |
| Tor | Anonymization | BSD | torproject.org |
| Nominatim | Geocoding | GPL | nominatim.org |
| SAM.gov API | Entity data | Free | open.gsa.gov |
| Census API | Market data | Free | census.gov/data/developers |
| TwentyCRM | CRM | MIT | twenty.com |
| Next.js | Frontend | MIT | nextjs.org |
| ReportLab | PDF generation | BSD | reportlab.com |

---

## Quick-Start: Next 5 Actions

These are the 5 highest-impact things to do right now (all free, all in Week 1):

1. **Scale Tor to 10 instances** — Edit `scripts/start_tor_instances.sh` and `.env` to add ports 9350-9950
2. **Hotfrog rotation fix** — In `sources.py:_gather_hotfrog()`, rotate IP every page instead of every N pages
3. **Free email verification** — Write `backend/services/verifier.py` with MX + SMTP checks (dnspython, smtplib)
4. **Free phone verification** — Add libphonenumber validation pass to `pipeline.py` in `_stage_score`
5. **SQLite WAL mode** — Add pragma event listener in `backend/database.py` for write-concurrency safety

These 5 changes require 0 new paid dependencies and will immediately improve data quality.
