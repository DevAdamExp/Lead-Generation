"""
config.py — Centralised settings loaded from .env
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).parent.parent          # Lead-Generator/
EXPORTS_DIR = BASE_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    APP_TITLE: str = "Lead-Generator API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Database — SQLite by default, swap to postgresql+asyncpg://... for prod
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/leads.db"
    SYNC_DATABASE_URL: str = f"sqlite:///{BASE_DIR}/leads.db"  # used by Celery

    # Redis — broker + result backend for Celery, progress cache
    REDIS_URL: str = "redis://localhost:6379/0"

    # Scraping
    # Headless browser by default — no popup windows stealing focus.
    SCRAPER_HEADLESS: bool = True
    HOTFROG_REQUEST_DELAY: float = 1.5       # seconds between Hotfrog pages
    MAPS_REQUEST_DELAY_MIN: float = 2.0      # seconds min between Maps queries
    MAPS_REQUEST_DELAY_MAX: float = 4.0      # seconds max between Maps queries
    WEBSITE_CONCURRENCY: int = 8             # parallel website checks
    CONTACT_CONCURRENCY: int = 10            # parallel per-lead contact enrichment
    TILE_CONCURRENCY: int = 4                # parallel Google Maps query-variant tiles
    # Desired parallel browser contexts for Maps enrichment. Actual concurrency
    # is capped at runtime by the number of distinct Tor exit circuits available
    # (one context per IP), so this is a ceiling, safe to raise with more ports.
    MAPS_ENRICH_CONCURRENCY: int = 4
    # Parallel place-detail extractions inside ONE discovery tile. The feed gives
    # us N place URLs; opening them one at a time was the single largest cost in
    # the pipeline. Same per-context-circuit design as MAPS_ENRICH_CONCURRENCY, so
    # actual concurrency is capped at runtime by available Tor exit circuits.
    MAPS_DETAIL_CONCURRENCY: int = 4
    # Polite delay between a worker's Maps queries (seconds). Per-context IPs mean
    # each exit sees far fewer requests, so this can be lower than the old 2.0–3.5.
    # Calibration knob: raise if you see Google throttling/consent walls.
    MAPS_ENRICH_DELAY_MIN: float = 1.5
    MAPS_ENRICH_DELAY_MAX: float = 3.0

    # ── Verified-target pipeline ────────────────────────────────────────
    # `limit` on a job means this many VERIFIED leads (score >= VERIFY_MIN_SCORE).
    # The pipeline over-scrapes a candidate pool then enriches best-first in
    # batches until `limit` verified are collected (or the pool is exhausted).
    VERIFY_MIN_SCORE: int = 40               # score at/above which a lead is "verified"
    # Final export accuracy gate: a delivered lead must clear this data_confidence
    # AND have an independently-verified contact (phone/email) or multi-source+Maps
    # corroboration. Applied with an empty-set fallback so a job never delivers
    # nothing. 0.6 = the >95%-accurate posture; leads that miss it still ship via
    # the fallback but are labelled verification_tier="unverified" in the export.
    MIN_EXPORT_CONFIDENCE: float = 0.6
    POOL_FACTOR: float = 1.5                 # ponytail: main runtime lever — pool = limit * this.
                                             # Was 2.6 (~520 enriched/200 delivered, ~2h run); 1.5
                                             # cuts enrichment work. Raise if verify rate drops below ~67%.
    POOL_MAX: int = 800                      # hard cap on candidates scraped

    # Sources enabled by default. YellowPages & Yelp yield ~0 over Tor but cost
    # the most (Playwright browser-per-fetch + full timeouts) — off by default,
    # re-enable via env (e.g. ENABLED_SOURCES=google_maps,npi,hotfrog,yellowpages,yelp)
    # once residential proxies exist.
    ENABLED_SOURCES: str = "google_maps,npi,hotfrog"
    # Hard per-source deadline. A live job sat 22+ min inside Hotfrog on a
    # half-dead Tor circuit with no ceiling; one slow source must not stall the
    # job. Raise if a genuinely slow but productive source gets cut off.
    SOURCE_TIMEOUT_SECONDS: int = 240

    # ── Review depth (Google Maps reviews tab) — goal 4a ────────────────────
    # Scrapes real review text/histogram/recency/owner-responses for the export
    # slice only. Off by default (adds a Maps visit per delivered lead).
    ENABLE_REVIEW_SCRAPE: bool = False
    REVIEW_SCRAPE_MAX: int = 10              # worst-first reviews harvested per lead

    # ── Registration / legal-entity lookup — goal 4b ────────────────────────
    ENABLE_REGISTRATION_LOOKUP: bool = False
    SAM_API_KEY: str = ""                    # free key from sam.gov (1k req/day)
    OPENCORPORATES_API_KEY: str = ""         # optional; free tier works keyless but rate-limited

    # ── Deep-research LLM (per-lead pitch synthesis) ────────────────────────
    ENABLE_PITCH_RESEARCH: bool = False
    # LLM extraction fallback: when regex/BeautifulSoup leaves services /
    # owner_name / owner_title / employee_count / year_founded empty, ask the
    # model to read the page text we ALREADY fetched (shared per-lead cache, so
    # no extra HTTP). Runs before scoring so recovered fields earn their points.
    # Extracted values never raise data_confidence — that is reserved for
    # independently verified signals. Costs ~1 LLM call per lead WITH GAPS.
    ENABLE_LLM_EXTRACTION: bool = False
    # Provider: "nvidia" (NVIDIA NIM cloud, free, OpenAI-compatible) or "ollama"
    # (local). "auto" = nvidia when NVIDIA_API_KEY is set, else ollama.
    RESEARCH_PROVIDER: str = "auto"
    # NVIDIA NIM — free tier, 40 req/min, no expiry. Key from build.nvidia.com.
    NVIDIA_API_KEY: str = ""
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    # Fallback chain (first that answers wins). First-party flagship instruct
    # models first (they outlive NVIDIA fine-tunes like nemotron, which rotate
    # off the free tier more often): 70B for quality, 8B/7B as fast survivors.
    # Dead models are auto-skipped at runtime (see pitch_research._DEAD_MODELS);
    # re-verify the chain quarterly against build.nvidia.com.
    NVIDIA_MODELS: str = (
        "meta/llama-3.3-70b-instruct,"
        "meta/llama-3.1-8b-instruct,"
        "qwen/qwen2.5-7b-instruct"
    )
    # Concurrency for per-lead LLM calls. Keep <= the 40 req/min free budget
    # (each lead ~1 call; a delivered slice of 50 finishes in ~2 min at 8-wide).
    PITCH_CONCURRENCY: int = 6
    # Local Ollama fallback backend
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b-instruct-q4_K_M"
    ENRICH_BATCH_SIZE: int = 50              # leads enriched per batch before re-checking target

    # Export dir (overridable)
    EXPORTS_DIR: Path = EXPORTS_DIR

    # ── Proxy Rotation ──────────────────────────────────────────────────
    # ONE Tor daemon exposing several SocksPort lines (see Dockerfile torrc).
    # httpx isolates circuits by SOCKS auth on the first port; Playwright/Chromium
    # can't do SOCKS auth, so it isolates workers across these ports instead.
    TOR_SOCKS_PORTS: str = "9050,9150,9250,9350,9450,9550,9650,9750,9850,9950,10050,10150,10250,10350,10450,10550,10650,10750,10850,10950"
    # Control port for the daemon (NEWNYM on explicit block via report_block()).
    TOR_CONTROL_PORTS: str = "9051,9151,9251,9351,9451,9551,9651,9751,9851,9951,10051,10151,10251,10351,10451,10551,10651,10751,10851,10951"
    # Number of independent Tor daemons (each with own DataDirectory + circuits)
    TOR_NUM_DAEMONS: int = 10
    # Seconds before Tor builds new circuit (lower = faster IP churn)
    TOR_CIRCUIT_DIRTINESS: int = 60
    # Exit node country restriction (us = US only, empty = any)
    TOR_EXIT_COUNTRIES: str = "us"
    # Per-IP rate limit: max requests per exit IP per minute
    TOR_MAX_REQUESTS_PER_IP_PER_MINUTE: int = 15
    # External SOCKS5 proxies (optional, comma-separated URLs)
    # Format: socks5://user:pass@host:port  or  socks5://host:port
    EXTERNAL_SOCKS_PROXIES: str = ""
    # Minimum time between IP rotation requests (seconds)
    IP_ROTATION_COOLDOWN: int = 30
    # Auto-rotate IP every N requests (0 = disabled)
    IP_ROTATE_EVERY_N_REQUESTS: int = 0


settings = Settings()
