"""Tests for review_scraper pure logic (no browser)."""
from backend.services import review_scraper as R


def test_parse_stars():
    assert R.parse_stars("Rated 2.0 out of 5") == 2
    assert R.parse_stars("1 star") == 1
    assert R.parse_stars("5 stars") == 5
    assert R.parse_stars(None) is None
    assert R.parse_stars("no stars here") is None


def test_summarize_weaknesses_from_negatives():
    reviews = [
        {"stars": 1, "date": "a week ago", "text": "rude staff and dirty rooms"},
        {"stars": 2, "date": "2 weeks ago", "text": "billing was wrong twice"},
        {"stars": 5, "date": "3 years ago", "text": "great"},
    ]
    text, count = R.summarize_review_weaknesses(reviews, {"1": 3, "2": 2, "5": 5},
                                                owner_responds=False)
    assert count >= 3
    assert "1-2★" in text
    assert "does not reply" in text


def test_summarize_weaknesses_clean_business():
    reviews = [{"stars": 5, "date": "today", "text": "perfect"}]
    text, count = R.summarize_review_weaknesses(reviews, {"5": 20}, owner_responds=True)
    assert count == 0
    assert text is None


def test_build_theme_input_worst_first():
    reviews = [
        {"stars": 5, "text": "loved it"},
        {"stars": 1, "text": "terrible"},
        {"stars": 3, "text": "meh"},
        {"stars": None, "text": "no rating"},
    ]
    out = R.build_theme_input(reviews, max_texts=2)
    assert out[0] == "terrible"      # 1★ first
    assert out[1] == "meh"           # 3★ next


# ── Throttling must not look like "this business has no reviews" ─────────────

class _FakeLoc:
    def __init__(self, n=0, text=""):
        self._n, self._text = n, text
    @property
    def first(self):
        return self
    async def count(self):
        return self._n
    async def inner_text(self):
        return self._text
    async def click(self, **kw):
        raise AssertionError("should not be clicked in these tests")


class _FakePage:
    """Only implements locator(); enough for the pure detection logic."""
    def __init__(self, mapping):
        self._m = mapping
    def locator(self, sel):
        return self._m.get(sel, _FakeLoc(0))


def test_missing_review_count_is_read_as_throttling():
    """A real place panel renders "4.9\n(418)". A throttled one renders just
    "4.9". Without this distinction a soft-block is indistinguishable from a
    business genuinely having no reviews, and the pipeline stores a false
    negative on every lead — which is exactly what a live run did."""
    import asyncio
    from backend.services.review_scraper import reviews_look_unavailable

    throttled = _FakePage({"div.F7nice": _FakeLoc(1, "4.9")})
    assert asyncio.run(reviews_look_unavailable(throttled)) is True

    healthy = _FakePage({"div.F7nice": _FakeLoc(1, "4.9\n(418)")})
    assert asyncio.run(reviews_look_unavailable(healthy)) is False

    # comma-formatted counts are still counts
    big = _FakePage({"div.F7nice": _FakeLoc(1, "4.6\n(1,580)")})
    assert asyncio.run(reviews_look_unavailable(big)) is False

    # no header block at all -> cannot trust the page
    assert asyncio.run(reviews_look_unavailable(_FakePage({}))) is True


def test_harvest_reports_unavailable_rather_than_empty():
    """The dict must carry the distinction to the caller."""
    import asyncio
    from backend.services.review_scraper import harvest_reviews

    page = _FakePage({"div.F7nice": _FakeLoc(1, "4.9")})   # rating, no count
    out = asyncio.run(harvest_reviews(page))
    assert out["reviews"] == []
    assert out["unavailable"] is True, "an empty harvest must say WHY it was empty"
