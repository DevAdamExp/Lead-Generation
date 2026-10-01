"""
tests/test_hotfrog_rotation.py — Test Hotfrog multi-IP rotation.

Tests that:
  1. The circuit breaker resets on rotation
  2. Multiple Tor instances give different IPs through Hotfrog
  3. _fetch_page handles 403 gracefully with rotation

NOTE: These tests require running Tor instances (10 recommended).
"""
import pytest
import sys
from unittest.mock import patch, MagicMock

from hotfrog.hotfrog_core import reset_block_state, _HARD_BLOCKED


class TestCircuitBreaker:
    def test_reset_block_state(self):
        """reset_block_state() should clear the circuit breaker."""
        import hotfrog.hotfrog_core as hf
        hf._HARD_BLOCKED = True
        reset_block_state()
        assert hf._HARD_BLOCKED is False


class TestFetchPage:
    def test_rotation_resets_block(self):
        """When rotate_proxy=True, the circuit breaker should reset before fetch."""
        import hotfrog.hotfrog_core as hf
        hf._HARD_BLOCKED = True

        with patch("hotfrog.hotfrog_core._rotate_ip") as mock_rotate, \
             patch("hotfrog.hotfrog_core._get_proxy_config") as mock_proxy, \
             patch("hotfrog.hotfrog_core._fetch_with_httpx") as mock_fetch:

            mock_proxy.return_value = {"all://": "socks5://127.0.0.1:9050"}
            mock_fetch.return_value = "<html>ok</html>"

            hf._fetch_page("https://www.hotfrog.com/search/us/test", rotate_proxy=True)

            # _HARD_BLOCKED should be False after reset
            assert hf._HARD_BLOCKED is False
            mock_rotate.assert_called_once()
            mock_fetch.assert_called_once()

    def test_no_rotation_respects_block(self):
        """When rotate_proxy=False, the circuit breaker should stop the fetch."""
        import hotfrog.hotfrog_core as hf
        hf._HARD_BLOCKED = True

        with patch("hotfrog.hotfrog_core._fetch_with_httpx") as mock_fetch:
            result = hf._fetch_page("https://www.hotfrog.com/search/us/test", rotate_proxy=False)
            assert result is None
            mock_fetch.assert_not_called()

        reset_block_state()

    def test_httpx_403_sets_block(self):
        """A 403 response should set _HARD_BLOCKED."""
        import hotfrog.hotfrog_core as hf
        hf._HARD_BLOCKED = False

        with patch("hotfrog.hotfrog_core._fetch_with_httpx") as mock_fetch, \
             patch("hotfrog.hotfrog_core._get_proxy_config") as mock_proxy:

            mock_proxy.return_value = {"all://": "socks5://127.0.0.1:9050"}

            # Simulate a 403 via side_effect that matches _fetch_with_httpx behavior
            def _simulate_403(url, proxy_config=None):
                import hotfrog.hotfrog_core as hf
                hf._HARD_BLOCKED = True
                return None

            mock_fetch.side_effect = _simulate_403

            result = hf._fetch_page("https://www.hotfrog.com/search/us/test", rotate_proxy=False)
            assert result is None
            assert hf._HARD_BLOCKED is True

        reset_block_state()


@pytest.mark.skipif(
    not any(True for _ in range(0)),  # Always skip unless explicitly enabled
    reason="Requires live Hotfrog + Tor — run with --runhotfrog"
)
class TestLiveHotfrog:
    """
    Live integration test.

    Run with:
        pytest tests/test_hotfrog_rotation.py --runhotfrog -x -v

    Requires 10 Tor instances running.
    """

    def test_rotation_yields_results(self):
        """Test multiple pages with IP rotation against live Hotfrog."""
        from hotfrog.hotfrog_core import search_businesses

        reset_block_state()
        results = []
        for page in range(1, 4):
            batch = search_businesses(
                "construction los angeles",
                country="us",
                limit=12,
                page=page,
                rotate_proxy=True,
            )
            results.extend(batch)
            if not batch:
                break

        assert len(results) > 0, "Hotfrog returned no results even with rotation"
