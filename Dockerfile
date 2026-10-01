FROM python:3.11-slim AS builder

WORKDIR /app

# System deps for WeasyPrint, Tor, Playwright
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 libpangoft2-1.0-0 libglib2.0-0 \
    libffi-dev libcairo2 libpq-dev gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock* requirements.txt* ./

# Install dependencies with uv (no dev deps)
# Try locked install first, then resolve fresh if no lockfile
RUN uv sync --no-dev --frozen --no-install-project || \
    uv sync --no-dev --no-install-project || \
    uv pip install --system -r requirements.txt

# Install Playwright chromium
RUN uv run playwright install chromium
RUN uv run playwright install-deps chromium

# ── Runtime stage ────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 libpangoft2-1.0-0 libglib2.0-0 \
    libcairo2 libpq-dev \
    tor \
    && rm -rf /var/lib/apt/lists/*

# Configure Tor — ONE daemon, several SocksPorts. httpx isolates circuits by
# SOCKS auth on 9050 (IsolateSOCKSAuth, unlimited exit IPs); Chromium can't do
# SOCKS auth, so Playwright workers isolate by picking a distinct SocksPort.
RUN mkdir -p /var/lib/tor /var/log/tor && \
    echo "SocksPort 9050 IsolateSOCKSAuth" > /etc/tor/torrc && \
    echo "SocksPort 9052" >> /etc/tor/torrc && \
    echo "SocksPort 9053" >> /etc/tor/torrc && \
    echo "SocksPort 9054" >> /etc/tor/torrc && \
    echo "SocksPort 9055" >> /etc/tor/torrc && \
    echo "ControlPort 9051" >> /etc/tor/torrc && \
    echo "CookieAuthentication 1" >> /etc/tor/torrc && \
    echo "MaxCircuitDirtiness 600" >> /etc/tor/torrc && \
    echo "ExitRelay 0" >> /etc/tor/torrc

# Copy uv + venv from builder
COPY --from=builder /uv /uvx /bin/
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

# Use uv's venv python by default
ENV PATH="/app/.venv/bin:$PATH" \
    UV_SYSTEM_PYTHON=1 \
    SCRAPER_HEADLESS=true

# Expose API port
EXPOSE 8000

# Default command (overridden in docker-compose)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
