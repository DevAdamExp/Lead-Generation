"""Tests for registration lookup — pure parsing + mocked HTTP, no network."""
from unittest.mock import MagicMock, patch

from backend.services import registration as reg


def test_norm_status():
    assert reg._norm_status("Active (Good Standing)") == "active"
    assert reg._norm_status("DISSOLVED") == "dissolved"
    assert reg._norm_status("Revoked") == "dissolved"
    assert reg._norm_status("Suspended") == "inactive"
    assert reg._norm_status(None) is None


def test_parse_sam():
    entity = {"entityRegistration": {"legalBusinessName": "ACME DENTAL LLC",
                                     "registrationStatus": "Active",
                                     "registrationDate": "2018-05-01"},
              "coreData": {"entityInformation": {"entityStructureDesc": "LLC"}}}
    out = reg._parse_sam(entity)
    assert out["legal_name"] == "ACME DENTAL LLC"
    assert out["entity_status"] == "active"
    assert out["entity_type"] == "LLC"
    assert out["registry_source"] == "sam_gov"


def test_sam_gov_lookup_no_key_returns_none():
    assert reg.sam_gov_lookup("Acme", "") is None


def _resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


def test_opencorporates_lookup_parses_first_match():
    payload = {"results": {"companies": [
        {"company": {"name": "ACME DENTAL LLC", "company_type": "LLC",
                     "current_status": "Active", "incorporation_date": "2018-05-01"}},
    ]}}
    with patch.object(reg.httpx, "get", return_value=_resp(payload)):
        out = reg.opencorporates_lookup("Acme Dental", state="ca")
    assert out["legal_name"] == "ACME DENTAL LLC"
    assert out["entity_status"] == "active"
    assert out["registry_source"] == "opencorporates"


def test_lookup_registration_off_by_default(monkeypatch):
    # Even with a matching source, disabled flag → None.
    from backend.config import settings
    monkeypatch.setattr(settings, "ENABLE_REGISTRATION_LOOKUP", False, raising=False)
    assert reg.lookup_registration("Acme", "ca") is None


def test_sos_lookup_is_noop_extension_point():
    assert reg.sos_lookup("Acme", "ca") is None
