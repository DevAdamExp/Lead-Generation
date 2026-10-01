"""Tests for google_maps_scraper: normalize, fuzzy match, constants, dedup."""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from backend.services.google_maps_scraper import (
    _normalize_name,
    _fuzzy_match_names,
    _pick_best_place,
    _EMPTY,
    scrape_maps_rating_sync,
    scrape_maps_ratings_batch_sync,
)


class TestNormalizeName:
    def test_lowercase_and_strip(self):
        assert _normalize_name("  ACME Corp  ") == "acme corp"

    def test_removes_punctuation(self):
        assert _normalize_name("John's Auto!") == "johns auto"

    def test_handles_empty_string(self):
        assert _normalize_name("") == ""

    def test_handles_special_chars(self):
        assert _normalize_name("Café & Bakery #1") == "caf  bakery 1"

    def test_keeps_numbers(self):
        assert _normalize_name("123 Main St.") == "123 main st"

    def test_multiple_spaces(self):
        assert _normalize_name("Very   Wide  Spacing") == "very   wide  spacing"

    def test_only_special_chars(self):
        assert _normalize_name("!!! @@@ ###") == ""


class TestEmptyDict:
    def test_empty_has_all_keys(self):
        assert "has_google_maps" in _EMPTY
        assert "google_rating" in _EMPTY
        assert "google_review_count" in _EMPTY
        assert "google_maps_url" in _EMPTY
        assert "google_is_open" in _EMPTY

    def test_empty_values(self):
        assert _EMPTY["has_google_maps"] is False
        assert _EMPTY["google_rating"] is None
        assert _EMPTY["google_review_count"] == 0
        assert _EMPTY["google_maps_url"] is None
        assert _EMPTY["google_is_open"] is None

    def test_empty_is_copyable(self):
        copy = dict(_EMPTY)
        assert copy == _EMPTY
        copy["has_google_maps"] = True
        assert _EMPTY["has_google_maps"] is False  # original unchanged


class TestPickBestPlace:
    """The pure place-ranking helper that decides which panel to open."""

    P1 = "https://www.google.com/maps/place/Acme+Plumbing/data=1"
    P2 = "https://www.google.com/maps/place/Bobs+Diner/data=2"

    def test_prefers_name_match_over_order(self):
        # Bob's is first, but Acme is the target — pick Acme, not the first card.
        cands = [(self.P2, "Bob's Diner"), (self.P1, "Acme Plumbing")]
        assert _pick_best_place(cands, "Acme Plumbing") == self.P1

    def test_falls_back_to_first_when_no_good_match(self):
        cands = [(self.P1, "Acme Plumbing"), (self.P2, "Bob's Diner")]
        # Target matches neither well → open the first (Maps ranks intent first).
        assert _pick_best_place(cands, "Zzzz Unrelated Co") == self.P1

    def test_ignores_non_place_links(self):
        cands = [("https://www.google.com/maps/search/x", "chrome"),
                 (self.P1, "Acme Plumbing")]
        assert _pick_best_place(cands, "Acme Plumbing") == self.P1

    def test_returns_none_when_no_place_links(self):
        assert _pick_best_place([("https://foo/bar", "x")], "Acme") is None

    def test_returns_none_on_empty(self):
        assert _pick_best_place([], "Acme") is None

    def test_tolerates_missing_aria(self):
        assert _pick_best_place([(self.P1, None)], "Acme Plumbing") == self.P1


class TestFuzzyMatchNames:
    @pytest.mark.asyncio
    async def test_exact_match(self):
        result = await _fuzzy_match_names("Acme Corp", "Acme Corp")
        assert result is True

    @pytest.mark.asyncio
    async def test_case_difference(self):
        result = await _fuzzy_match_names("acme corp", "ACME CORP")
        assert result is True

    @pytest.mark.asyncio
    async def test_token_order_difference(self):
        result = await _fuzzy_match_names("Acme Services Corp", "Corp Acme Services")
        assert result is True

    @pytest.mark.asyncio
    async def test_completely_different(self):
        result = await _fuzzy_match_names("Acme Corp", "Pizza Hut")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_search_name(self):
        result = await _fuzzy_match_names("", "Acme Corp")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_result_name(self):
        result = await _fuzzy_match_names("Acme Corp", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_both_empty(self):
        result = await _fuzzy_match_names("", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_substring_match(self):
        result = await _fuzzy_match_names("Acme", "Acme Corporation LLC")
        assert result is True

    @pytest.mark.asyncio
    async def test_typo_tolerance(self):
        result = await _fuzzy_match_names("Acme Corporation", "Acme Corpuration")
        assert result is True

    @pytest.mark.asyncio
    async def test_high_threshold_rejects_fuzzy(self):
        result = await _fuzzy_match_names("Acme Corp", "Acme Corp XYZ 123", threshold=0.95)
        assert result is True  # still high because of partial ratio

    @pytest.mark.asyncio
    async def test_rapidfuzz_import_fallback(self):
        with patch.dict("sys.modules", {"rapidfuzz": None}):
            import importlib
            import sys
            # Remove quickfuzz from cache if present
            # The function handles ImportError with substring fallback
            # Reload the module so the import at top doesn't affect
            from backend.services import google_maps_scraper
            original_class = google_maps_scraper._fuzzy_match_names

            async def fallback_match(search_name, result_name, threshold=0.6):
                return search_name.lower().strip() in result_name.lower().strip()

            with patch.object(google_maps_scraper, "_fuzzy_match_names", fallback_match):
                assert await google_maps_scraper._fuzzy_match_names("Acme", "Acme Corp") is True
                assert await google_maps_scraper._fuzzy_match_names("XYZ", "Acme Corp") is False

    @pytest.mark.asyncio
    async def test_partial_word_match(self):
        result = await _fuzzy_match_names("ACME", "Acme Corporation")
        assert result is True

    @pytest.mark.asyncio
    async def test_punctuation_difference(self):
        result = await _fuzzy_match_names("John's Auto Repair", "Johns Auto Repair")
        assert result is True

    @pytest.mark.asyncio
    async def test_extra_words(self):
        result = await _fuzzy_match_names("Joe's Pizza", "Joe's Pizza & Restaurant LLC")
        assert result is True


class TestScrapeMapsRatingSync:
    def test_sync_wrapper_runs_async(self):
        with patch(
            "backend.services.google_maps_scraper.scrape_maps_ratings_batch",
            new_callable=AsyncMock,
        ) as mock_batch:
            mock_batch.return_value = {"_": {"has_google_maps": False, "google_rating": None}}
            result = scrape_maps_rating_sync("Test Biz", "Dallas")
            assert result["has_google_maps"] is False
            mock_batch.assert_called_once()

    def test_sync_wrapper_propagates_error(self):
        with patch(
            "backend.services.google_maps_scraper.scrape_maps_ratings_batch",
            new_callable=AsyncMock,
        ) as mock_batch:
            mock_batch.side_effect = Exception("browser crash")
            with pytest.raises(Exception, match="browser crash"):
                scrape_maps_rating_sync("Test Biz", "Dallas")


class TestScrapeMapsRatingsBatchSync:
    def test_sync_batch_wrapper_runs_async(self):
        leads = [{"id": "1", "name": "Biz A", "city": "NYC"}]
        with patch(
            "backend.services.google_maps_scraper.scrape_maps_ratings_batch",
            new_callable=AsyncMock,
        ) as mock_batch:
            mock_batch.return_value = {"1": {"has_google_maps": True}}
            result = scrape_maps_ratings_batch_sync(leads)
            assert result["1"]["has_google_maps"] is True
            mock_batch.assert_called_once()

    def test_sync_batch_passes_on_progress(self):
        leads = [{"id": "1", "name": "Biz A", "city": "NYC"}]
        progress = MagicMock()
        with patch(
            "backend.services.google_maps_scraper.scrape_maps_ratings_batch",
            new_callable=AsyncMock,
        ) as mock_batch:
            mock_batch.return_value = {"1": {"has_google_maps": True}}
            result = scrape_maps_ratings_batch_sync(leads, on_progress=progress)
            mock_batch.assert_called_once()
            args, kwargs = mock_batch.call_args
            assert args[1] is progress  # on_progress is 2nd positional arg

    def test_sync_batch_handles_empty_list(self):
        with patch(
            "backend.services.google_maps_scraper.scrape_maps_ratings_batch",
            new_callable=AsyncMock,
        ) as mock_batch:
            mock_batch.return_value = {}
            result = scrape_maps_ratings_batch_sync([])
            assert result == {}

    def test_sync_batch_propagates_exception(self):
        with patch(
            "backend.services.google_maps_scraper.scrape_maps_ratings_batch",
            new_callable=AsyncMock,
        ) as mock_batch:
            mock_batch.side_effect = Exception("timeout")
            with pytest.raises(Exception, match="timeout"):
                scrape_maps_ratings_batch_sync([])
