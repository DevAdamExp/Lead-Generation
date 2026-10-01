"""Tests for the Hotfrog hard-block circuit breaker (fail fast on 403/429)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hotfrog"))
import hotfrog_core as core


def test_block_short_circuits_fetch(monkeypatch):
    core.reset_block_state()
    called = {"httpx": 0, "pw": 0}
    monkeypatch.setattr(core, "_fetch_with_httpx",
                        lambda *a, **k: called.__setitem__("httpx", called["httpx"] + 1))
    monkeypatch.setattr(core, "_fetch_with_playwright",
                        lambda *a, **k: called.__setitem__("pw", called["pw"] + 1))

    core._HARD_BLOCKED = True
    assert core._fetch_page("https://www.hotfrog.com/search/us/x") is None
    assert called == {"httpx": 0, "pw": 0}     # never even attempted
    core.reset_block_state()


def test_httpx_block_falls_through_to_playwright(monkeypatch):
    """A Cloudflare 403 on httpx is a JS challenge the BROWSER can solve — the
    browser fallback must run, not be skipped (this was the bug that killed
    Hotfrog: an httpx 403 tripped the breaker before Playwright ever ran)."""
    core.reset_block_state()
    pw = {"n": 0}
    monkeypatch.setattr(core, "_fetch_with_httpx", lambda *a, **k: None)  # blocked

    def fake_pw(*a, **k):
        pw["n"] += 1
        return "<html><body>real listings</body></html>"

    monkeypatch.setattr(core, "_fetch_with_playwright", fake_pw)

    html = core._fetch_page("https://www.hotfrog.com/search/us/x")
    assert pw["n"] == 1                        # browser WAS attempted
    assert html and "real listings" in html
    assert core._HARD_BLOCKED is False         # succeeded — breaker stays clear
    core.reset_block_state()


def test_breaker_trips_only_when_browser_also_fails(monkeypatch):
    """Breaker trips (fail-fast for later pages) only after BOTH httpx and the
    browser fail on the current IP."""
    core.reset_block_state()
    monkeypatch.setattr(core, "_fetch_with_httpx", lambda *a, **k: None)
    monkeypatch.setattr(core, "_fetch_with_playwright", lambda *a, **k: None)

    assert core._fetch_page("https://www.hotfrog.com/search/us/x") is None
    assert core._HARD_BLOCKED is True          # now later pages fail fast
    core.reset_block_state()


def test_challenge_html_is_rejected(monkeypatch):
    """If the browser returns a Cloudflare interstitial (not real content), it
    must be treated as a failure, not accepted as data."""
    core.reset_block_state()
    monkeypatch.setattr(core, "_fetch_with_httpx", lambda *a, **k: None)
    monkeypatch.setattr(core, "_fetch_with_playwright",
                        lambda *a, **k: "<title>Just a moment...</title>")

    assert core._fetch_page("https://www.hotfrog.com/search/us/x") is None
    assert core._HARD_BLOCKED is True
    core.reset_block_state()


def test_reset_clears_block():
    core._HARD_BLOCKED = True
    core.reset_block_state()
    assert core._HARD_BLOCKED is False
