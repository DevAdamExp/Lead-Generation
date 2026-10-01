"""Phase-2 fresh-circuit retry + failure classification for directory scrapers.

Verifies the retry loop rotates circuits on block/dead outcomes and bails fast
on target_down/reject (where a new exit IP can't help)."""
from unittest.mock import patch

import backend.services.yelp_scraper as yelp
import backend.services.yellowpages_scraper as yp

OK_HTML = "<html>" + "x" * 3000 + "</html>"   # long + no block markers => not blocked


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class _FakeClient:
    """Pops a scripted (status, text) or Exception off a shared queue per .get()."""
    queue = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        item = _FakeClient.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(*item)


def _run_yelp(script):
    _FakeClient.queue = list(script)
    with patch("httpx.Client", _FakeClient), \
         patch.object(yelp, "_proxy_kwargs", return_value={"proxy": "socks5://t:x@h:9050"}), \
         patch.object(yelp, "_fetch_playwright", return_value=None):
        return yelp._fetch("https://yelp.com/biz/x")


# ── classification ───────────────────────────────────────────────────────────

def test_classify_yelp():
    assert yelp._classify(200, blocked=False) == "ok"
    assert yelp._classify(403, blocked=False) == "blocked"
    assert yelp._classify(200, blocked=True) == "blocked"
    assert yelp._classify(503, blocked=False) == "target_down"
    assert yelp._classify(404, blocked=False) == "reject"


def test_classify_yp_short_body_is_block():
    assert yp._classify(200, "x" * 3000) == "ok"
    assert yp._classify(200, "tiny") == "blocked"      # interstitial, not a 403
    assert yp._classify(500, "") == "target_down"
    assert yp._classify(404, "x" * 3000) == "reject"


# ── retry behaviour ──────────────────────────────────────────────────────────

def test_direct_ok_skips_proxy():
    # Single response consumed (direct); proxy never reached.
    assert _run_yelp([(200, OK_HTML)]) == OK_HTML


def test_retries_on_fresh_circuit_after_block():
    # direct blocked → proxy#1 blocked → proxy#2 ok
    out = _run_yelp([(403, ""), (403, ""), (200, OK_HTML)])
    assert out == OK_HTML


def test_dead_circuit_retries():
    # direct dead (timeout) → proxy#1 dead → proxy#2 ok
    import httpx
    out = _run_yelp([httpx.ConnectError("boom"), httpx.TimeoutException("t"), (200, OK_HTML)])
    assert out == OK_HTML


def test_target_down_bails_fast_to_playwright():
    # direct 500 → proxy#1 500 → STOP (no 2nd proxy try) → playwright(None)
    _FakeClient.queue = [(500, ""), (500, "")]
    with patch("httpx.Client", _FakeClient), \
         patch.object(yelp, "_proxy_kwargs", return_value={"proxy": "socks5://t:x@h:9050"}), \
         patch.object(yelp, "_fetch_playwright", return_value=None) as pw:
        assert yelp._fetch("https://yelp.com/biz/x") is None
        pw.assert_called_once()
    assert _FakeClient.queue == []  # exactly 2 gets consumed, 2nd proxy retry skipped


def test_all_blocked_falls_to_playwright():
    _FakeClient.queue = [(403, ""), (403, ""), (403, "")]
    with patch("httpx.Client", _FakeClient), \
         patch.object(yelp, "_proxy_kwargs", return_value={"proxy": "socks5://t:x@h:9050"}), \
         patch.object(yelp, "_fetch_playwright", return_value="PW") as pw, \
         patch.object(yelp, "_is_blocked", return_value=False):
        assert yelp._fetch("https://yelp.com/biz/x") == "PW"
        pw.assert_called_once()
