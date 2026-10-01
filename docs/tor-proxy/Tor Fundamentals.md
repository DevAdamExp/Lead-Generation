---
title: Tor Fundamentals
type: concept
tags: [tor-proxy, tor, fundamentals]
updated: 2026-07-05
---

# Tor Fundamentals

> [!info] Scope
> Only the Tor mechanics this system actually leans on. Not a general Tor primer.
> Parent: [[Tor Proxy System]] · Enables: [[Circuit Isolation]]

## Circuit = 3 hops = one exit IP

A Tor **circuit** is a path through three relays:

```
your app ──SOCKS──> [Guard] ──> [Middle] ──> [Exit] ──> target site
```

The **exit** relay is the IP the target site sees. **Different circuit → different exit IP.** Everything this subsystem does is really about *"how do I get another circuit?"*

> [!note] Why Tor is free but slow
> Three encrypted hops means ~0.5–2 Mbit and higher latency than a direct request. This is the structural speed ceiling discussed in [[Proxy Strategy Tiers]].

## The two ways to force a distinct circuit

This is the crux — Tor gives you a **new circuit** (new exit IP) in two independent ways, *without* restarting anything:

### 1. `IsolateSOCKSAuth` (per SOCKS username)
On by **default**. If two connections to the same `SocksPort` present **different SOCKS username/password**, Tor isolates them onto **different circuits**.

- Password is irrelevant — Tor doesn't authenticate it, only uses the pair as an isolation key.
- Effectively **unlimited** distinct exit IPs from **one port**.
- ⚠️ Only usable by clients that *send* SOCKS auth — see the Chromium caveat below.

### 2. `SocksPort` (per listening port)
One daemon can listen on many SOCKS ports (`SocksPort 9050`, `9052`, …). Streams on **different ports** get **different circuits** too.

- Bounded (you configure N ports).
- Works for **any** SOCKS client, including ones that can't send auth.

> [!warning] The Chromium caveat (drives the whole design)
> **Chromium ignores SOCKS5 username/password auth.** So the elegant "unlimited IPs on one port" trick works for **httpx** but **not** for **Playwright/Chromium**. That asymmetry is exactly why the code isolates httpx by auth and Playwright by port. Full story in [[Circuit Isolation]].

## `NEWNYM` — the *old* way to rotate

A control-port signal that tells Tor "build fresh circuits for new connections."

- Requires a **ControlPort** + auth.
- Internally **rate-limited** (~10 s minimum between effective NEWNYMs).
- Blocks while the new circuit settles (~5 s in our old code).
- **Superseded** by isolation: with per-key circuits you never need to rotate an existing circuit — you just use a new key. NEWNYM survives only as the `report_block()` fallback via [[ProxyRotator#rotate_ip and report_block]].

## `MaxCircuitDirtiness`

How long Tor keeps reusing one circuit for *new* streams before building a fresh one. We set **600 s** (`torrc`). This is why a **stable tag = a stable IP** for the life of a worker (see [[get_proxy and Tags]]): the same auth/port keeps landing on the same circuit for ~10 minutes.

## Cheat-sheet

| Term | Meaning | Where it shows up |
|------|---------|-------------------|
| Circuit | 3-hop path; one exit IP | everywhere |
| `SocksPort` | SOCKS listener; distinct port → distinct circuit | Playwright isolation |
| `IsolateSOCKSAuth` | distinct SOCKS user → distinct circuit | httpx isolation |
| `ControlPort` | control channel for `NEWNYM` | `report_block()` fallback |
| `NEWNYM` | "new circuits please" signal | legacy rotation |
| `MaxCircuitDirtiness` | circuit reuse window (600 s) | stable per-worker IP |

#tor-proxy/concept
