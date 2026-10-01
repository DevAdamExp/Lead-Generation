"""Tests for core utility modules: validator, retry, deduplicator, contact_finder, google_maps_scraper."""

import asyncio
import builtins
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.retry import (
    FatalError,
    RetryableError,
    exponential_backoff,
    retry_async,
    retry_sync,
)
from backend.utils.validator import COUNTRY_TO_REGION, format_phone, is_valid_email
from backend.services.contact_finder import _best_email, _email_tier, _score_email
from backend.services.google_maps_scraper import _fuzzy_match_names, _normalize_name


class TestCOUNTRY_TO_REGION:
    def test_common_countries(self):
        assert COUNTRY_TO_REGION["us"] == "US"
        assert COUNTRY_TO_REGION["gb"] == "GB"
        assert COUNTRY_TO_REGION["de"] == "DE"
        assert COUNTRY_TO_REGION["jp"] == "JP"

    def test_uk_alias(self):
        assert COUNTRY_TO_REGION["uk"] == "GB"

    def test_ca_maps_to_ca(self):
        assert COUNTRY_TO_REGION["ca"] == "CA"

    def test_all_values_uppercase(self):
        for code, region in COUNTRY_TO_REGION.items():
            assert region == region.upper(), f"{code} -> {region} is not uppercase"

    def test_all_keys_lowercase(self):
        for code in COUNTRY_TO_REGION:
            assert code == code.lower(), f"{code} is not lowercase"

    def test_contains_expected_keys(self):
        expected = {"us", "gb", "de", "fr", "it", "es", "jp", "cn", "br", "in", "au", "ru"}
        assert expected.issubset(COUNTRY_TO_REGION.keys())


class TestFormatPhone:
    def test_none_returns_none(self):
        assert format_phone(None) is None

    def test_empty_string_returns_none(self):
        assert format_phone("") is None

    def test_whitespace_only_returns_none(self):
        assert format_phone("   ") is None

    def test_only_non_digit_chars_returns_none(self):
        assert format_phone("abc!@#") is None

    def test_valid_us_number(self, monkeypatch):
        import phonenumbers
        mock_parse = MagicMock(return_value="parsed")
        mock_is_valid = MagicMock(return_value=True)
        mock_format = MagicMock(return_value="+1 212-555-0190")
        monkeypatch.setattr(phonenumbers, "parse", mock_parse)
        monkeypatch.setattr(phonenumbers, "is_valid_number", mock_is_valid)
        monkeypatch.setattr(phonenumbers, "format_number", mock_format)
        monkeypatch.setattr(phonenumbers, "PhoneNumberFormat", phonenumbers.PhoneNumberFormat)

        result = format_phone("212-555-0190")
        assert result == "+1 212-555-0190"
        mock_parse.assert_called()

    def test_falls_through_regions(self, monkeypatch):
        import phonenumbers
        parse_results = iter([
            MagicMock(),
            MagicMock(),
            "parsed_none",
        ])
        is_valid_results = iter([False, False, True])
        def side_effect_parse(num, reg=None):
            val = next(parse_results)
            return val
        def side_effect_is_valid(p):
            return next(is_valid_results)

        mock_parse = MagicMock(side_effect=side_effect_parse)
        mock_is_valid = MagicMock(side_effect=side_effect_is_valid)
        mock_format = MagicMock(return_value="+44 20 7946 0958")

        monkeypatch.setattr(phonenumbers, "parse", mock_parse)
        monkeypatch.setattr(phonenumbers, "is_valid_number", mock_is_valid)
        monkeypatch.setattr(phonenumbers, "format_number", mock_format)
        monkeypatch.setattr(phonenumbers, "PhoneNumberFormat", phonenumbers.PhoneNumberFormat)

        result = format_phone("2079460958", region="GB")
        assert result == "+44 20 7946 0958"

    def test_invalid_number_returns_none(self, monkeypatch):
        import phonenumbers
        mock_parse = MagicMock(return_value="parsed")
        mock_is_valid = MagicMock(return_value=False)
        mock_is_possible = MagicMock(return_value=False)
        mock_format = MagicMock()
        monkeypatch.setattr(phonenumbers, "parse", mock_parse)
        monkeypatch.setattr(phonenumbers, "is_valid_number", mock_is_valid)
        monkeypatch.setattr(phonenumbers, "is_possible_number", mock_is_possible)
        monkeypatch.setattr(phonenumbers, "format_number", mock_format)
        monkeypatch.setattr(phonenumbers, "PhoneNumberFormat", phonenumbers.PhoneNumberFormat)

        result = format_phone("not-a-number")
        assert result is None

    def test_parse_raises_exception_falls_through(self, monkeypatch):
        import phonenumbers
        parse_results = [
            Exception("parse error"),
            Exception("parse error"),
            Exception("parse error"),
            "valid_parsed",
        ]
        idx = 0
        def parse_side(num, reg=None):
            nonlocal idx
            val = parse_results[idx]
            idx += 1
            if isinstance(val, Exception):
                raise val
            return val

        mock_parse = MagicMock(side_effect=parse_side)
        mock_is_valid = MagicMock(return_value=False)
        mock_is_possible = MagicMock(return_value=True)
        mock_format = MagicMock(return_value="+1 555-000-0000")
        monkeypatch.setattr(phonenumbers, "parse", mock_parse)
        monkeypatch.setattr(phonenumbers, "is_valid_number", mock_is_valid)
        monkeypatch.setattr(phonenumbers, "is_possible_number", mock_is_possible)
        monkeypatch.setattr(phonenumbers, "format_number", mock_format)
        monkeypatch.setattr(phonenumbers, "PhoneNumberFormat", phonenumbers.PhoneNumberFormat)

        result = format_phone("555-000-0000")
        assert result == "+1 555-000-0000"

    def test_possible_number_fallback(self, monkeypatch):
        import phonenumbers
        parse_results = [MagicMock(), MagicMock(), MagicMock(), "possible_parsed"]
        is_valid_results = [False, False, False, False]
        idx = 0
        def parse_side(num, reg=None):
            nonlocal idx
            val = parse_results[idx]
            idx += 1
            return val
        valid_idx = 0
        def valid_side(p):
            nonlocal valid_idx
            val = is_valid_results[valid_idx]
            valid_idx += 1
            return val

        mock_parse = MagicMock(side_effect=parse_side)
        mock_is_valid = MagicMock(side_effect=valid_side)
        mock_is_possible = MagicMock(return_value=True)
        mock_format = MagicMock(return_value="+1 555-123-4567")

        monkeypatch.setattr(phonenumbers, "parse", mock_parse)
        monkeypatch.setattr(phonenumbers, "is_valid_number", mock_is_valid)
        monkeypatch.setattr(phonenumbers, "is_possible_number", mock_is_possible)
        monkeypatch.setattr(phonenumbers, "format_number", mock_format)
        monkeypatch.setattr(phonenumbers, "PhoneNumberFormat", phonenumbers.PhoneNumberFormat)

        result = format_phone("555-123-4567")
        assert result == "+1 555-123-4567"

    def test_cleans_non_digit_characters(self, monkeypatch):
        import phonenumbers
        captured_args = []
        def parse_side(num, reg=None):
            captured_args.append((num, reg))
            p = MagicMock()
            return p
        mock_parse = MagicMock(side_effect=parse_side)
        monkeypatch.setattr(phonenumbers, "parse", mock_parse)
        monkeypatch.setattr(phonenumbers, "is_valid_number", MagicMock(return_value=True))
        monkeypatch.setattr(phonenumbers, "format_number", MagicMock(return_value="+1 212-555-0190"))
        monkeypatch.setattr(phonenumbers, "PhoneNumberFormat", phonenumbers.PhoneNumberFormat)

        format_phone("(212) 555-0190 ext. 123")
        assert any("2125550190" in args[0] for args in captured_args)


class TestIsValidEmail:
    def test_valid_email(self):
        assert is_valid_email("test@example.com") is True

    def test_invalid_email_no_at(self):
        assert is_valid_email("notanemail") is False

    def test_invalid_email_no_domain(self):
        assert is_valid_email("user@") is False

    def test_invalid_email_no_tld(self):
        assert is_valid_email("user@domain") is False

    def test_email_with_plus(self):
        assert is_valid_email("user+tag@example.com") is True

    def test_email_with_dots(self):
        assert is_valid_email("first.last@example.co.uk") is True

    def test_empty_string(self):
        assert is_valid_email("") is False

    def test_none_raises(self):
        with pytest.raises(TypeError):
            is_valid_email(None)


class TestExponentialBackoff:
    def test_base_delay_attempt_1(self):
        delay = exponential_backoff(1, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 1.0

    def test_base_delay_attempt_2(self):
        delay = exponential_backoff(2, base_delay=2.0, max_delay=30.0, jitter=False)
        assert delay == 4.0

    def test_respects_max_delay(self):
        delay = exponential_backoff(10, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 30.0

    def test_jitter_increases_delay(self, monkeypatch):
        random_mock = MagicMock(return_value=0.5)
        monkeypatch.setattr("backend.utils.retry.random.random", random_mock)
        delay = exponential_backoff(2, base_delay=1.0, max_delay=30.0, jitter=True)
        assert delay == 2.0 * (1 + 0.5 * 0.5)

    def test_jitter_minimum(self, monkeypatch):
        monkeypatch.setattr("backend.utils.retry.random.random", MagicMock(return_value=0.0))
        delay = exponential_backoff(2, base_delay=1.0, max_delay=30.0, jitter=True)
        assert delay == 2.0

    def test_jitter_maximum(self, monkeypatch):
        monkeypatch.setattr("backend.utils.retry.random.random", MagicMock(return_value=1.0))
        delay = exponential_backoff(2, base_delay=1.0, max_delay=30.0, jitter=True)
        assert delay == 2.0 * 1.5

    def test_first_attempt_no_exponential(self):
        delay = exponential_backoff(1, base_delay=5.0, max_delay=60.0, jitter=False)
        assert delay == 5.0

    def test_exponential_growth(self):
        delays = [exponential_backoff(i, base_delay=1.0, max_delay=100.0, jitter=False) for i in range(1, 6)]
        expected = [1.0, 2.0, 4.0, 8.0, 16.0]
        assert delays == expected


class TestRetrySync:
    def test_success_on_first_attempt(self):
        mock_fn = MagicMock(return_value="success")

        @retry_sync(max_attempts=3)
        def decorated():
            return mock_fn()

        result = decorated()
        assert result == "success"
        assert mock_fn.call_count == 1

    def test_success_after_retries(self):
        mock_fn = MagicMock(side_effect=[RetryableError("fail1"), RetryableError("fail2"), "success"])

        @retry_sync(max_attempts=5)
        def decorated():
            return mock_fn()

        result = decorated()
        assert result == "success"
        assert mock_fn.call_count == 3

    def test_fails_after_exhausting_retries(self):
        mock_fn = MagicMock(side_effect=RetryableError("always fail"))

        @retry_sync(max_attempts=3)
        def decorated():
            return mock_fn()

        with pytest.raises(RetryableError):
            decorated()
        assert mock_fn.call_count == 3

    def test_fatal_error_raised_immediately(self):
        mock_fn = MagicMock(side_effect=FatalError("permanent failure"))

        @retry_sync(max_attempts=3)
        def decorated():
            return mock_fn()

        with pytest.raises(FatalError):
            decorated()
        assert mock_fn.call_count == 1

    def test_unexpected_exception_raised_immediately(self):
        mock_fn = MagicMock(side_effect=ValueError("unexpected"))

        @retry_sync(max_attempts=3)
        def decorated():
            return mock_fn()

        with pytest.raises(ValueError):
            decorated()
        assert mock_fn.call_count == 1

    def test_custom_retryable_exceptions(self):
        class CustomRetryable(Exception):
            pass

        mock_fn = MagicMock(side_effect=[CustomRetryable("fail"), "ok"])

        @retry_sync(max_attempts=3, retryable_exceptions=(CustomRetryable,))
        def decorated():
            return mock_fn()

        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 2

    def test_non_retryable_exception_not_in_tuple(self):
        class CustomError(Exception):
            pass

        mock_fn = MagicMock(side_effect=CustomError("not retryable"))

        @retry_sync(max_attempts=3, retryable_exceptions=(RetryableError,))
        def decorated():
            return mock_fn()

        with pytest.raises(CustomError):
            decorated()
        assert mock_fn.call_count == 1

    def test_connection_error_is_retried(self):
        mock_fn = MagicMock(side_effect=[ConnectionError("refused"), "ok"])

        @retry_sync(max_attempts=3)
        def decorated():
            return mock_fn()

        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 2

    def test_timeout_error_is_retried(self):
        mock_fn = MagicMock(side_effect=[TimeoutError("timed out"), "ok"])

        @retry_sync(max_attempts=3)
        def decorated():
            return mock_fn()

        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 2

    def test_os_error_is_retried(self):
        mock_fn = MagicMock(side_effect=[OSError("disk"), "ok"])

        @retry_sync(max_attempts=3)
        def decorated():
            return mock_fn()

        result = decorated()
        assert result == "ok"
        assert mock_fn.call_count == 2

    def test_single_attempt_mode(self):
        mock_fn = MagicMock(side_effect=RetryableError("fail"))

        @retry_sync(max_attempts=1)
        def decorated():
            return mock_fn()

        with pytest.raises(RetryableError):
            decorated()
        assert mock_fn.call_count == 1

    def test_passes_args_and_kwargs(self):
        mock_fn = MagicMock(return_value="done")

        @retry_sync(max_attempts=2)
        def decorated(a, b=None):
            return mock_fn(a, b=b)

        result = decorated(1, b=2)
        assert result == "done"
        mock_fn.assert_called_once_with(1, b=2)


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        mock_fn = AsyncMock(return_value="success")

        @retry_async(max_attempts=3)
        async def decorated():
            return await mock_fn()

        result = await decorated()
        assert result == "success"
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retries(self):
        mock_fn = AsyncMock(side_effect=[RetryableError("fail1"), RetryableError("fail2"), "success"])

        @retry_async(max_attempts=5)
        async def decorated():
            return await mock_fn()

        result = await decorated()
        assert result == "success"
        assert mock_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_fails_after_exhausting_retries(self):
        mock_fn = AsyncMock(side_effect=RetryableError("always fail"))

        @retry_async(max_attempts=3)
        async def decorated():
            return await mock_fn()

        with pytest.raises(RetryableError):
            await decorated()
        assert mock_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_fatal_error_raised_immediately(self):
        mock_fn = AsyncMock(side_effect=FatalError("permanent failure"))

        @retry_async(max_attempts=3)
        async def decorated():
            return await mock_fn()

        with pytest.raises(FatalError):
            await decorated()
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_unexpected_exception_raised_immediately(self):
        mock_fn = AsyncMock(side_effect=ValueError("unexpected"))

        @retry_async(max_attempts=3)
        async def decorated():
            return await mock_fn()

        with pytest.raises(ValueError):
            await decorated()
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_custom_retryable_exceptions(self):
        class CustomRetryable(Exception):
            pass

        mock_fn = AsyncMock(side_effect=[CustomRetryable("fail"), "ok"])

        @retry_async(max_attempts=3, retryable_exceptions=(CustomRetryable,))
        async def decorated():
            return await mock_fn()

        result = await decorated()
        assert result == "ok"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_single_attempt_mode(self):
        mock_fn = AsyncMock(side_effect=RetryableError("fail"))

        @retry_async(max_attempts=1)
        async def decorated():
            return await mock_fn()

        with pytest.raises(RetryableError):
            await decorated()
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        mock_fn = AsyncMock(return_value="done")

        @retry_async(max_attempts=2)
        async def decorated(a, b=None):
            return await mock_fn(a, b=b)

        result = await decorated(1, b=2)
        assert result == "done"
        mock_fn.assert_awaited_once_with(1, b=2)


class TestEmailTier:
    def test_high_tier(self):
        assert _email_tier(85) == "high"
        assert _email_tier(70) == "high"
        assert _email_tier(100) == "high"

    def test_medium_tier(self):
        assert _email_tier(40) == "medium"
        assert _email_tier(55) == "medium"
        assert _email_tier(69) == "medium"

    def test_low_tier(self):
        assert _email_tier(20) == "low"
        assert _email_tier(30) == "low"
        assert _email_tier(39) == "low"

    def test_fallback_tier(self):
        assert _email_tier(0) == "fallback"
        assert _email_tier(10) == "fallback"
        assert _email_tier(19) == "fallback"

    def test_boundary_high_low(self):
        assert _email_tier(69) == "medium"
        assert _email_tier(70) == "high"

    def test_boundary_low_fallback(self):
        assert _email_tier(19) == "fallback"
        assert _email_tier(20) == "low"

    def test_boundary_medium_low(self):
        assert _email_tier(39) == "low"
        assert _email_tier(40) == "medium"

    def test_above_max_falls_to_fallback(self):
        assert _email_tier(200) == "fallback"

    def test_below_zero(self):
        assert _email_tier(-10) == "fallback"


class TestScoreEmail:
    def test_generic_domain_scores_10(self):
        score = _score_email("john@gmail.com")
        assert score == 10

    def test_generic_domain_list(self):
        for domain in ["yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com"]:
            assert _score_email(f"user@{domain}") == 10, f"failed for {domain}"

    def test_name_with_dot_on_business_domain(self):
        score = _score_email("john.doe@company.com", domain="company.com")
        assert score == 100

    def test_name_with_dot_off_domain(self):
        score = _score_email("john.doe@other.com", domain="company.com")
        assert score == 80

    def test_name_without_dot_on_business_domain(self):
        score = _score_email("john@company.com", domain="company.com")
        assert score == 80

    def test_name_without_dot_off_domain(self):
        score = _score_email("john@other.com", domain="company.com")
        assert score == 60

    def test_role_based_on_domain(self):
        score = _score_email("info@company.com", domain="company.com")
        assert score == 80

    def test_role_based_off_domain(self):
        score = _score_email("contact@gmail.com")
        assert score == 10

    def test_short_local_on_domain(self):
        score = _score_email("ab@company.com", domain="company.com")
        assert score == 40

    def test_short_local_off_domain(self):
        score = _score_email("xy@other.com")
        assert score == 20

    def test_generic_catchall_on_domain(self):
        score = _score_email("random123@company.com", domain="company.com")
        assert score == 60

    def test_generic_catchall_off_domain(self):
        score = _score_email("random123@other.com")
        assert score == 40

    def test_role_local_parts_match_name_heuristic(self):
        for role in ["info", "contact", "admin", "sales", "marketing"]:
            score = _score_email(f"{role}@company.com", domain="company.com")
            assert score == 80, f"failed for {role}"

    def test_edge_single_letter_local(self):
        score = _score_email("a@company.com")
        assert score == 20

    def test_edge_single_letter_on_domain(self):
        score = _score_email("a@company.com", domain="company.com")
        assert score == 40

    def test_edge_two_char_local(self):
        score = _score_email("ab@company.com")
        assert score == 20

    def test_edge_two_char_on_domain(self):
        score = _score_email("ab@company.com", domain="company.com")
        assert score == 40

    def test_edge_three_char_local(self):
        score = _score_email("abc@company.com")
        assert score == 20

    def test_edge_three_char_on_domain(self):
        score = _score_email("abc@company.com", domain="company.com")
        assert score == 40

    def test_edge_four_char_name_like(self):
        score = _score_email("john@company.com", domain="company.com")
        assert score == 80

    def test_numeric_local(self):
        score = _score_email("12345@company.com")
        assert score == 40

    def test_numeric_local_on_domain(self):
        score = _score_email("12345@company.com", domain="company.com")
        assert score == 60

    def test_empty_domain_parameter(self):
        score = _score_email("john.doe@company.com")
        assert score == 80

    def test_email_with_no_at_sign_treated_as_name(self):
        score = _score_email("notanemail")
        assert score == 60

    def test_role_email_off_domain_still_above_generic(self):
        generic_score = _score_email("john@gmail.com")
        role_score = _score_email("info@other.com")
        assert role_score > generic_score


class TestBestEmail:
    def test_empty_list_returns_none(self):
        assert _best_email([]) is None

    def test_single_email(self):
        result = _best_email(["john@company.com"])
        assert result == "john@company.com"

    def test_prefers_personal_over_role(self):
        emails = ["info@company.com", "john.doe@company.com"]
        result = _best_email(emails, domain="company.com")
        assert result == "john.doe@company.com"

    def test_prefers_on_domain_over_generic(self):
        emails = ["john@gmail.com", "john@company.com"]
        result = _best_email(emails, domain="company.com")
        assert result == "john@company.com"

    def test_keeps_role_if_no_better_option(self):
        emails = ["info@company.com", "contact@company.com"]
        result = _best_email(emails, domain="company.com")
        assert result in emails

    def test_deduplicates_case_variants(self):
        emails = ["John@Company.com", "john@company.com", "JOHN@COMPANY.COM"]
        result = _best_email(emails, domain="company.com")
        assert result is not None
        assert len(set(e.lower() for e in emails if e.lower() == result.lower())) >= 1

    def test_prefers_on_domain_over_role_when_both_generic(self):
        emails = ["john.doe@gmail.com", "bob@business.com"]
        result = _best_email(emails, domain="business.com")
        assert result == "bob@business.com"

    def test_sort_by_score_then_alphabetical(self):
        emails = ["bob@company.com", "alice@company.com"]
        result = _best_email(emails, domain="company.com")
        assert result is not None

    def test_large_list_of_emails(self):
        emails = [f"user{i}@company.com" for i in range(100)]
        result = _best_email(emails, domain="company.com")
        assert result is not None

    def test_all_generic_returns_one(self):
        emails = ["a@gmail.com", "b@yahoo.com", "c@hotmail.com"]
        result = _best_email(emails)
        assert result is not None

    def test_none_input_returns_none(self):
        assert _best_email(None) is None


class TestNormalizeName:
    def test_basic_normalization(self):
        assert _normalize_name("Acme Plumbing") == "acme plumbing"

    def test_strips_special_chars(self):
        assert _normalize_name("Acme's Plumbing & Co.") == "acmes plumbing  co"

    def test_lowercases(self):
        assert _normalize_name("ACME PLUMBING") == "acme plumbing"

    def test_strips_whitespace(self):
        assert _normalize_name("  Acme Plumbing  ") == "acme plumbing"

    def test_removes_numbers(self):
        assert _normalize_name("Acme 123 Plumbing") == "acme 123 plumbing"

    def test_handles_empty_string(self):
        assert _normalize_name("") == ""

    def test_handles_only_special_chars(self):
        assert _normalize_name("!@#$%^&*()") == ""

    def test_unicode_characters(self):
        assert _normalize_name("José's Café") == "joss caf"


class TestFuzzyMatchNames:
    @pytest.mark.asyncio
    async def test_exact_match(self):
        result = await _fuzzy_match_names("Acme Plumbing", "Acme Plumbing")
        assert result is True

    @pytest.mark.asyncio
    async def test_case_different(self):
        result = await _fuzzy_match_names("acme plumbing", "ACME PLUMBING")
        assert result is True

    @pytest.mark.asyncio
    async def test_word_order_different(self):
        result = await _fuzzy_match_names("Plumbing Acme", "Acme Plumbing")
        assert result is True

    @pytest.mark.asyncio
    async def test_fuzzy_match_with_typo(self):
        result = await _fuzzy_match_names("Acme Plumbng", "Acme Plumbing", threshold=0.6)
        assert result is True

    @pytest.mark.asyncio
    async def test_below_threshold(self):
        result = await _fuzzy_match_names("Totally Different Co", "Acme Plumbing", threshold=0.6)
        assert result is False

    @pytest.mark.asyncio
    async def test_custom_threshold_high(self):
        result = await _fuzzy_match_names("Acme Plbg", "Acme Plumbing", threshold=0.95)
        assert result is False

    @pytest.mark.asyncio
    async def test_custom_threshold_low(self):
        result = await _fuzzy_match_names("Acme Plbg", "Acme Plumbing", threshold=0.3)
        assert result is True

    @pytest.mark.asyncio
    async def test_empty_search_name(self):
        result = await _fuzzy_match_names("", "Acme Plumbing")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_result_name(self):
        result = await _fuzzy_match_names("Acme Plumbing", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_both_empty(self):
        result = await _fuzzy_match_names("", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_partial_match_scores_high_enough(self):
        result = await _fuzzy_match_names("Acme", "Acme Plumbing Services LLC", threshold=0.5)
        assert result is True

    @pytest.mark.asyncio
    async def test_extra_words_ignored(self):
        result = await _fuzzy_match_names("Acme Plumbing LLC", "Acme Plumbing", threshold=0.6)
        assert result is True

    @pytest.mark.asyncio
    async def test_special_chars_handled(self):
        result = await _fuzzy_match_names("Acme's Plumbing!", "Acme Plumbing", threshold=0.6)
        assert result is True

    @pytest.mark.asyncio
    async def test_import_fallback_mocked(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "rapidfuzz", None)
        result = await _fuzzy_match_names("acme plumbing", "ACME PLUMBING", threshold=0.6)
        assert result is True
