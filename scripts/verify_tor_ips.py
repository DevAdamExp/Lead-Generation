"""
scripts/verify_tor_ips.py — Verify all Tor instances are running with unique IPs.

Usage:
    python scripts/verify_tor_ips.py

Output:
    - Status of each Tor instance (port, IP, health)
    - Unique IP count
    - Summary (pass/fail)
"""
import httpx
import sys
import time

TOR_PORTS = [9050, 9150, 9250, 9350, 9450, 9550, 9650, 9750, 9850, 9950]
PROBE_URL = "https://check.torproject.org/api/ip"
TIMEOUT = 10


def check_port(port: int) -> dict:
    start = time.time()
    try:
        with httpx.Client(proxy=f"socks5://127.0.0.1:{port}", timeout=TIMEOUT) as client:
            resp = client.get(PROBE_URL)
            data = resp.json()
            return {
                "port": port,
                "ip": data.get("IP", "unknown"),
                "is_tor": data.get("IsTor", False),
                "latency": round(time.time() - start, 2),
                "healthy": True,
            }
    except Exception as e:
        return {
            "port": port,
            "ip": None,
            "is_tor": False,
            "latency": round(time.time() - start, 2),
            "healthy": False,
            "error": str(e),
        }


def main():
    results = [check_port(p) for p in TOR_PORTS]
    healthy = [r for r in results if r["healthy"]]
    ips = {r["ip"] for r in healthy if r.get("ip")}

    print(f"{'Port':>6} {'IP':<18} {'Tor':>5} {'Latency':>8}  Status")
    print("-" * 60)
    for r in results:
        status = "✅" if r["healthy"] else "❌"
        ip = r.get("ip") or "—"
        is_tor = "✓" if r.get("is_tor") else "✗"
        latency = f"{r['latency']}s" if r.get("latency") else "—"
        print(f"{r['port']:>6} {ip:<18} {is_tor:>5} {latency:>8}  {status}")

    print(f"\nHealthy: {len(healthy)}/{len(TOR_PORTS)}")
    print(f"Unique IPs: {len(ips)}")
    print(f"Min latency: {min(r['latency'] for r in healthy):.2f}s" if healthy else "")
    print(f"Max latency: {max(r['latency'] for r in healthy):.2f}s" if healthy else "")

    all_healthy = len(healthy) == len(TOR_PORTS)
    all_unique = len(ips) == len(healthy)
    summary = "PASS" if all_healthy and all_unique else "FAIL"
    print(f"\nResult: {summary}")
    return 0 if all_healthy and all_unique else 1


if __name__ == "__main__":
    sys.exit(main())
