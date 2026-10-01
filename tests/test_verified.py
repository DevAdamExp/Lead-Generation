"""Tests for the _is_verified predicate (the 'what counts as a delivered lead' rule)."""
from unittest.mock import MagicMock

from backend.workers.pipeline import _is_verified, _is_accurate


def mk(**kw):
    l = MagicMock()
    l.lead_score = 0
    l.phone_verified = False
    l.email_verified = False
    l.has_google_maps = False
    l.data_confidence = 1.0
    l.sources = None
    for k, v in kw.items():
        setattr(l, k, v)
    return l


def test_score_at_threshold_verifies():
    assert _is_verified(mk(lead_score=40))


def test_score_below_threshold_not_verified():
    assert not _is_verified(mk(lead_score=39))


def test_none_score_not_verified():
    assert not _is_verified(mk(lead_score=None))


def test_phone_plus_maps_verifies():
    assert _is_verified(mk(phone_verified=True, has_google_maps=True))


def test_phone_without_maps_not_verified():
    assert not _is_verified(mk(phone_verified=True, has_google_maps=False))


def test_npi_plus_phone_verifies():
    assert _is_verified(mk(phone_verified=True, sources="google_maps,npi"))


def test_npi_without_phone_not_verified():
    assert not _is_verified(mk(phone_verified=False, sources="npi"))


def test_no_signals_not_verified():
    assert not _is_verified(mk())


# ── Accuracy gate (final export filter) ───────────────────────────────────────

def test_accurate_requires_confidence_floor():
    # Verified phone but confidence below the 0.5 floor → not export-accurate.
    assert not _is_accurate(mk(phone_verified=True, data_confidence=0.3))


def test_accurate_with_verified_phone():
    assert _is_accurate(mk(phone_verified=True, data_confidence=0.6))


def test_accurate_with_verified_email():
    assert _is_accurate(mk(email_verified=True, data_confidence=0.7))


def test_accurate_via_multisource_maps():
    # No verified contact, but on Maps AND confirmed by 2 sources → accurate.
    assert _is_accurate(mk(has_google_maps=True, sources="google_maps,hotfrog",
                           data_confidence=0.6))


def test_not_accurate_single_source_no_verified_contact():
    assert not _is_accurate(mk(has_google_maps=True, sources="hotfrog",
                               data_confidence=0.9))
