"""
services/proxy_rotator.py — Tor Multi-IP Proxy Rotator.

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │                     ProxyRotator (singleton)                    │
  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐ │
  │  │ TorInstance │  │ TorInstance │  │ TorInstance │  │ Fallback │ │
  │  │ :9050      │  │ :9150      │  │ :9250      │  │ Direct   │ │
  │  └────────────┘  └────────────┘  └────────────┘  └──────────┘ │
  │         │               │               │              │        │
  │         └───────────────┴───────────────┴──────────────┘        │
  │                           │ Pool                                 │
  │                    Round-Robin Rotation                          │
  └─────────────────────────────────────────────────────────────────┘

Features:
  - Automatic Tor detection and circuit management
  - Multiple Tor instances for concurrent multi-IP scraping
  - NEWNYM circuit switching via stem control protocol
  - IP verification after rotation
  - Proxy output for both httpx (SOCKS5) and Playwright
  - Graceful degradation to direct connection
  - Round-robin proxy pool for concurrent requests
"""
import logging
import random
import re
import socket
import threading
import time
import zlib
from dataclasses import dataclass
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# Default Tor SOCKS / control ports — must mirror config.py's defaults so a
# failed settings import narrows to the same pool. Independent daemons.
DEFAULT_TOR_PORTS = [9050, 9150, 9250, 9350, 9450, 9550, 9650, 9750, 9850, 9950, 10050, 10150, 10250, 10350, 10450, 10550, 10650, 10750, 10850, 10950]
DEFAULT_TOR_CONTROL_PORTS = [9051, 9151, 9251, 9351, 9451, 9551, 9651, 9751, 9851, 9951, 10051, 10151, 10251, 10351, 10451, 10551, 10651, 10751, 10851, 10951]

# How long to wait for a new circuit to establish
CIRCUIT_WAIT_SECONDS = 5

# How many seconds to cache IP verification
IP_CACHE_TTL = 60

# Re-probe for Tor instances at most this often when the pool is empty
REDISCOVER_COOLDOWN = 30

# Ports that accepted a TCP connection but failed the circuit check, with WHEN.
# Remembered so re-discovery (which fires whenever the pool is empty) does not
# re-pay the ~10s probe every 30s against something that will never be Tor — the
# SSH tunnel squatting on :10250, say.
#
# Time-bounded, because a healthy daemon can fail transiently: a live run saw
# :9150 miss one probe while its circuit rebuilt, and a permanent ban would have
# dropped a working exit IP for the rest of the process. After the TTL it gets
# another chance.
_UNUSABLE_PORTS: dict = {}
UNUSABLE_PORT_TTL = 600   # seconds


def _port_is_known_bad(port: int) -> bool:
    ts = _UNUSABLE_PORTS.get(port)
    if ts is None:
        return False
    if time.time() - ts > UNUSABLE_PORT_TTL:
        _UNUSABLE_PORTS.pop(port, None)      # expired — let it prove itself again
        return False
    return True

# External IP check endpoints
IP_CHECK_URLS = [
    "https://check.torproject.org/api/ip",
    "https://httpbin.org/ip",
    "https://api.ipify.org?format=json",
]


def _tag_slot(tag: str, n: int) -> int:
    """Map a worker tag to one of `n` circuits — stably and evenly.

    Was `abs(hash(tag)) % n`. Python randomises str hashing per process, so the
    tag->circuit mapping changed on every restart AND clustered badly: measured
    across four processes, tags maps0..maps3 mapped to [2,0,2,2], [3,3,3,2],
    [1,0,1,0], [2,2,1,3] — i.e. three of four Maps workers could share one exit
    IP. That silently defeats the per-worker isolation the enrichment and
    discovery concurrency is sized against (they cap workers at
    circuit_capacity() believing each gets its own IP), raising per-IP request
    rate and block risk. crc32 is stable across processes and spreads evenly.
    """
    if n <= 0:
        return 0
    # Callers tag workers with an index — "maps0".."maps3", "disc0".., "rev0"..
    # Use it directly: that is a perfect round-robin over the circuits, whereas
    # hashing similar strings into a small modulus clusters badly (crc32 put
    # maps0..maps3 on circuits [2,0,2,0] — half the pool idle, two pairs sharing
    # an IP). crc32 remains the fallback for unindexed tags.
    m = re.search(r"(\d+)$", tag)
    if m:
        return int(m.group(1)) % n
    return zlib.crc32(tag.encode("utf-8")) % n


@dataclass
class TorInstance:
    """Represents a single Tor daemon with SOCKS + optional control port."""
    socks_port: int
    control_port: Optional[int] = None
    label: str = "tor"
    host: str = "127.0.0.1"

    # Runtime state
    last_ip: Optional[str] = None
    ip_last_checked: float = 0.0
    healthy: bool = True
    consecutive_failures: int = 0
    circuit_id: Optional[str] = None
    _request_timestamps: list = None  # per-IP throttle window

    def __post_init__(self):
        if self._request_timestamps is None:
            self._request_timestamps = []

    @property
    def socks_url(self) -> str:
        return f"socks5://{self.host}:{self.socks_port}"

    def throttle(self, max_per_minute: int = 15) -> None:
        """Sleep if this IP has exceeded the rate limit in the last minute.

        Called by callers before each request. Ensures no single exit IP
        makes more than `max_per_minute` requests per 60-second window.
        """
        now = time.time()
        # Prune timestamps older than 60s
        self._request_timestamps = [
            t for t in self._request_timestamps if now - t < 60
        ]
        if len(self._request_timestamps) >= max_per_minute:
            # Sleep until the oldest request falls out of the window
            sleep_time = 60 - (now - self._request_timestamps[0]) + 0.5
            if sleep_time > 0:
                logger.debug("%s: Throttling %.1fs (window full: %d/%d)",
                             self.label, sleep_time,
                             len(self._request_timestamps), max_per_minute)
                time.sleep(sleep_time)
        self._request_timestamps.append(time.time())

    @property
    def proxy_dict_playwright(self) -> Optional[dict]:
        return {"server": self.socks_url}

    @property
    def proxy_dict_httpx(self) -> dict:
        return {
            "all://": self.socks_url,
            "http://": self.socks_url,
            "https://": self.socks_url,
        }

    def isolated_httpx_dict(self, tag: str) -> dict:
        """httpx proxy dict on an isolated Tor circuit. Tor's IsolateSOCKSAuth
        (on by default) gives a distinct circuit — and thus a distinct exit IP —
        per unique SOCKS username, on the SAME port. So `tag` picks the circuit;
        the password is a throwaway (Tor doesn't authenticate it).

        Chromium ignores SOCKS auth, so this is httpx-only — Playwright callers
        isolate by choosing a different SocksPort instead (see get_proxy)."""
        url = f"socks5://{tag}:x@{self.host}:{self.socks_port}"
        return {"all://": url, "http://": url, "https://": url}

    def is_tor_running(self) -> bool:
        """Check if the SOCKS port is accepting connections."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect((self.host, self.socks_port))
                return True
        except (ConnectionRefusedError, OSError, socket.timeout):
            return False

    def circuit_works(self, timeout: float = 8.0) -> bool:
        """Prove this port is a WORKING TOR proxy before the pool adopts it.

        Two failures this prevents, both observed live:

        1. Not Tor at all. TOR_SOCKS_PORTS lists 20 ports and is_tor_running()
           only checks that one accepts a TCP connection — so an SSH tunnel
           listening on 10250 was adopted as a Tor daemon and every scrape was
           routed into it. Hence the IsTor assertion: a merely-working proxy is
           not good enough.
        2. Open but dead. A daemon still bootstrapping accepts the connection and
           then black-holes the request. httpx's timeout did NOT cover the SOCKS
           handshake (a probe hung for minutes), so the call runs in a thread
           under a hard deadline and the thread is abandoned if it overruns.

        Runs once per daemon at discovery, never on the hand-out path.
        """
        from backend.utils.retry import call_with_deadline

        def _probe() -> bool:
            import httpx
            with httpx.Client(proxy=self.socks_url, timeout=timeout) as c:
                r = c.get("https://check.torproject.org/api/ip")
                return r.status_code == 200 and r.json().get("IsTor") is True

        try:
            ok, finished = call_with_deadline(_probe, timeout + 2, False)
        except Exception as e:
            logger.debug("%s circuit check failed: %s", self.label, e)
            return False
        if not finished:
            logger.debug("%s circuit check timed out — treating as unusable", self.label)
            return False
        return bool(ok)


    def get_current_ip(self) -> Optional[str]:
        """Determine the external IP seen through this Tor instance."""
        import httpx
        for url in IP_CHECK_URLS:
            try:
                # httpx >= 0.28 takes a single `proxy=` string (proxies= dict removed)
                with httpx.Client(proxy=self.socks_url, timeout=10) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        ip = (data.get("origin") or data.get("ip")
                              or data.get("IP") or data.get("ip_address"))
                        if ip:
                            # httpx may return "x.x.x.x, y.y.y.y" if there are proxies
                            return ip.split(",")[0].strip()
            except Exception:
                continue
        return None

    def rotate_circuit(self) -> bool:
        """Send NEWNYM signal to Tor control port to get a new exit IP."""
        if not self.control_port:
            logger.warning("%s: No control port configured — cannot rotate circuit", self.label)
            return False

        try:
            from stem import Signal
            from stem.control import Controller

            with Controller.from_port(port=self.control_port) as controller:
                controller.authenticate()
                controller.signal(Signal.NEWNYM)
                self.circuit_id = str(int(time.time()))

            logger.info("%s: NEWNYM signal sent, waiting %ds for new circuit…",
                        self.label, CIRCUIT_WAIT_SECONDS)
            time.sleep(CIRCUIT_WAIT_SECONDS)

            # Verify IP changed
            new_ip = self.get_current_ip()
            if new_ip:
                logger.info("%s: New exit IP = %s (was %s)",
                            self.label, new_ip, self.last_ip or "unknown")
                self.last_ip = new_ip
                self.ip_last_checked = time.time()
                self.consecutive_failures = 0
                return True
            else:
                logger.warning("%s: Could not verify new IP after NEWNYM", self.label)
                self.consecutive_failures += 1
                return False

        except Exception as e:
            logger.warning("%s: Circuit rotation failed: %s", self.label, e)
            self.consecutive_failures += 1
            return False

    def check_health(self, verify_ip: bool = False) -> bool:
        """Liveness check: is the SOCKS port open?

        `verify_ip=True` additionally does a (slow, network) exit-IP lookup —
        only the health endpoint / post-rotation paths want that. The proxy
        hand-out path (`get_proxy`) must stay cheap, so it passes False: no
        multi-second httpx call inside the request/event-loop hot path.
        """
        if not self.is_tor_running():
            if self.healthy:
                logger.warning("%s: Tor not running on %s:%d",
                               self.label, self.host, self.socks_port)
            self.healthy = False
            self.consecutive_failures += 1
            return False

        if verify_ip:
            now = time.time()
            if now - self.ip_last_checked > IP_CACHE_TTL:
                self.last_ip = self.get_current_ip()
                self.ip_last_checked = now

        self.healthy = True
        return True


class ProxyRotator:
    """
    Multi-IP proxy rotator.

    Usage:
        rotator = ProxyRotator()
        rotator.discover_tor_instances()    # auto-detect Tor
        proxy = rotator.get_proxy()         # get a proxy for Playwright
        rotator.rotate_ip()                 # switch to new exit IP

    Manages a pool of Tor instances and rotates through them
    round-robin. Each request gets the next proxy in the pool.
    """

    def __init__(self, config: Optional[dict] = None):
        self._instances: List[TorInstance] = []
        self._current_index: int = 0
        self._direct_fallback: bool = True
        self._config = config or {}
        self._initialized: bool = False
        self._external_proxies: List[str] = []
        # Concurrency: sources.py / pipeline.py fan out via ThreadPoolExecutor
        # onto this singleton, so the round-robin index, request counter and
        # rotation timestamp are all shared mutable state. Guard them.
        self._lock = threading.Lock()
        self._request_count: int = 0
        self._last_rotation: float = 0.0
        self._last_discover: float = 0.0
        self._cooldown: int = int(self._config.get("ip_rotation_cooldown", 30))
        self._rotate_every_n: int = int(self._config.get("ip_rotate_every_n", 0))
        self._max_rpm: int = int(self._config.get("max_requests_per_ip_per_minute", 15))

    def discover_tor_instances(self) -> int:
        """Auto-detect running Tor instances by probing common ports.
        Returns the number of healthy instances found."""
        socks_ports = self._config.get("tor_socks_ports", DEFAULT_TOR_PORTS)
        control_ports = self._config.get("tor_control_ports", DEFAULT_TOR_CONTROL_PORTS)

        found = 0
        for i, socks_port in enumerate(socks_ports):
            ctrl_port = control_ports[i] if i < len(control_ports) else None
            inst = TorInstance(
                socks_port=socks_port,
                control_port=ctrl_port,
                label=f"tor:{socks_port}",
            )
            if _port_is_known_bad(socks_port):
                continue
            if inst.is_tor_running():
                # An open port is NOT a working proxy. A half-dead daemon (still
                # bootstrapping, or with no usable circuits) accepts the TCP
                # connection and then black-holes every request. One such daemon
                # made Hotfrog hang 22 minutes and Google Maps return 0 results
                # for every tile, because the rotator had adopted it and routed
                # everything through it. Prove the circuit carries traffic before
                # letting it into the pool — once, at discovery, not per hand-out.
                if not inst.circuit_works():
                    _UNUSABLE_PORTS[socks_port] = time.time()
                    logger.warning("Tor on %s accepts connections but its circuit "
                                   "does not carry traffic — excluding it "
                                   "(won't re-probe this port)", inst.socks_url)
                    continue
                inst.check_health()
                self._instances.append(inst)
                found += 1
                logger.info("Discovered Tor instance: %s", inst.socks_url)
            else:
                logger.debug("No Tor on %s:%d", inst.host, socks_port)

        # Load external SOCKS proxies from config
        ext_proxies = self._config.get("external_socks_proxies", [])
        for url in ext_proxies:
            self._external_proxies.append(url)
            logger.info("Loaded external proxy: %s", url)

        self._initialized = True
        self._last_discover = time.time()

        if not self._instances and not self._external_proxies:
            logger.warning("No Tor instances or external proxies found. "
                           "Falling back to direct connection.")

        return found

    def add_instance(self, inst: TorInstance) -> None:
        """Manually add a Tor instance."""
        self._instances.append(inst)
        self._initialized = True

    def _ensure_initialized(self) -> None:
        """Lazy init — discover Tor on first use, and re-probe periodically if
        the pool is still empty (Tor may have come up after the first call)."""
        if not self._initialized:
            self.discover_tor_instances()
            return
        if (not self._instances and not self._external_proxies
                and time.time() - self._last_discover > REDISCOVER_COOLDOWN):
            logger.info("Proxy pool empty — re-probing for Tor instances…")
            self.discover_tor_instances()

    def get_proxy(self, for_playwright: bool = True,
                  tag: Optional[str] = None) -> Optional[dict]:
        """
        Get a proxy from the pool.

        Args:
            for_playwright: True → Playwright format ({"server": "socks5://..."});
                            False → httpx format ({"all://": "socks5://..."}).
            tag: Optional circuit-isolation key. Callers that own a long-lived
                 worker (a browser context, an enrichment slot) pass a STABLE tag
                 so that worker rides its own exit IP, independent of every other
                 worker — the core of concurrent multi-IP scraping. Two workers
                 with different tags get different circuits/IPs; the same tag
                 reuses one circuit (up to Tor's MaxCircuitDirtiness).
                   - httpx  → isolation via SOCKS auth on one port (unlimited IPs)
                   - Playwright → isolation by SocksPort (Chromium ignores auth),
                                  so distinct tags fan out across the port pool.
                 tag=None keeps the legacy round-robin behaviour byte-for-byte.

        Returns:
            Proxy dict or None for direct connection.
        """
        self._ensure_initialized()

        # Auto-rotate every N hand-outs (0 = disabled). Decide under the lock,
        # rotate outside it (rotate_ip sleeps for the circuit to settle).
        with self._lock:
            self._request_count += 1
            due = (self._rotate_every_n > 0
                   and self._request_count % self._rotate_every_n == 0)
        if due:
            self.rotate_ip(force_new=True)

        if tag is not None:
            return self._isolated_proxy(for_playwright, tag)

        with self._lock:
            # Try external proxies first (round-robin)
            if self._external_proxies:
                proxy_url = self._external_proxies[
                    self._current_index % len(self._external_proxies)
                ]
                self._current_index += 1
                return {"server": proxy_url} if for_playwright else {"all://": proxy_url}

            # Try Tor instances (round-robin, skip unhealthy ones)
            if self._instances:
                for _ in range(len(self._instances) * 2):  # try each instance up to 2x
                    idx = self._current_index % len(self._instances)
                    inst = self._instances[idx]
                    self._current_index += 1
                    if inst.check_health():  # cheap: TCP liveness only
                        return (inst.proxy_dict_playwright if for_playwright
                                else inst.proxy_dict_httpx)
                logger.warning("All Tor instances unhealthy — using direct connection")

        return None  # direct connection

    def _isolated_proxy(self, for_playwright: bool, tag: str) -> Optional[dict]:
        """Deterministic per-tag circuit selection (see get_proxy `tag`)."""
        # External proxies are real distinct endpoints — map the tag onto one.
        if self._external_proxies:
            url = self._external_proxies[_tag_slot(tag, len(self._external_proxies))]
            return {"server": url} if for_playwright else {"all://": url}

        with self._lock:
            healthy = [i for i in self._instances if i.check_health()]
        if not healthy:
            if self._instances:
                logger.warning("All Tor instances unhealthy — using direct connection")
            return None

        # Playwright/Chromium can't do SOCKS auth → isolate by picking a port.
        # httpx → one port is enough; SOCKS-auth gives the distinct circuit.
        inst = healthy[_tag_slot(tag, len(healthy))]
        return inst.proxy_dict_playwright if for_playwright else inst.isolated_httpx_dict(tag)

    def get_random_proxy(self, for_playwright: bool = True) -> Optional[dict]:
        """
        Get a random proxy from the pool (random selection, not round-robin).
        Useful for distributing load when making many concurrent requests.
        """
        self._ensure_initialized()
        healthy = [i for i in self._instances if i.check_health()]  # cheap liveness
        if healthy:
            inst = random.choice(healthy)
            if for_playwright:
                return inst.proxy_dict_playwright
            return inst.proxy_dict_httpx

        if self._external_proxies:
            proxy_url = random.choice(self._external_proxies)
            if for_playwright:
                return {"server": proxy_url}
            return {"all://": proxy_url}

        return None

    def get_httpx_client_args(self) -> dict:
        """Get httpx.Client arguments for the next proxy."""
        proxy = self.get_proxy(for_playwright=False)
        if proxy:
            # get_proxy returns a {scheme: url} dict; httpx >= 0.28 wants a single
            # `proxy=` string (proxies= dict removed).
            url = proxy.get("all://") or next(iter(proxy.values()), None)
            if url:
                return {"proxy": url}
        return {}

    def rotate_ip(self, force_new: bool = True) -> bool:
        """
        Rotate to a new exit IP by picking a random healthy Tor instance
        and sending NEWNYM (or picking a different instance).

        Args:
            force_new: If True, sends NEWNYM to get a fresh circuit.
                      If False, just picks a different proxy from the pool.

        Returns:
            True if IP was successfully rotated, False otherwise.
        """
        self._ensure_initialized()

        # Rate-limit rotations (NEWNYM abuse gets you throttled by Tor, and each
        # rotation blocks CIRCUIT_WAIT_SECONDS). Concurrent callers that lose the
        # race just skip — the winner's fresh circuit serves everyone.
        now = time.time()
        with self._lock:
            if now - self._last_rotation < self._cooldown:
                return False
            self._last_rotation = now

        if force_new and self._instances:
            # Try to rotate circuit on a random healthy instance
            healthy = [i for i in self._instances if i.check_health() and i.control_port]
            if healthy:
                inst = random.choice(healthy)
                logger.info("Rotating circuit on %s…", inst.label)
                return inst.rotate_circuit()

            # No controllable Tor instance — try to find one by probing
            logger.info("No controllable Tor instance found — probing...")
            for inst in self._instances:
                if inst.control_port and inst.rotate_circuit():
                    return True

            # If we have multiple instances, just mark current as unhealthy
            # to force picking a different one
            if len(self._instances) > 1:
                logger.info("No Tor control — cycling to next instance")
                old_idx = self._current_index % len(self._instances)
                self._instances[old_idx].healthy = False
                self._current_index += 1
                return True

            logger.warning("Cannot rotate IP: no Tor control port available")
            return False

        if force_new:
            # force_new requested but no instances to rotate — nothing happened.
            return False

        # Non-force: just advance the round-robin index
        with self._lock:
            self._current_index += 1
        return True

    def throttle_request(self, proxy_dict: Optional[dict] = None) -> None:
        """Call before each proxied request to enforce per-IP rate limits.

        Finds the TorInstance that owns this proxy and throttles it.
        For direct connections or unknown proxies, this is a no-op.
        """
        max_rpm = int(self._config.get("max_requests_per_ip_per_minute", 15))
        if not proxy_dict or not self._instances:
            return

        # Find which instance this proxy belongs to
        proxy_url = (proxy_dict.get("server")
                     or proxy_dict.get("all://")
                     or proxy_dict.get("https://")
                     or "")
        if not proxy_url:
            return

        for inst in self._instances:
            if inst.socks_url in proxy_url:
                inst.throttle(max_rpm)
                return

    def circuit_capacity(self) -> int:
        """Number of DISTINCT concurrent exit IPs the pool can hand out. Callers
        size their worker concurrency by this so they never pile several workers
        onto one IP (which would raise per-IP request rate and block risk).
        Playwright isolates by SocksPort → capacity = healthy ports; external
        proxies → their count; empty pool → 1 (direct: a single shared IP)."""
        self._ensure_initialized()
        if self._external_proxies:
            return len(self._external_proxies)
        with self._lock:
            healthy = sum(1 for i in self._instances if i.check_health())
        return healthy or 1

    def report_block(self) -> bool:
        """Call when a scraper hits a 403 / captcha / block on a proxied request.
        Triggers a fresh exit IP (cooldown-limited). Safe to call from many
        worker threads — only the first within the cooldown window rotates."""
        logger.info("Block reported — requesting new exit circuit")
        return self.rotate_ip(force_new=True)

    def get_all_ips(self) -> Dict[str, Optional[str]]:
        """Get the current exit IP for each Tor instance."""
        self._ensure_initialized()
        result = {}
        for inst in self._instances:
            if inst.check_health(verify_ip=True):
                result[inst.socks_url] = inst.last_ip
            else:
                result[inst.socks_url] = None
        return result

    def get_all_ips_fast(self, max_workers: int = 10) -> Dict[str, Optional[str]]:
        """Get exit IPs for all instances in parallel (much faster than sequential).

        Uses ThreadPoolExecutor to check up to `max_workers` instances at once.
        Each IP check goes through Tor (slow), so parallelism helps a lot.
        """
        self._ensure_initialized()
        result = {}
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _check_inst(inst):
            try:
                ip = inst.get_current_ip()
                return inst.socks_url, ip
            except Exception:
                return inst.socks_url, None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_check_inst, inst): inst for inst in self._instances}
            for fut in as_completed(futures):
                try:
                    socks_url, ip = fut.result()
                    result[socks_url] = ip
                except Exception:
                    result[futures[fut].socks_url] = None

        return result

    def health_summary(self) -> Dict:
        """Fast health summary from cached flags (maintained by get_proxy's
        liveness probe). Stays cheap so /health returns promptly — use
        get_all_ips() when you actually need fresh exit-IP verification."""
        self._ensure_initialized()
        return {
            "instances": [
                {
                    "label": i.label,
                    "socks": i.socks_url,
                    "healthy": i.healthy,
                    "last_ip": i.last_ip,
                    "failures": i.consecutive_failures,
                }
                for i in self._instances
            ],
            "external_proxies": len(self._external_proxies),
            "total_healthy": sum(1 for i in self._instances if i.healthy),
        }

    # ── Health monitor + auto-restart ─────────────────────────────────────
    _monitor_thread: Optional[threading.Thread] = None
    _monitor_stop: threading.Event = threading.Event()

    def start_health_monitor(self, interval: int = 60) -> None:
        """Background thread that checks daemon health every `interval` seconds
        and auto-restarts dead ones. Call once at startup."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return  # already running

        self._monitor_stop.clear()

        def _monitor():
            while not self._monitor_stop.is_set():
                self._monitor_stop.wait(interval)
                if self._monitor_stop.is_set():
                    break
                self._check_and_restart_dead()

        self._monitor_thread = threading.Thread(target=_monitor, daemon=True,
                                                 name="tor-health-monitor")
        self._monitor_thread.start()
        logger.info("Tor health monitor started (interval=%ds)", interval)

    def stop_health_monitor(self) -> None:
        """Stop the background health monitor."""
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    def _check_and_restart_dead(self) -> None:
        """Check all instances, restart any that are dead.

        Runs OUTSIDE self._lock. It used to hold the lock across a time.sleep(3)
        per dead daemon — with 20 configured ports all down that pinned the global
        proxy lock for a full minute, every minute, stalling every get_proxy()
        caller (all enrichment threads + the Maps event loop). We also spawn every
        dead daemon first and wait ONCE, turning N*3s into 3s.
        """
        import subprocess, os

        with self._lock:                       # snapshot only — no I/O under lock
            snapshot = list(enumerate(self._instances))

        dead = []
        for i, inst in snapshot:
            if _port_is_known_bad(socks_port):
                continue
            if inst.is_tor_running():
                inst.healthy = True
            else:
                dead.append((i, inst))
        if not dead:
            return

        tor_bin = os.environ.get("TOR_BIN") or "/opt/homebrew/bin/tor"
        spawned = []
        for i, inst in dead:
            logger.warning("Daemon %s is dead — restarting...", inst.label)
            ctrl_port = inst.control_port or (9051 + i * 100)
            try:
                subprocess.Popen([
                    tor_bin,
                    "--SocksPort", f"127.0.0.1:{inst.socks_port} IsolateSOCKSAuth",
                    "--ControlPort", str(ctrl_port),
                    "--CookieAuthentication", "1",
                    "--DataDirectory", f"/opt/homebrew/var/lib/tor/tor_{i}",
                    "--Log", f"notice file /opt/homebrew/var/log/tor/tor_{i}.log",
                    "--RunAsDaemon", "1",
                    "--ExitRelay", "0",
                    "--BridgeRelay", "0",
                    "--MaxCircuitDirtiness", "30",
                    "--ExitNodes", "{us}",
                    "--StrictNodes", "1",
                    "--UseEntryGuards", "1",
                    "--NewCircuitPeriod", "15",
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                spawned.append(inst)
            except Exception as e:
                logger.error("Daemon %s restart error: %s", inst.label, e)

        if not spawned:
            return
        time.sleep(3)  # one bootstrap wait for the whole wave, not one per daemon

        restarted = 0
        for inst in spawned:
            if _port_is_known_bad(socks_port):
                continue
            if inst.is_tor_running():
                inst.healthy = True
                inst.consecutive_failures = 0
                restarted += 1
            else:
                logger.error("Daemon %s restart failed", inst.label)
        if restarted:
            logger.info("Auto-restarted %d dead Tor daemons", restarted)


# ── Global singleton ───────────────────────────────────────────────────────────

_rotator: Optional[ProxyRotator] = None


def _build_config_from_settings() -> dict:
    """Build proxy rotator config from project settings."""
    try:
        from backend.config import settings

        socks_ports = [int(p.strip()) for p in settings.TOR_SOCKS_PORTS.split(",") if p.strip()]
        ctrl_ports = [int(p.strip()) for p in settings.TOR_CONTROL_PORTS.split(",") if p.strip()]
        ext_proxies = [p.strip() for p in settings.EXTERNAL_SOCKS_PROXIES.split(",") if p.strip()]

        return {
            "tor_socks_ports": socks_ports or DEFAULT_TOR_PORTS,
            "tor_control_ports": ctrl_ports or DEFAULT_TOR_CONTROL_PORTS,
            "external_socks_proxies": ext_proxies,
            "ip_rotation_cooldown": settings.IP_ROTATION_COOLDOWN,
            "ip_rotate_every_n": settings.IP_ROTATE_EVERY_N_REQUESTS,
            "max_requests_per_ip_per_minute": settings.TOR_MAX_REQUESTS_PER_IP_PER_MINUTE,
        }
    except Exception:
        return {
            "tor_socks_ports": list(DEFAULT_TOR_PORTS),
            "tor_control_ports": list(DEFAULT_TOR_CONTROL_PORTS),
            "external_socks_proxies": [],
        }


def get_rotator(config: Optional[dict] = None) -> ProxyRotator:
    """Get or create the global ProxyRotator singleton."""
    global _rotator
    if _rotator is None:
        cfg = config if config is not None else _build_config_from_settings()
        _rotator = ProxyRotator(cfg)
        _rotator.discover_tor_instances()
        # Start health monitor to auto-restart dead daemons
        _rotator.start_health_monitor(interval=60)
    return _rotator


def reset_rotator():
    """Reset the global rotator (useful for testing or reconfiguration)."""
    global _rotator
    _rotator = None
