---
title: Health and Thread-Safety
type: concept
tags: [tor-proxy, concurrency, health, reliability]
source: backend/services/proxy_rotator.py
updated: 2026-07-05
---

# Health and Thread-Safety

> [!info] Keeping the hot path cheap and the shared state safe
> Parent: [[ProxyRotator]] · Related: [[Circuit Capacity and Concurrency]]

## Two kinds of "health" — don't confuse them

| Check | Cost | Method | When |
|-------|------|--------|------|
| **Liveness** | cheap — 1 s TCP connect | `is_tor_running()` / `check_health(verify_ip=False)` | **every** `get_proxy` |
| **Exit-IP verification** | slow — httpx to 3 echo URLs | `get_current_ip()` / `check_health(verify_ip=True)` | only `get_all_ips()` / diagnostics |

> [!danger] Never do IP verification on the hot path
> An earlier version verified the exit IP inside `get_proxy` every 60 s. In the **async** Playwright path that blocked the event loop for up to ~30 s. Fix: `get_proxy` uses `verify_ip=False` (TCP only); the expensive lookup moved out. See [[TorInstance#Health & IP methods]].

## `health_summary()` vs `get_all_ips()`

- `health_summary()` (line 475) — **fast**, reads cached flags only. Powers `/health` so the endpoint stays snappy. It does **not** re-probe exit IPs (a unit test pins this contract).
- `get_all_ips()` (line 464) — **slow**, deliberately verifies each exit IP over the network. Use it for diagnostics ("are my 5 circuits really distinct?"), not in request paths.

## Thread-safety

The singleton is hit concurrently by `ThreadPoolExecutor` fan-outs (tiling in `sources.py`, enrichment batches in `pipeline.py`, `pitch_research.py`). Shared mutable state is guarded by `self._lock` (a `threading.Lock`):

| Protected | Why |
|-----------|-----|
| `_current_index` | round-robin read-modify-write |
| `_request_count` | auto-rotate-every-N counter |
| `_last_rotation` | cooldown gate (only one caller rotates) |
| health-flag reads for capacity | consistent snapshot |

> [!note] Network work happens *outside* the lock
> `rotate_ip` decides *whether* to rotate under the lock, then releases it before the `NEWNYM` + settle sleep. Losers of the cooldown race simply skip — the winner's fresh circuit serves everyone. Prevents serializing all workers behind a 5 s rotation.

## Re-discovery

`_ensure_initialized()` re-probes for Tor when the pool is empty and >30 s have passed (`REDISCOVER_COOLDOWN`). So a Tor daemon that comes up **after** first use is picked up automatically instead of being pinned to direct forever.

#tor-proxy/concept
