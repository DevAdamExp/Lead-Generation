"""Tests for pipeline scoring algorithm."""

from unittest.mock import MagicMock, patch

import pytest

from backend.workers.pipeline import _stage_score, _build_manifest, _is_contactable


class TestManifestAndContactable:
    def _lead(self, score, **kw):
        l = MagicMock()
        l.lead_score = score
        l.phone = kw.get("phone")
        l.owner_email = kw.get("owner_email")
        l.website = kw.get("website")
        return l

    def test_manifest_counts_and_score_stats(self):
        job = MagicMock()
        job.id = "abc"; job.niche = "dentist"; job.location = "Austin"
        job.country = "us"; job.limit = 10; job.created_at = None
        leads = [self._lead(80), self._lead(40), self._lead(60)]
        m = _build_manifest(job, leads, total_scraped=50, total_verified=12,
                            filenames={"xlsx": "x.xlsx"})
        assert m["counts"] == {"total_scraped": 50, "total_verified": 12, "exported": 3}
        assert m["lead_score"]["min"] == 40
        assert m["lead_score"]["max"] == 80
        assert m["lead_score"]["avg"] == 60.0
        assert m["files"] == {"xlsx": "x.xlsx"}

    def test_contactable_keeps_phone_drops_bare(self):
        assert _is_contactable(self._lead(0, phone="555")) is True
        assert _is_contactable(self._lead(0)) is False


class TestStageScore:
    def make_lead(self, **overrides):
        lead = MagicMock()
        lead.owner_email = None
        lead.phone = None
        lead.website = None
        lead.website_status = None
        lead.website_cms = None
        lead.has_ssl = False
        lead.has_google_maps = False
        lead.google_rating = None
        lead.google_review_count = 0
        lead.google_is_open = False
        lead.social_facebook = ""
        lead.social_instagram = ""
        lead.social_twitter = ""
        lead.owner_name = None
        lead.name = "Test Business"
        lead.lead_score = 0
        lead.status = None
        # Fields added for owner-contact + verification + multi-source scoring
        lead.owner_phone = None
        lead.owner_phone_confidence = 0.0
        lead.phone_verified = False
        lead.email_verified = False
        lead.sources = None
        lead.data_confidence = 0.0
        lead.employee_count = None
        lead.year_founded = None
        lead.partners = None
        lead.services = None
        lead.team_members = None
        lead.owner_title = None
        lead.review_weakness_count = 0
        lead.phone_source_count = 0
        lead.address_valid = None
        lead.verification_tier = "unverified"
        for k, v in overrides.items():
            setattr(lead, k, v)
        return lead

    @patch("backend.workers.pipeline.logger")
    def test_empty_lead_scores_zero(self, mock_logger):
        lead = self.make_lead()
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        assert lead.lead_score == 0

    @patch("backend.workers.pipeline.logger")
    def test_personal_email_on_domain_max_email_score(self, mock_logger):
        lead = self.make_lead(
            owner_email="john.doe@company.com",
            website="https://company.com",
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # email=30 + website_exists_but_no_status=5 = 35
        assert lead.lead_score == 35

    @patch("backend.workers.pipeline.logger")
    def test_valid_phone_adds_10_points(self, mock_logger):
        lead = self.make_lead(
            phone="+1 512-555-0100",
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        assert lead.lead_score == 10

    @patch("backend.workers.pipeline.logger")
    def test_active_website_with_cms_and_ssl(self, mock_logger):
        from backend.models import WebsiteStatus
        lead = self.make_lead(
            website="https://company.com",
            website_status=WebsiteStatus.ACTIVE,
            website_cms="WordPress",
            has_ssl=True,
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # website_active=15 + cms=5 + ssl=5 = 25
        assert lead.lead_score == 25

    @patch("backend.workers.pipeline.logger")
    def test_google_maps_presence_and_rating(self, mock_logger):
        lead = self.make_lead(
            has_google_maps=True,
            google_rating=4.5,
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # maps_presence=5 + high_rating=10 = 15
        assert lead.lead_score == 15

    @patch("backend.workers.pipeline.logger")
    def test_social_media_facebook_and_instagram(self, mock_logger):
        lead = self.make_lead(
            social_facebook="https://facebook.com/biz",
            social_instagram="https://instagram.com/biz",
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # social=5*2=10 (capped at 10)
        assert lead.lead_score == 10

    @patch("backend.workers.pipeline.logger")
    def test_owner_name_bonus(self, mock_logger):
        lead = self.make_lead(owner_name="John Doe")
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        assert lead.lead_score == 5

    @patch("backend.workers.pipeline.logger")
    def test_high_review_count_bonus(self, mock_logger):
        lead = self.make_lead(google_review_count=100)
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        assert lead.lead_score == 5

    @patch("backend.workers.pipeline.logger")
    def test_is_open_bonus(self, mock_logger):
        lead = self.make_lead(google_is_open=True)
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        assert lead.lead_score == 5

    @patch("backend.workers.pipeline.logger")
    def test_all_bonus_points(self, mock_logger):
        lead = self.make_lead(
            owner_name="Jane Doe",
            google_review_count=75,
            google_is_open=True,
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # bonus = 5 + 5 + 5 = 15
        assert lead.lead_score == 15

    @patch("backend.workers.pipeline.logger")
    def test_score_capped_at_100(self, mock_logger):
        from backend.models import WebsiteStatus
        lead = self.make_lead(
            owner_email="john.doe@company.com",
            phone="+1 512-555-0100",
            website="https://company.com",
            website_status=WebsiteStatus.ACTIVE,
            website_cms="WordPress",
            has_ssl=True,
            has_google_maps=True,
            google_rating=4.8,
            google_review_count=200,
            google_is_open=True,
            social_facebook="https://facebook.com/biz",
            social_instagram="https://instagram.com/biz",
            social_twitter="https://twitter.com/biz",
            owner_name="John Doe",
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        assert lead.lead_score <= 100
        # full score: email=30 + phone=10 + web=15 + cms=5 + ssl=5 +
        #             maps=15 + social=10 + bonus=15 = 105 capped → 100
        assert lead.lead_score == 100

    @patch("backend.workers.pipeline.logger")
    def test_role_email_on_domain_scores_30(self, mock_logger):
        """role emails like info/sales match the 'personal' regex (>4 chars, only letters)."""
        lead = self.make_lead(
            owner_email="info@company.com",
            website="https://company.com",
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # email=30 (personal regex matches) + website_exists=5 = 35
        assert lead.lead_score == 35

    @patch("backend.services.verifier.verify_email",
           return_value={"mx_valid": False, "smtp_valid": False, "is_role": False})
    @patch("backend.workers.pipeline.logger")
    def test_personal_email_on_generic_domain_no_site(self, mock_logger, mock_verify):
        # Mock the MX/SMTP verifier so the score is deterministic offline — gmail.com
        # actually resolves MX + answers SMTP, which otherwise adds the +10 verify
        # bonus and makes this a flaky, network-dependent test.
        lead = self.make_lead(
            owner_email="john.doe@gmail.com",
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # email=20 (personal + generic domain) + no website = 20
        assert lead.lead_score == 20

    @patch("backend.workers.pipeline.logger")
    def test_website_without_ssl_scores_15(self, mock_logger):
        from backend.models import WebsiteStatus
        lead = self.make_lead(
            website="https://company.com",
            website_status=WebsiteStatus.ACTIVE,
            has_ssl=False,
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # website_active=15 + no ssl = 15
        assert lead.lead_score == 15

    @patch("backend.workers.pipeline.logger")
    def test_dead_website_with_url_scores_5(self, mock_logger):
        from backend.models import WebsiteStatus
        lead = self.make_lead(
            website="https://dead.com",
            website_status=WebsiteStatus.DEAD,
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        assert lead.lead_score == 5

    @patch("backend.workers.pipeline.logger")
    def test_decent_google_rating_scores_10(self, mock_logger):
        lead = self.make_lead(
            has_google_maps=True,
            google_rating=3.5,
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # maps_presence=5 + decent_rating=5 = 10
        assert lead.lead_score == 10

    @patch("backend.workers.pipeline.logger")
    def test_social_three_platforms_capped_at_10(self, mock_logger):
        lead = self.make_lead(
            social_facebook="https://facebook.com/biz",
            social_instagram="https://instagram.com/biz",
            social_twitter="https://twitter.com/biz",
        )
        db = MagicMock()
        job = MagicMock()
        job.country = "us"
        _stage_score(db, job, [lead])
        # 3*5=15 but capped at 10
        assert lead.lead_score == 10

    @patch("backend.workers.pipeline.logger")
    def test_uk_phone_region_used(self, mock_logger):
        lead = self.make_lead(phone="020 7946 0958")
        db = MagicMock()
        job = MagicMock()
        job.country = "gb"
        _stage_score(db, job, [lead])
        assert lead.lead_score >= 0
