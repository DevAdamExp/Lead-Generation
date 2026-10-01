#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Start N independent Tor daemons for multi-IP scraping.
#
# Architecture:
#   Daemon i: SockPort (9050 + i*100), ControlPort (9051 + i*100), DataDir ./tor_i
#   Each daemon bootstraps independently → own entry guard → own circuit pool
#   → own exit IPs. Google blocking one daemon's IPs doesn't affect others.
#
# Usage:
#   ./scripts/start_tor_instances.sh [num_daemons]    # default: 20
#   ./scripts/start_tor_instances.sh 30                # even more IPs
#   ./scripts/start_tor_instances.sh --status          # check running daemons
#   ./scripts/start_tor_instances.sh --restart         # kill all, restart fresh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

TOR_BIN="${TOR_BIN:-$(which tor 2>/dev/null || echo /opt/homebrew/bin/tor)}"
BASE_DATA_DIR="${TOR_DATA_DIR:-/opt/homebrew/var/lib/tor}"
BASE_LOG_DIR="${TOR_LOG_DIR:-/opt/homebrew/var/log/tor}"
CIRCUIT_DIRTINESS="${TOR_CIRCUIT_DIRTINESS:-30}"    # 30s = fast IP rotation
EXIT_COUNTRIES="${TOR_EXIT_COUNTRIES:-us}"

# ── Status / Restart modes ───────────────────────────────────────────────────
show_status() {
    echo "═══════════════════════════════════════════════════════════════════"
    echo "  Tor Daemon Status"
    echo "═══════════════════════════════════════════════════════════════════"
    RUNNING=0; TOTAL=0
    for i in $(seq 0 29); do
        PORT=$((9050 + i * 100))
        if lsof -i :"$PORT" -P -n 2>/dev/null | grep -q LISTEN; then
            BOOTSTRAPPED=$(grep -c "Bootstrapped 100%" "${BASE_LOG_DIR}/tor_${i}.log" 2>/dev/null || echo "0")
            if [[ "$BOOTSTRAPPED" -gt 0 ]]; then
                echo "  daemon $i (port $PORT): ✅ running"
                RUNNING=$((RUNNING + 1))
            else
                echo "  daemon $i (port $PORT): ⏳ bootstrapping..."
            fi
            TOTAL=$((TOTAL + 1))
        fi
    done
    echo "═══════════════════════════════════════════════════════════════════"
    echo "  Running: $RUNNING  |  Total: $TOTAL"
    echo "═══════════════════════════════════════════════════════════════════"
}

kill_all() {
    echo "Killing all Tor daemons..."
    # Match OUR daemons only, via the DataDirectory we launch them with.
    # `pkill -f tor` is a substring match over the whole command line and would
    # also kill system processes containing "tor" -- thermalmonitord,
    # contextstored, opendirectoryd all match.
    pkill -f "${BASE_DATA_DIR}/tor_" 2>/dev/null || true
    sleep 2
    # Verify dead
    ALIVE=0
    for i in $(seq 0 29); do
        PORT=$((9050 + i * 100))
        if lsof -i :"$PORT" -P -n 2>/dev/null | grep -q LISTEN; then
            ALIVE=$((ALIVE + 1))
        fi
    done
    if [[ $ALIVE -gt 0 ]]; then
        echo "Force killing remaining $ALIVE daemons..."
        pkill -9 -f "${BASE_DATA_DIR}/tor_" 2>/dev/null || true
        sleep 1
    fi
    echo "All Tor daemons stopped."
}

case "${1:-}" in
    --status)  show_status; exit 0 ;;
    --restart) kill_all; shift; set -- "${1:-20}"; ;;
    --stop)    kill_all; exit 0 ;;
esac

NUM_DAEMONS="${1:-20}"
mkdir -p "${BASE_LOG_DIR}"

# ── Port allocation ──────────────────────────────────────────────────────────
# Parse .env ports if available (backward compat)
ENV_SOCKS="${TOR_SOCKS_PORTS:-}"
if [[ -n "$ENV_SOCKS" ]]; then
    IFS=',' read -ra SOCKS_PORTS <<< "$ENV_SOCKS"
    IFS=',' read -ra CTRL_PORTS <<< "${TOR_CONTROL_PORTS:-}"
    NUM_DAEMONS=${#SOCKS_PORTS[@]}
else
    SOCKS_PORTS=()
    CTRL_PORTS=()
    for i in $(seq 0 $((NUM_DAEMONS - 1))); do
        SOCKS_PORTS+=($((9050 + i * 100)))
        CTRL_PORTS+=($((9051 + i * 100)))
    done
fi

# ── Count already running ────────────────────────────────────────────────────
RUNNING=0
for port in "${SOCKS_PORTS[@]}"; do
    if lsof -i :"$port" -P -n 2>/dev/null | grep -q LISTEN; then
        RUNNING=$((RUNNING + 1))
    fi
done

if [[ $RUNNING -eq ${#SOCKS_PORTS[@]} ]]; then
    echo "⚠️  All ${#SOCKS_PORTS[@]} Tor daemons already running."
    show_status
    exit 0
fi

# ── Start missing daemons ───────────────────────────────────────────────────
STARTED=0
FAILED=0

for i in "${!SOCKS_PORTS[@]}"; do
    SOCKS_PORT="${SOCKS_PORTS[$i]}"
    CTRL_PORT="${CTRL_PORTS[$i]}"
    DATA_DIR="${BASE_DATA_DIR}/tor_${i}"
    LOG_FILE="${BASE_LOG_DIR}/tor_${i}.log"

    # Skip if already running
    if lsof -i :"$SOCKS_PORT" -P -n 2>/dev/null | grep -q LISTEN; then
        continue
    fi

    mkdir -p "${DATA_DIR}" "$(dirname "${LOG_FILE}")"

    # Truncate old log on fresh start
    > "${LOG_FILE}"

    echo -n "  daemon $i (socks:${SOCKS_PORT})... "

    "${TOR_BIN}" \
        --SocksPort "127.0.0.1:${SOCKS_PORT} IsolateSOCKSAuth" \
        --ControlPort "${CTRL_PORT}" \
        --CookieAuthentication 1 \
        --DataDirectory "${DATA_DIR}" \
        --Log "notice file ${LOG_FILE}" \
        --RunAsDaemon 1 \
        --ExitRelay 0 \
        --BridgeRelay 0 \
        --MaxCircuitDirtiness "${CIRCUIT_DIRTINESS}" \
        --ExitNodes "{${EXIT_COUNTRIES}}" \
        --StrictNodes 1 \
        --UseEntryGuards 1 \
        --NumEntryGuards 2 \
        --NewCircuitPeriod 15 \
        2>/dev/null

    sleep 0.3

    if lsof -i :"$SOCKS_PORT" -P -n 2>/dev/null | grep -q LISTEN; then
        echo "✅"
        STARTED=$((STARTED + 1))
    else
        echo "❌"
        FAILED=$((FAILED + 1))
    fi
done

# ── Wait for bootstrap (parallel) ────────────────────────────────────────────
echo ""
echo "Waiting for daemons to bootstrap..."

BOOTSTRAPPED=0
MAX_WAIT=60  # seconds
for attempt in $(seq 1 $MAX_WAIT); do
    BOOTSTRAPPED=0
    for i in "${!SOCKS_PORTS[@]}"; do
        LOG_FILE="${BASE_LOG_DIR}/tor_${i}.log"
        if grep -q "Bootstrapped 100%" "$LOG_FILE" 2>/dev/null; then
            BOOTSTRAPPED=$((BOOTSTRAPPED + 1))
        fi
    done
    if [[ $BOOTSTRAPPED -eq ${#SOCKS_PORTS[@]} ]]; then
        break
    fi
    sleep 1
    # Progress dots
    if [[ $((attempt % 5)) -eq 0 ]]; then
        echo -n "  $BOOTSTRAPPED/${#SOCKS_PORTS[@]} ready (${attempt}s)..."
    fi
done

echo ""
echo ""
TOTAL_OK=$((${#SOCKS_PORTS[@]} - FAILED))
echo "═══════════════════════════════════════════════════════════════════"
echo "  Tor: ${BOOTSTRAPPED}/${#SOCKS_PORTS[@]} daemons bootstrapped"
echo "  Circuit dirtiness: ${CIRCUIT_DIRTINESS}s"
echo "  Exit countries: ${EXIT_COUNTRIES}"
echo "  NewCircuitPeriod: 15s"
echo "═══════════════════════════════════════════════════════════════════"

if [[ $BOOTSTRAPPED -eq 0 ]]; then
    echo "  ❌ No daemons bootstrapped. Check logs in ${BASE_LOG_DIR}/"
    exit 1
fi

echo ""
echo "⚡ Quick IP verification (first 5):"
.venv/bin/python -c "
import httpx, sys
ports = [${SOCKS_PORTS[*]:0:5}]
for p in ports[:5]:
    try:
        with httpx.Client(proxy=f'socks5://127.0.0.1:{p}', timeout=8) as c:
            r = c.get('https://api.ipify.org?format=json')
            print(f'  tor:{p} -> {r.json().get(\"ip\",\"?\")}')
    except:
        print(f'  tor:{p} -> (checking...)')
" 2>/dev/null || true
