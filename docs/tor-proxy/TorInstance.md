---
title: TorInstance
type: class
tags: [tor-proxy, class]
source: backend/services/proxy_rotator.py
updated: 2026-07-05
---

# TorInstance

> [!info] One SOCKS port as a health-tracked unit
> `backend/services/proxy_rotator.py` (class at line 59) · Owned by: [[ProxyRotator]]

A dataclass wrapping a single Tor SOCKS endpoint (`host:socks_port`), its optional control port, and runtime health state (`last_ip`, `healthy`, `consecutive_failures`, `ip_last_checked`).

> [!note] "Instance" ≠ "daemon"
> Historically each `TorInstance` was a separate Tor **process**. Under [[Circuit Isolation]] they're now just the several `SocksPort`s of **one** daemon (see [[Tor Daemon Deployment]]). The class didn't need to change — it only ever cared about a port.

## Proxy-shape builders

| Member | Line | Returns | Used by |
|--------|:--:|---------|---------|
| `socks_url` | 74 | `socks5://host:port` | everything |
| `proxy_dict_playwright` | 78 | `{"server": socks_url}` | Playwright, **no auth** |
| `proxy_dict_httpx` | 82 | 3-key `{all://,http://,https://}` | httpx, no tag (legacy) |
| `isolated_httpx_dict(tag)` | 89 | `socks5://{tag}:x@host:port` (3-key) | httpx **with** tag → [[Circuit Isolation]] |

> [!tip] Why `isolated_httpx_dict` embeds the tag in the URL
> Tor's `IsolateSOCKSAuth` keys off the SOCKS **username**. Putting `tag` in the userinfo (`{tag}:x@`) is how httpx tells Tor "give me *this* circuit." The `x` password is a throwaway. This member is httpx-only — Chromium can't use it (see [[Tor Fundamentals#The Chromium caveat drives the whole design]]).

## Health & IP methods

| Method | Line | Cost | Notes |
|--------|:--:|------|-------|
| `is_tor_running()` | 100 | cheap (1 s TCP connect) | liveness only |
| `check_health(verify_ip=False)` | 167 | cheap by default | `verify_ip=True` adds a network exit-IP lookup |
| `get_current_ip()` | 110 | **slow** (httpx to 3 IP-echo URLs) | populates `last_ip` |
| `rotate_circuit()` | 129 | **slow** (NEWNYM + settle) | needs a control port |

> [!warning] Keep `verify_ip` off the hot path
> `get_proxy()` calls `check_health()` with the default `verify_ip=False` — a TCP probe, not a web request. The expensive `get_current_ip()` only runs in `get_all_ips()` / explicit verification. Rationale in [[Health and Thread-Safety]].

#tor-proxy/class
