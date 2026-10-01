"""
tests/test_verifier.py — Test email + phone verification.
"""
from unittest.mock import patch

import pytest
from backend.services import verifier
from backend.services.verifier import (
    verify_email, verify_phone, verify_batch, clear_cache,
)


class TestVerifyEmail:
    def test_invalid_format(self):
        r = verify_email("not-an-email")
        assert r["format_valid"] is False
        assert r["score"] == 0

    def test_empty(self):
        r = verify_email("")
        assert r["format_valid"] is False

    def test_none(self):
        r = verify_email(None)  # type: ignore
        assert r["format_valid"] is False

    def test_role_detection(self):
        r = verify_email("info@example.com")
        assert r["format_valid"] is True
        assert r["is_role"] is True

        r = verify_email("john.doe@example.com")
        assert r["format_valid"] is True
        assert r["is_role"] is False

    def test_free_domain_detection(self):
        r = verify_email("user@gmail.com")
        assert r["is_free_domain"] is True

        r = verify_email("ceo@acmecorp.com")
        assert r["is_free_domain"] is False

    def test_mx_lookup(self):
        gmail = verify_email("test@gmail.com")
        assert gmail["mx_valid"] is True
        assert gmail["score"] >= 5

    def test_bogus_domain(self):
        r = verify_email("user@thisshouldnotexist99999999.com")
        assert r["mx_valid"] is False

    def test_cache_hit(self):
        clear_cache()
        r1 = verify_email("a@gmail.com")
        r2 = verify_email("b@gmail.com")
        assert r1["mx_valid"] == r2["mx_valid"]


class TestEmailProbe:
    """RCPT probe / catch-all / tier logic — fully mocked, no network."""

    def setup_method(self):
        clear_cache()

    def test_deliverable_mailbox_is_verified(self):
        with patch.object(verifier, "_resolve_mx", return_value=["mx.acme.com"]), \
             patch.object(verifier, "_rcpt_code", side_effect=[250, 550]):
            # real address → 250 (accepted); random catch-all probe → 550 (rejected)
            r = verify_email("owner@acme.com")
        assert r["deliverable"] is True
        assert r["catch_all"] is False
        assert r["verified"] is True
        assert r["tier"] == "verified"

    def test_catch_all_is_not_verified(self):
        with patch.object(verifier, "_resolve_mx", return_value=["mx.acme.com"]), \
             patch.object(verifier, "_rcpt_code", return_value=250):
            # both the real address AND the random probe get 250 → catch-all
            r = verify_email("owner@acme.com")
        assert r["catch_all"] is True
        assert r["deliverable"] is None
        assert r["verified"] is False
        assert r["tier"] == "catch_all"

    def test_dead_mailbox_undeliverable(self):
        with patch.object(verifier, "_resolve_mx", return_value=["mx.acme.com"]), \
             patch.object(verifier, "_rcpt_code", return_value=550):
            r = verify_email("ghost@acme.com")
        assert r["deliverable"] is False
        assert r["verified"] is False
        assert r["tier"] == "undeliverable"

    def test_port25_blocked_falls_back_to_mx_only(self):
        with patch.object(verifier, "_resolve_mx", return_value=["mx.acme.com"]), \
             patch.object(verifier, "_rcpt_code", return_value=None):
            r = verify_email("owner@acme.com")
        assert r["mx_valid"] is True
        assert r["verified"] is True          # MX is the best signal available
        assert r["tier"] == "mx_only"

    def test_no_mx_is_invalid(self):
        with patch.object(verifier, "_resolve_mx", return_value=[]):
            r = verify_email("owner@nope-nxdomain.test")
        assert r["mx_valid"] is False
        assert r["verified"] is False
        assert r["tier"] == "invalid"

    def test_result_cached_no_second_probe(self):
        with patch.object(verifier, "_resolve_mx", return_value=["mx.acme.com"]), \
             patch.object(verifier, "_rcpt_code", side_effect=[250, 550]) as probe:
            verify_email("owner@acme.com")
            verify_email("owner@acme.com")  # second call must hit the cache
        assert probe.call_count == 2  # only the first verify probed (real + catch-all)


class TestVerifyAddress:
    """Census geocoder — mocked HTTP, no network."""

    def setup_method(self):
        clear_cache()

    def _resp(self, matches):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"result": {"addressMatches": matches}}
        return r

    def test_matched_address_true(self):
        with patch.object(verifier.httpx, "get", return_value=self._resp([{"x": 1}])):
            assert verifier.verify_address("1600 Pennsylvania Ave NW, Washington DC") is True

    def test_unmatched_address_false(self):
        with patch.object(verifier.httpx, "get", return_value=self._resp([])):
            assert verifier.verify_address("nowhere at all") is False

    def test_empty_address_none(self):
        assert verifier.verify_address("") is None

    def test_network_error_none(self):
        with patch.object(verifier.httpx, "get", side_effect=Exception("boom")):
            assert verifier.verify_address("123 Main St") is None


class TestVerifyPhone:
    def test_valid_us_number(self):
        r = verify_phone("(212) 555-1234")
        assert r["valid"] is True
        assert r["type"] in ("fixed_line", "fixed_line_or_mobile")
        assert r["country_code"] == 1
        assert r["national"] == "(212) 555-1234"

    def test_invalid_number(self):
        r = verify_phone("555-1234")
        assert r["valid"] is False
        assert r["score"] <= 2

    def test_empty(self):
        r = verify_phone("")
        assert r["valid"] is False

    def test_mobile_detection(self):
        r = verify_phone("+14155551234")
        assert r["valid"] is True
        assert r["type"] in ("mobile", "fixed_line_or_mobile")

    def test_toll_free(self):
        r = verify_phone("+18005551234")
        assert r["valid"] is True
        assert r["type"] == "toll_free"

    def test_area_code_ny(self):
        r = verify_phone("+12125551234")
        assert r["state"] == "NY"

    def test_area_code_tx(self):
        r = verify_phone("+17135551234")
        assert r["state"] == "TX"

    def test_area_code_fl(self):
        r = verify_phone("+13055551234")
        assert r["state"] == "FL"

    def test_uk_number(self):
        r = verify_phone("+44 20 7946 0958", region="GB")
        assert r["valid"] is True
        assert r["country_code"] == 44


class TestBatch:
    def test_email_batch(self):
        r = verify_batch(emails=["test@gmail.com", "nope@thisshouldnotexist99999.com"])
        assert r["emails"]["total"] == 2

    def test_phone_batch(self):
        r = verify_batch(phones=["+14155551234", "(212) 555-1234"])
        assert r["phones"]["total"] == 2
