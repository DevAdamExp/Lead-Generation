"""Tests for export folder / download-file naming."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from backend.utils.naming import slugify, export_dirname, export_filebase


def _job(niche, location, jid, dt):
    j = MagicMock()
    j.niche, j.location, j.id, j.created_at = niche, location, jid, dt
    return j


def test_slugify_basic():
    assert slugify("Los Angeles, CA") == "Los_Angeles_CA"
    assert slugify("  Dentist & Ortho!! ") == "Dentist_Ortho"


def test_slugify_empty_falls_back():
    assert slugify("") == "leads"
    assert slugify(None) == "leads"
    assert slugify("!!!") == "leads"


def test_slugify_maxlen():
    assert len(slugify("a" * 100, maxlen=10)) <= 10


def test_export_dirname():
    j = _job("Medical clinic", "Los Angeles, CA", "50e721a7-d141-4e9f",
             datetime(2026, 6, 22, tzinfo=timezone.utc))
    assert export_dirname(j) == "Medical_clinic_Los_Angeles_CA_2026-06-22_50e721a7"


def test_export_dirname_special_chars():
    j = _job("Dentist & Orthodontics!", "Austin, TX", "abcd1234-xyz",
             datetime(2026, 6, 22, tzinfo=timezone.utc))
    assert export_dirname(j) == "Dentist_Orthodontics_Austin_TX_2026-06-22_abcd1234"


def test_export_filebase():
    j = _job("Dentist", "Austin, TX", "abcd1234", datetime(2026, 6, 22, tzinfo=timezone.utc))
    assert export_filebase(j) == "Dentist_Austin_TX_leads"
