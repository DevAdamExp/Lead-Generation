"""Tests for website_checker: SSL validation, CMS detection, parked domain detection."""

from unittest.mock import MagicMock, patch

import pytest

from backend.services.website_checker import (
    _check_ssl_certificate,
    _detect_parked_page,
    CMS_SIGNATURES,
    PARKED_INDICATORS,
)
from backend.models import WebsiteStatus


class TestDetectParkedPage:
    def test_empty_html(self):
        assert _detect_parked_page("") is False

    def test_parked_indicator_found(self):
        html = "<html><body><h1>This domain is parked</h1></body></html>"
        assert _detect_parked_page(html) is True

    def test_for_sale_indicator(self):
        html = "<html><body>domain is for sale</body></html>"
        assert _detect_parked_page(html) is True

    def test_under_construction(self):
        html = "<html><body>this website is under construction</body></html>"
        assert _detect_parked_page(html) is True

    def test_normal_page(self):
        html = "<html><body><h1>Welcome to our plumbing service</h1></body></html>"
        assert _detect_parked_page(html) is False

    def test_case_insensitive(self):
        html = "<html><body>THIS DOMAIN IS PARKED</body></html>"
        assert _detect_parked_page(html) is True

    def test_hugedomains_indicator(self):
        html = "<html><body>hugedomains.com</body></html>"
        assert _detect_parked_page(html) is True

    def test_coming_soon_indicator(self):
        html = "<html><body>Coming Soon</body></html>"
        assert _detect_parked_page(html) is True

    def test_parked_indicators_list_not_empty(self):
        assert len(PARKED_INDICATORS) >= 10


class TestCheckSSLCertificate:
    def test_connection_refused(self):
        with patch("backend.services.website_checker.socket.create_connection") as mock_conn:
            mock_conn.side_effect = ConnectionRefusedError("refused")
            result = _check_ssl_certificate("example.com")
            assert result["valid"] is False
            assert result["error"] is not None

    def test_timeout(self):
        with patch("backend.services.website_checker.socket.create_connection") as mock_conn:
            import socket
            mock_conn.side_effect = socket.timeout("timed out")
            result = _check_ssl_certificate("example.com")
            assert result["valid"] is False
            assert "timeout" in (result["error"] or "").lower()

    def test_ssl_error(self):
        with patch("backend.services.website_checker.socket.create_connection") as mock_conn:
            mock_conn.side_effect = ConnectionRefusedError()
            result = _check_ssl_certificate("example.com")
            assert result["valid"] is False

    def test_generic_exception(self):
        with patch("backend.services.website_checker.socket.create_connection") as mock_conn:
            mock_conn.side_effect = Exception("unexpected error")
            result = _check_ssl_certificate("example.com")
            assert result["valid"] is False
            assert "unexpected error" in (result["error"] or "")


class TestCMSSignatures:
    def test_wordpress_signatures(self):
        sigs = CMS_SIGNATURES["WordPress"]
        assert len(sigs) >= 4
        assert "wp-content" in sigs
        assert "wp-includes" in sigs

    def test_shopify_signatures(self):
        sigs = CMS_SIGNATURES["Shopify"]
        assert len(sigs) >= 4
        assert "cdn.shopify.com" in sigs

    def test_wix_signatures(self):
        sigs = CMS_SIGNATURES["Wix"]
        assert len(sigs) >= 3

    def test_all_cms_types_present(self):
        expected = {
            "WordPress", "Shopify", "Wix", "Squarespace",
            "Webflow", "Weebly", "GoDaddy", "Joomla",
            "Drupal", "Magento", "PrestaShop", "WixStudio",
        }
        assert expected == set(CMS_SIGNATURES.keys())

    def test_each_cms_has_signatures(self):
        for cms, sigs in CMS_SIGNATURES.items():
            assert len(sigs) >= 2, f"{cms} has fewer than 2 signatures"


class TestWebsiteStatusEnum:
    def test_has_different_statuses(self):
        assert WebsiteStatus.ACTIVE != WebsiteStatus.DEAD
        assert WebsiteStatus.NONE != WebsiteStatus.ACTIVE

    def test_active_string_value(self):
        assert WebsiteStatus.ACTIVE.value == "active"

    def test_dead_string_value(self):
        assert WebsiteStatus.DEAD.value == "dead"

    def test_none_string_value(self):
        assert WebsiteStatus.NONE.value == "none"
