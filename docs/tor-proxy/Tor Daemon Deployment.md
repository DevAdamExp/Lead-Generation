---
title: Tor Daemon Deployment
type: reference
tags: [tor-proxy, deployment, ops, tor]
source: Dockerfile, scripts/start_tor_instances.sh, docker-compose.yml
updated: 2026-07-05
---

# Tor Daemon Deployment

> [!info] One daemon, many SocksPorts
> Parent: [[Tor Proxy System]] · Enables: [[Circuit Isolation]] · Config: [[Configuration]]

## The `torrc` (Docker)

`Dockerfile` writes:

```
SocksPort 9050 IsolateSOCKSAuth
SocksPort 9052
SocksPort 9053
SocksPort 9054
SocksPort 9055
ControlPort 9051
CookieAuthentication 1
MaxCircuitDirtiness 600
ExitRelay 0
```

| Line | Why |
|------|-----|
| `9050 IsolateSOCKSAuth` | httpx circuit-per-username (explicit; it's the default) |
| `9052–9055` | extra ports → Chromium circuit-per-port ([[Tor Fundamentals#The Chromium caveat drives the whole design]]) |
| `ControlPort 9051` | `NEWNYM` fallback for `report_block()` |
| `MaxCircuitDirtiness 600` | stable per-worker IP for ~10 min → [[get_proxy and Tags]] |
| `ExitRelay 0` | never act as an exit relay |

## Local dev

`scripts/start_tor_instances.sh` — rewritten to launch **one** daemon with the same SocksPorts (was: N separate daemons on 9050/9150/9250). Aligns local dev with Docker + [[Configuration]].

## docker-compose caveat

> [!warning] `dperson/torproxy` exposes only 9050
> `docker-compose.yml` uses the `dperson/torproxy` image, which by default serves a single SOCKS port. Under that image, `circuit_capacity()` = 1 → enrichment falls back to the graceful single-port mode (2 contexts, shared IP) — **correct but not accelerated**. To get the multi-IP win in compose, configure the image (or the app's own Tor) to open the extra `SocksPort`s. [[Circuit Isolation#Graceful degradation why its low-risk]] explains why this is safe.

## Live validation

> [!todo] The check unit tests can't do
> After deploying the multi-port `torrc`, confirm the circuits are really distinct:
> ```bash
> python3 -c "from backend.services.proxy_rotator import get_rotator; print(get_rotator().get_all_ips())"
> ```
> Expect **distinct** exit IPs across the ports. Then run one real enrichment job and watch for Google consent walls; tune `MAPS_ENRICH_DELAY_MIN` accordingly ([[Circuit Capacity and Concurrency]]).

#tor-proxy/reference #tor-proxy/ops
