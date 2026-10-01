#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stop all Tor instances started by start_tor_instances.sh
# ─────────────────────────────────────────────────────────────────────────────

BASE_SOCKS=${1:-9050}

echo "🛑 Stopping Tor instances on SOCKS ports >= ${BASE_SOCKS}…"

# Kill Tor processes by port
for port in $(lsof -i :${BASE_SOCKS}-10000 -P -n 2>/dev/null | grep LISTEN | awk '{print $9}' | grep -oE '\d+$' | sort -u); do
    pid=$(lsof -ti :${port} 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "  Killing Tor on port ${port} (PID: ${pid})"
        kill "${pid}" 2>/dev/null || kill -9 "${pid}" 2>/dev/null || true
    fi
done

# Kill remaining tor daemons
pkill -f "tor.*SOCKSPort" 2>/dev/null || true

sleep 1
echo "  ✅ All Tor instances stopped"
lsof -i :${BASE_SOCKS}-10000 -P -n 2>/dev/null | grep LISTEN | grep -q tor && echo "  ⚠️  Some instances may still be running" || echo "  ✅ Clean shutdown"
