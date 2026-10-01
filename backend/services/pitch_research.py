# Setup: brew install ollama && ollama serve && ollama pull qwen2.5:7b-instruct-q4_K_M  (or the 3b variant if RAM-tight)
"""
services/pitch_research.py — per-lead pitch synthesis via a free local LLM (Ollama).

# ponytail: SYNTHESIS over already-scraped fields, NOT a web crawler. Upgrade path
# is richer scraped input (better description/services/review_weaknesses), not a
# bigger model. Off by default, batch, soft-fails if Ollama isn't running.
"""
import json
import logging
import re
import time
from threading import Lock

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"
DEFAULT_URL = "http://localhost:11434"

_SYSTEM = ("You are a precise B2B sales strategist. You reason only from the facts "
           "given and always answer with a single valid JSON object, nothing else.")


class _RateLimiter:
    """Token-bucket-ish min-interval gate so parallel workers stay under a
    requests-per-minute budget (NVIDIA free tier = 40 rpm)."""
    def __init__(self, rpm: int):
        self._min_interval = 60.0 / max(rpm, 1)
        self._lock = Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next - now
            self._next = max(now, self._next) + self._min_interval
        if sleep_for > 0:
            time.sleep(sleep_for)


# NVIDIA free tier is 40 rpm; leave headroom.
_NVIDIA_LIMITER = _RateLimiter(rpm=36)

# Models that returned a non-rate-limit failure (deprecated / removed / bad
# request) are memoized here and skipped for the rest of the run, so we don't
# burn a rate-limited call + timeout on a dead model for every one of N leads.
# A 429 does NOT mark a model dead — that's transient. Reset per process.
_DEAD_MODELS: set[str] = set()
_DEAD_LOCK = Lock()

# 4xx is an instant death sentence (model removed / bad request). Everything else
# that isn't a 429 gets STRIKES instead: a saturated free-tier model answers
# "503 ResourceExhausted: Worker local total request limit reached" only after
# burning the full 90s timeout, and since 503 is not 4xx it used to be retried for
# every single lead — ~90s each, forever. Two strikes and we skip it for the run.
_MODEL_STRIKES: dict[str, int] = {}
_STRIKE_LIMIT = 2


def _call_nvidia(prompt: str, api_key: str, base_url: str, models: list[str],
                 system: str | None = None, max_tokens: int = 1100) -> str:
    """OpenAI-compatible chat call to NVIDIA NIM, trying each model in the chain
    until one answers. Returns the raw response string (expected to be JSON).

    Models that hard-fail (not a 429) are remembered in _DEAD_MODELS and skipped
    on subsequent leads — the first lead pays to discover a dead model, the rest
    go straight to the first live one."""
    # Tolerate a key pasted with a leading "Bearer " (avoids "Bearer Bearer …").
    api_key = api_key.strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()

    live = [m for m in models if m not in _DEAD_MODELS]
    # If every model is marked dead (e.g. a total outage), fall back to trying the
    # full chain again rather than giving up outright.
    if not live:
        live = list(models)

    last_err = None
    for model in live:
        _NVIDIA_LIMITER.wait()
        try:
            resp = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system or _SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    # 700 truncated ~3% of leads mid-JSON (verbose pain_points).
                    # _loads_lenient can now salvage those, but headroom is cheaper.
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                },
                timeout=90.0,
            )
            if resp.status_code == 429:  # rate limited — brief backoff, retry chain
                time.sleep(2.0)
                last_err = "429"
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 — fall through to next model
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and status != 429 and 400 <= status < 500:
                # 404 unknown model / 400 bad request — it's gone. Kill immediately.
                with _DEAD_LOCK:
                    _DEAD_MODELS.add(model)
                logger.info("NVIDIA model %s marked dead (HTTP %s) — skipping henceforth",
                            model, status)
            elif status != 429:
                # 503 ResourceExhausted, timeouts, connection errors: possibly
                # transient, but each one costs a full timeout. Strike it out.
                with _DEAD_LOCK:
                    n = _MODEL_STRIKES[model] = _MODEL_STRIKES.get(model, 0) + 1
                    dead = n >= _STRIKE_LIMIT
                    if dead:
                        _DEAD_MODELS.add(model)
                logger.info("NVIDIA model %s failed (%s) — strike %d/%d%s",
                            model, str(e)[:90], n, _STRIKE_LIMIT,
                            "; skipping henceforth" if dead else ", trying next")
            else:
                logger.info("NVIDIA model %s rate-limited (429), trying next", model)
    raise RuntimeError(f"all NVIDIA models failed: {last_err}")


# Models decorate prose even when asked for plain strings: "**Trust Gap**: no SSL"
# reaches the XLSX/PDF as literal asterisks. A live run over 187 leads produced
# markdown in 99 of them. Strip it at parse time so every consumer gets clean text.
_MD_BOLD_RE = re.compile(r"\*\*|__|`")
_MD_HEAD_RE = re.compile(r"^\s*#{1,6}\s+", re.M)
# Reasoning the model leaked into the answer instead of committing to a value:
# "clients in [LOCATION, assumed from 'Local business (US)' for context] are…".
# Rare (2/187) but unusable verbatim, so the caller drops the field rather than
# shipping it.
#
# Deliberately NARROW: match an ALL-CAPS slot or a bracket containing filler
# words, NOT any bracketed text. A first attempt used r"\[[^\]]{3,}\]" and threw
# away 62 of 187 perfectly good openers that merely used parenthetical brackets.
# A live run shipped "Hi [Owner's Name], noticed Point Roofing's..." — a mail-merge
# slot the model left for a human to fill. Bracketed GENERIC-SUBJECT words are the
# tell; a legitimate bracketed aside ("[43 reviews]") names no such subject.
_PLACEHOLDER_RE = re.compile(
    r"\[[^\]]*\b(?i:assumed|insert|placeholder|tbd|todo|fill in|your name|your city)\b[^\]]*\]"
    # Word STEMS, not whole words: a live run shipped "[Named Nearby Competitor]",
    # which \bname\b could never match.
    r"|\[[^\]]*\b(?i:name|city|company|business|owner|contact|email|phone|location|"
    r"first|last|title|address|website|competitor|industry|service|client)\w*[^\]]*\]"
    r"|\[[A-Z][A-Z_ ]{2,}[^\]]*\]"
    r"|\{\{[^}]*\}\}"
)


def _clean_text(s) -> str | None:
    """Normalise one model-authored string: no markdown, no stray whitespace."""
    if s is None:
        return None
    s = _MD_HEAD_RE.sub("", _MD_BOLD_RE.sub("", str(s)))
    s = re.sub(r"\s+", " ", s).strip(" -–—:;")
    return s or None


def _close_json(s: str) -> str:
    """Close a JSON fragment that max_tokens cut off mid-value.

    Walks the text tracking string/escape state and the bracket stack, then
    appends the missing closers. A run over 175 leads lost 6 of them to
    "Unterminated string" at ~2800 chars — the fields that HAD been emitted
    (pitch_angle comes first) were thrown away with the tail.
    """
    out, stack, in_str, esc = [], [], False, False
    for ch in s:
        out.append(ch)
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        out.append('"')
    text = "".join(out).rstrip()
    # Drop a dangling separator or a key with no value ({"a":1,"b": -> {"a":1)
    text = re.sub(r'(?:,\s*)?"[^"]*"\s*:\s*$', "", text).rstrip().rstrip(",")
    return text + "".join(reversed(stack))


def _loads_lenient(raw: str) -> dict:
    """Parse JSON that a reasoning model may have wrapped in prose / fences,
    or left truncated because it ran out of output tokens."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    s = (raw or "").strip()
    if "```" in s:  # strip markdown fences
        s = s.split("```")[1].removeprefix("json").strip() if s.count("```") >= 2 else s
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except json.JSONDecodeError:
            pass
    # Truncated: salvage whatever fields did land rather than losing the lead.
    if start != -1:
        try:
            parsed = json.loads(_close_json(s[start:]))
            logger.info("recovered truncated LLM JSON (%d chars)", len(s))
            return parsed
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("no JSON object in response", raw, 0)


def _call_ollama(prompt: str, model: str, url: str) -> str:
    resp = httpx.post(
        f"{url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False,
              "format": "json", "keep_alive": "30s"},
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["response"]

_PROMPT = """You are a B2B sales strategist analysing ONE business to plan a cold outreach. \
Reason ONLY from the facts below — never invent details. Where a fact is "(none)"/"(unknown)", \
treat its absence as a signal (e.g. no website -> needs web presence; no SSL -> security/trust gap; \
old/WordPress site -> redesign; low rating or review weaknesses -> reputation management; \
few reviews -> review-generation; no socials -> social presence).

{vertical_context}

Business: {name}
Category / what they do: {business_category}
Location: {location}
Website: {website}  | platform: {website_cms}  | secure(SSL): {has_ssl}
Services listed: {services}
About: {description}
Reputation: rating {rating} from {review_count} reviews (~{review_velocity}/yr) | open now: {is_open} | price: {price_level}
3rd-party platforms they rely on (commission): {external_platforms} | runs own ordering/booking: {has_direct}
Marketing/ad tech on their site: {marketing_stack} | running paid ads: {runs_paid_ads}
Known review weaknesses: {review_weaknesses}
Actual recent negative review snippets (verbatim): {review_texts}
People: owner {owner_name} ({owner_title}) | team size {employee_count} | founded {year_founded}
Social presence: {socials}

Return ONLY JSON: {{"pain_points":["...","..."],"pitch_angle":"...","opener":"...","review_themes":["...","..."]}}
- pain_points: 2-4 SPECIFIC, evidence-based problems, each tied to a fact above (cite the signal).
- pitch_angle: name the EXACT service/system to sell them and why it fits THIS business \
(e.g. "a booking + review-request system because they have 0 reviews and no website"). 1-2 sentences.
- opener: one cold-email opening line under 25 words that references a concrete detail about them \
(not generic). Sound human, not salesy.
- review_themes: 0-4 recurring complaint themes from the review snippets above (e.g. "long wait times", \
"billing disputes"). Empty list if no snippets given. Never invent themes not present in the snippets."""


def research_lead(lead_dict: dict, model: str = DEFAULT_MODEL, url: str = DEFAULT_URL,
                  provider: str = "ollama", api_key: str = "",
                  base_url: str = "", models: list[str] | None = None) -> dict:
    """Deep-research one lead. Returns {pitch_angle, pain_points, opener} (strings).

    provider="nvidia" uses the NVIDIA NIM cloud (model fallback chain in `models`);
    anything else uses local Ollama (`model`/`url`)."""
    from backend.services.vertical_profiles import match_profile
    from backend.services.business_intel import review_velocity

    g = lead_dict.get
    vel = review_velocity(g("review_count"), g("year_founded"))
    prompt = _PROMPT.format(
        vertical_context=match_profile(g("business_category")).prompt_context(),
        review_velocity=vel if vel is not None else "unknown",
        name=g("name") or "",
        business_category=g("business_category") or "(unknown)",
        location=g("location") or "(unknown)",
        website=g("website") or "(none)",
        website_cms=g("website_cms") or "(unknown)",
        has_ssl="yes" if g("has_ssl") else "no/unknown",
        services=g("services") or "(none listed)",
        description=g("description") or "(none)",
        rating=g("rating") if g("rating") not in (None, "") else "(unknown)",
        review_count=g("review_count") if g("review_count") not in (None, "") else "(unknown)",
        is_open=g("is_open") if g("is_open") not in (None, "") else "(unknown)",
        price_level=g("price_level") or "(unknown)",
        external_platforms=g("external_platforms") or "(none detected)",
        has_direct="yes" if g("has_direct_commerce") else "no/unknown",
        marketing_stack=g("marketing_stack") or "(none detected)",
        runs_paid_ads="yes" if g("runs_paid_ads") else "no/unknown",
        review_weaknesses=g("review_weaknesses") or "(none found)",
        review_texts=g("review_texts") or "(none)",
        owner_name=g("owner_name") or "(unknown)",
        owner_title=g("owner_title") or "(unknown)",
        employee_count=g("employee_count") if g("employee_count") not in (None, "") else "(unknown)",
        year_founded=g("year_founded") if g("year_founded") not in (None, "") else "(unknown)",
        socials=g("socials") or "(none found)",
    )
    if provider == "nvidia":
        raw = _call_nvidia(prompt, api_key=api_key, base_url=base_url,
                           models=models or [])
    else:
        raw = _call_ollama(prompt, model=model, url=url)

    parsed = _loads_lenient(raw)

    pain = parsed.get("pain_points")
    if isinstance(pain, list):
        pain = "; ".join(str(p) for p in pain)
    elif pain is not None:
        pain = str(pain)

    themes = parsed.get("review_themes")
    if isinstance(themes, list):
        themes = ", ".join(str(t) for t in themes)
    elif themes is not None:
        themes = str(themes)

    opener = _clean_text(parsed.get("opener"))
    # An opener carrying an unfilled placeholder is worse than no opener — it
    # would be pasted straight into an email. Drop it; the rest still ships.
    #
    # `_rejected` marks fields we deliberately threw away, as opposed to fields
    # the model simply never emitted (a truncated response). The caller keeps an
    # existing value in the second case but must CLEAR it in the first —
    # otherwise re-running on a lead that already had a bad opener regenerates a
    # bad one, drops it, and the old bad one survives untouched.
    rejected: list[str] = []
    if opener and _PLACEHOLDER_RE.search(opener):
        logger.info("dropping opener with unfilled placeholder: %s", opener[:80])
        opener, _ = None, rejected.append("opener")

    return {
        "pitch_angle": _clean_text(parsed.get("pitch_angle")),
        "pain_points": _clean_text(pain),
        "opener": opener,
        "review_themes": _clean_text(themes),
        "_rejected": rejected,
    }


# ── Extraction fallback ───────────────────────────────────────────────────────
# Regex/BeautifulSoup in business_intel does the extraction; this only fires where
# it came back empty AND the homepage was already fetched (so the text is in the
# shared per-lead page cache — zero extra network). Cost scales with the gap, not
# the pool.
_EXTRACT_SYSTEM = (
    "You extract facts from web page text. You never guess, never infer, and "
    "never use outside knowledge. If a fact is not stated verbatim on the page, "
    "you return null for it. You answer with a single JSON object, nothing else."
)

_EXTRACT_PROMPT = """Below is the visible text of {name}'s website.

Extract ONLY what is literally stated. Return null for anything absent — a null is
correct and useful; a guess is a defect.

Return ONLY JSON:
{{"services": "comma-separated list or null",
  "owner_name": "full name of the owner/founder/principal, or null",
  "owner_title": "their exact title as written, or null",
  "employee_count": integer or null,
  "year_founded": 4-digit integer or null}}

Rules:
- owner_name must be a PERSON's name. Never a company, department, page heading,
  or phrase like "Our Team" / "About Us" / "Contact Us".
- employee_count only if the page states a headcount. Never estimate from a team
  page you cannot count.
- year_founded only from an explicit founding/established statement. NOT a
  copyright year.
- services: what they sell, as written on the page.

PAGE TEXT:
{text}"""


def extract_facts(name: str, page_text: str, *, api_key: str, base_url: str,
                  models: list[str], max_chars: int = 4000) -> dict:
    """Pull missing structured facts out of page text. Returns only keys the page
    actually supports; everything else is omitted (never guessed)."""
    if not page_text or not page_text.strip():
        return {}
    prompt = _EXTRACT_PROMPT.format(name=name or "this business",
                                    text=page_text[:max_chars])
    raw = _call_nvidia(prompt, api_key=api_key, base_url=base_url, models=models,
                       system=_EXTRACT_SYSTEM, max_tokens=400)
    parsed = _loads_lenient(raw)

    out: dict = {}
    for key in ("services", "owner_name", "owner_title"):
        val = _clean_text(parsed.get(key))
        if val and val.lower() not in ("null", "none", "n/a", "unknown"):
            out[key] = val
    for key in ("employee_count", "year_founded"):
        val = parsed.get(key)
        if isinstance(val, str) and val.strip().isdigit():
            val = int(val)
        if isinstance(val, int) and val > 0:
            out[key] = val

    # The model is told not to return a company/heading as a person, but the
    # deterministic gate is what we actually trust — same one contact_finder uses.
    if "owner_name" in out:
        from backend.services.contact_finder import is_plausible_person_name
        if not is_plausible_person_name(out["owner_name"]):
            logger.info("LLM owner_name rejected by name gate: %r", out["owner_name"])
            out.pop("owner_name")
    if "year_founded" in out and not (1700 < out["year_founded"] <= 2100):
        out.pop("year_founded")
    if "employee_count" in out and not (1 <= out["employee_count"] <= 50000):
        out.pop("employee_count")
    return out


def _review_snippets(raw_json: str | None, max_texts: int = 6) -> str:
    """Turn stored review JSON (from review_scraper) into a compact snippet blob
    for the LLM prompt. Returns '' when there are no reviews."""
    if not raw_json:
        return ""
    try:
        reviews = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    worst = sorted((r for r in reviews if r.get("text")),
                   key=lambda r: (r.get("stars") if isinstance(r.get("stars"), int) else 5))
    snippets = [f'{r.get("stars", "?")}★: {r["text"][:160]}' for r in worst[:max_texts]]
    return " | ".join(snippets)


def _lead_facts(lead) -> dict:
    socials = ", ".join(
        s for s in (
            "facebook" if getattr(lead, "social_facebook", None) else "",
            "instagram" if getattr(lead, "social_instagram", None) else "",
            "linkedin" if getattr(lead, "social_linkedin", None) else "",
            "twitter" if getattr(lead, "social_twitter", None) else "",
        ) if s
    )
    return {
        "name": getattr(lead, "name", None),
        "business_category": getattr(lead, "business_category", None),
        "location": getattr(lead, "address", None) or getattr(lead, "city", None),
        "website": getattr(lead, "website", None),
        "website_cms": getattr(lead, "website_cms", None),
        "has_ssl": getattr(lead, "has_ssl", None),
        "services": getattr(lead, "services", None),
        "description": getattr(lead, "description", None),
        "rating": getattr(lead, "google_rating", None),
        "review_count": getattr(lead, "google_review_count", None),
        "is_open": getattr(lead, "google_is_open", None),
        "price_level": getattr(lead, "price_level", None),
        "external_platforms": getattr(lead, "external_platforms", None),
        "has_direct_commerce": getattr(lead, "has_direct_commerce", None),
        "marketing_stack": getattr(lead, "marketing_stack", None),
        "runs_paid_ads": getattr(lead, "runs_paid_ads", None),
        "review_weaknesses": getattr(lead, "review_weaknesses", None),
        "review_texts": _review_snippets(getattr(lead, "review_lowest_texts", None)),
        "owner_name": getattr(lead, "owner_name", None),
        "owner_title": getattr(lead, "owner_title", None),
        "employee_count": getattr(lead, "employee_count", None),
        "year_founded": getattr(lead, "year_founded", None),
        "socials": socials,
    }


def _resolve_backend(model: str, url: str):
    """Decide NVIDIA vs Ollama from settings. Returns kwargs for research_lead
    plus a worker count. Falls back to Ollama if no NVIDIA key."""
    try:
        from backend.config import settings as s
        provider = (s.RESEARCH_PROVIDER or "auto").lower()
        key = s.NVIDIA_API_KEY or ""
        if provider == "auto":
            provider = "nvidia" if key else "ollama"
        if provider == "nvidia" and key:
            models = [m.strip() for m in (s.NVIDIA_MODELS or "").split(",") if m.strip()]
            return (dict(provider="nvidia", api_key=key,
                         base_url=s.NVIDIA_BASE_URL, models=models),
                    max(1, s.PITCH_CONCURRENCY))
    except Exception as e:  # noqa: BLE001
        logger.info("research backend resolve fell back to ollama: %s", e)
    # Local Ollama: keep it serial (one model, one GPU) unless caller overrides.
    return (dict(provider="ollama", model=model, url=url), 1)


def research_leads_sync(leads, model: str = DEFAULT_MODEL, url: str = DEFAULT_URL) -> None:
    """Deep-research each ORM lead in place. NVIDIA backend runs leads in parallel
    (rate-limited); Ollama runs serial. Per-lead soft-fail so one bad lead (or a
    transient hiccup) never aborts the batch."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    call_kwargs, workers = _resolve_backend(model, url)

    def _one(lead):
        try:
            result = research_lead(_lead_facts(lead), **call_kwargs)
            # A result salvaged from a truncated response is PARTIAL — models emit
            # these keys in their own order, so a cut-off reply can carry
            # pain_points but no pitch_angle. Only ever fill; never let a missing
            # field blank out a value the lead already has.
            rejected = set(result.get("_rejected") or ())
            for attr in ("pitch_angle", "pain_points", "opener"):
                if result.get(attr):
                    setattr(lead, attr, result[attr])
                elif attr in rejected:
                    # Deliberately rejected (e.g. a placeholder opener) — clearing
                    # is the point. Only an ABSENT field keeps its old value.
                    setattr(lead, attr, None)
            if result.get("review_themes") and not getattr(lead, "review_themes", None):
                lead.review_themes = result["review_themes"]
        except Exception as e:  # noqa: BLE001 — soft-fail per lead
            logger.warning("pitch_research failed for %s: %s", getattr(lead, "name", "?"), e)

    if workers <= 1:
        for lead in leads:
            _one(lead)
        return
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_one, l) for l in leads]):
            fut.result()  # _one swallows its own errors; this just joins
