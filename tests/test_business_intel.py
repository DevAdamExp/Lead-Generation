"""tests/test_business_intel.py — unit tests for business intelligence extraction."""

from unittest.mock import MagicMock, patch

import pytest

from backend.services.business_intel import (
    _compute_review_weaknesses,
    _extract_about_page_summary,
    _extract_blog_post_titles,
    _extract_employee_regex,
    _extract_employee_schema,
    _extract_meta_description,
    _extract_partners_from_page,
    _extract_recent_activity,
    _extract_schemaorg_description,
    _extract_services_from_html,
    _extract_services_from_schema,
    _extract_team_members,
    _extract_year_from_copyright,
    _extract_year_regex,
    _extract_year_schema,
    _is_owner_title,
    _visible_text,
)


# ── _visible_text ────────────────────────────────────────────────────────────

class TestVisibleText:
    def test_strips_tags(self):
        assert _visible_text("<p>Hello <b>World</b></p>") == "Hello World"

    def test_removes_script_and_style(self):
        html = "<script>var x=1;</script><p>Hi</p><style>.c{}</style>"
        assert _visible_text(html) == "Hi"

    def test_empty_string(self):
        assert _visible_text("") == ""

    def test_only_invisible_tags(self):
        assert _visible_text("<script>x</script><style>y</style>") == ""


# ── _extract_meta_description ────────────────────────────────────────────────

class TestExtractMetaDescription:
    def test_og_description_by_name(self):
        html = '<meta name="og:description" content="Good enough description text that is long enough">'
        assert _extract_meta_description(html) == "Good enough description text that is long enough"

    def test_og_description_by_property(self):
        html = '<meta property="og:description" content="Property based description that is sufficiently long">'
        assert _extract_meta_description(html) == "Property based description that is sufficiently long"

    def test_standard_description(self):
        html = '<meta name="description" content="Standard meta description right here">'
        assert _extract_meta_description(html) == "Standard meta description right here"

    def test_too_short_skipped(self):
        html = '<meta name="description" content="Short">'
        assert _extract_meta_description(html) is None

    def test_no_meta_tags_returns_none(self):
        assert _extract_meta_description("<html><body></body></html>") is None


# ── _extract_schemaorg_description ───────────────────────────────────────────

class TestExtractSchemaorgDescription:
    def test_dict_jsonld(self):
        html = '<script type="application/ld+json">{"description":"Schema description that is definitely long enough"}</script>'
        assert _extract_schemaorg_description(html) == "Schema description that is definitely long enough"

    def test_array_jsonld(self):
        html = '<script type="application/ld+json">[{"name":"X"},{"description":"Array description that is certainly long enough for filtering"}]</script>'
        assert _extract_schemaorg_description(html) == "Array description that is certainly long enough for filtering"

    def test_too_short_returns_none(self):
        html = '<script type="application/ld+json">{"description":"Short"}</script>'
        assert _extract_schemaorg_description(html) is None

    def test_no_jsonld_returns_none(self):
        assert _extract_schemaorg_description("<html></html>") is None


# ── _extract_about_page_summary ──────────────────────────────────────────────

class TestExtractAboutPageSummary:
    def test_returns_longest_weighted_line(self):
        html = "<p>We specialize in providing high quality services to our clients worldwide.</p>"
        result = _extract_about_page_summary(html)
        assert result is not None
        assert len(result) > 40

    def test_returns_none_if_no_long_lines(self):
        html = "<p>Hi</p><p>Yo</p>"
        assert _extract_about_page_summary(html) is None

    def test_custom_max_chars(self):
        # A single paragraph of 60+ chars is returned when within max_chars bound.
        html = "<p>" + "A" * 60 + "</p>"
        result = _extract_about_page_summary(html, max_chars=200)
        assert result is not None


# ── _extract_employee_schema ─────────────────────────────────────────────────

class TestExtractEmployeeSchema:
    def test_regex_pattern(self):
        # JSON-LD with numeric value (string values won't match the regex)
        html = '<script type="application/ld+json">{"numberOfEmployees":150}</script>'
        assert _extract_employee_schema(html) == 150

    def test_microdata_format(self):
        html = '<meta itemprop="numberOfEmployees" content="42">'
        assert _extract_employee_schema(html) == 42

    def test_no_match_returns_none(self):
        assert _extract_employee_schema("<html></html>") is None


# ── _extract_employee_regex ──────────────────────────────────────────────────

class TestExtractEmployeeRegex:
    def test_employees_pattern(self):
        html = "We have 250 employees worldwide"
        count, source = _extract_employee_regex(html, [])
        assert count == 250

    def test_team_of_pattern(self):
        html = "Our team of 35 professionals"
        count, source = _extract_employee_regex(html, [])
        assert count == 35

    def test_out_of_range_skipped(self):
        html = "We have 999999 employees"
        count, source = _extract_employee_regex(html, [])
        assert count is None
        assert source is None

    def test_no_match_returns_none(self):
        assert _extract_employee_regex("<p>Hello</p>", []) == (None, None)


# ── _extract_year_schema ─────────────────────────────────────────────────────

class TestExtractYearSchema:
    def test_founding_date_regex(self):
        html = '<script type="application/ld+json">{"foundingDate":"2005"}</script>'
        assert _extract_year_schema(html) == 2005

    def test_founded_year_regex(self):
        # JSON-LD with foundingDate
        html = '<script type="application/ld+json">{"foundingDate":"1998"}</script>'
        assert _extract_year_schema(html) == 1998

    def test_inline_founding_date(self):
        # Inline attribute
        html = '<div foundingDate="2005">'
        assert _extract_year_schema(html) == 2005

    def test_no_match_returns_none(self):
        assert _extract_year_schema("<html></html>") is None


# ── _extract_year_regex ──────────────────────────────────────────────────────

class TestExtractYearRegex:
    def test_founded_in_pattern(self):
        html = "Founded in 2003, we have grown steadily"
        assert _extract_year_regex(html) == 2003

    def test_established_pattern(self):
        html = "Established 1987"
        assert _extract_year_regex(html) == 1987

    def test_since_pattern(self):
        html = "Serving clients since 2010"
        assert _extract_year_regex(html) == 2010

    def test_median_of_multiple_candidates(self):
        html = "Founded in 2000. Established 1995. Since 2005."
        result = _extract_year_regex(html)
        # candidates sorted: [1995, 2000, 2005] → middle = 2000
        assert result == 2000

    def test_no_match_returns_none(self):
        assert _extract_year_regex("<p>No years here</p>") is None


# ── _extract_year_from_copyright ─────────────────────────────────────────────

class TestExtractYearFromCopyright:
    def test_copyright_range(self):
        html = "Copyright © 2010-2026 Company Inc."
        assert _extract_year_from_copyright(html) == 2010

    def test_single_year_not_matched(self):
        html = "Copyright © 2024 Company Inc."
        assert _extract_year_from_copyright(html) is None

    def test_no_copyright_returns_none(self):
        assert _extract_year_from_copyright("<p>Hello</p>") is None


# ── _is_owner_title ──────────────────────────────────────────────────────────

class TestIsOwnerTitle:
    def test_ceo(self):
        assert _is_owner_title("CEO") is True

    def test_founder(self):
        assert _is_owner_title("Founder & President") is True

    def test_owner(self):
        assert _is_owner_title("Owner") is True

    def test_case_insensitive(self):
        assert _is_owner_title("chief executive officer") is True

    def test_non_owner_title(self):
        assert _is_owner_title("Software Engineer") is False

    def test_empty_string(self):
        assert _is_owner_title("") is False


# ── _extract_partners_from_page ──────────────────────────────────────────────

class TestExtractPartnersFromPage:
    def test_partner_logo_alt_text(self):
        # Parent context must contain partner keyword in visible text
        html = '<div class="section"><h2>Our Partners</h2><img alt="Microsoft Corp" class="logo"></div>'
        result = _extract_partners_from_page(html, "")
        assert "Microsoft Corp" in result

    def test_partner_logo_with_keyword_in_alt(self):
        html = '<img alt="Microsoft Partner" class="logo">'
        result = _extract_partners_from_page(html, "")
        assert "Microsoft Partner" in result

    def test_partner_keyword_in_li(self):
        html = "<li>Our partners include <strong>Acme Corp</strong></li>"
        result = _extract_partners_from_page(html, "")
        assert any("Acme Corp" in p for p in result)

    def test_empty_html_returns_empty_list(self):
        assert _extract_partners_from_page("", "") == []


# ── _extract_services_from_schema ────────────────────────────────────────────

class TestExtractServicesFromSchema:
    def test_single_service(self):
        html = '<script type="application/ld+json">{"@type":"Service","name":"Plumbing Repair"}</script>'
        result = _extract_services_from_schema(html)
        assert result == "Plumbing Repair"

    def test_multiple_services(self):
        html = '<script type="application/ld+json">[{"@type":"Service","name":"Cleaning"},{"@type":"Service","name":"Painting"}]</script>'
        result = _extract_services_from_schema(html)
        assert "Cleaning" in result
        assert "Painting" in result

    def test_no_schema_returns_none(self):
        assert _extract_services_from_schema("<html></html>") is None


# ── _extract_services_from_html ──────────────────────────────────────────────

class TestExtractServicesFromHtml:
    def test_we_offer_pattern(self):
        # "we offer" regex requires trailing context (. We/Our/Call/Contact...)
        html = "<p>We offer Plumbing, Electrical, and HVAC services. Contact us today for details.</p>"
        result = _extract_services_from_html(html)
        assert result is not None
        assert "Plumbing" in result

    def test_heading_with_list(self):
        html = "<h2>Our Services</h2><ul><li>Web Design</li><li>SEO</li></ul>"
        result = _extract_services_from_html(html)
        assert result is not None
        assert "Web Design" in result

    def test_no_match_returns_none(self):
        assert _extract_services_from_html("<p>Hello world</p>") is None


# ── _extract_team_members ────────────────────────────────────────────────────

class TestExtractTeamMembers:
    def test_css_container_based(self):
        html = """
        <div class="team-member">
            <h3>Jane Doe</h3>
            <p>CEO</p>
        </div>
        <div class="team-member">
            <h3>John Smith</h3>
            <p>CTO</p>
        </div>
        """
        team_json, owner_title = _extract_team_members(html)
        assert team_json is not None
        assert "Jane Doe" in team_json
        assert owner_title is not None and "CEO" in owner_title

    def test_no_team_returns_none(self):
        assert _extract_team_members("<p>Hello</p>") == (None, None)


# ── _extract_recent_activity ─────────────────────────────────────────────────

class TestExtractRecentActivity:
    def test_iso_date_found(self):
        html = "<p>Posted on 2026-03-15 in blog</p>"
        result = _extract_recent_activity(html)
        assert len(result["dates"]) == 1
        assert result["latest"] is not None

    def test_text_date_found(self):
        html = "<p>March 15, 2026 — Latest news</p>"
        result = _extract_recent_activity(html)
        assert len(result["dates"]) == 1

    def test_no_dates_returns_empty(self):
        html = "<p>No dates here at all</p>"
        result = _extract_recent_activity(html)
        assert result == {"dates": [], "latest": None}


# ── _extract_blog_post_titles ────────────────────────────────────────────────

class TestExtractBlogPostTitles:
    def test_returns_headings(self):
        html = "<h2>How to Fix a Leak</h2><h2>Winter Plumbing Tips</h2>"
        result = _extract_blog_post_titles(html)
        assert "How to Fix a Leak" in result
        assert "Winter Plumbing Tips" in result

    def test_filters_navigation_text(self):
        html = "<h2>Recent Posts</h2>"
        result = _extract_blog_post_titles(html)
        assert result == []

    def test_no_headings_returns_empty(self):
        assert _extract_blog_post_titles("<p>Hello</p>") == []


# ── _compute_review_weaknesses ───────────────────────────────────────────────

class TestComputeReviewWeaknesses:
    def make_lead(self, **overrides):
        lead = MagicMock()
        lead.google_rating = None
        lead.google_review_count = 0
        lead.website = "https://example.com"
        lead.website_status = None
        lead.owner_email = "test@example.com"
        lead.phone = "1234567890"
        lead.has_google_maps = True
        for k, v in overrides.items():
            setattr(lead, k, v)
        return lead

    def test_no_weaknesses(self):
        lead = self.make_lead(google_rating=4.5, google_review_count=100)
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert weaknesses is None
        assert wc == 0

    def test_poor_reputation(self):
        lead = self.make_lead(google_rating=1.5)
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert wc >= 1
        assert "poor" in (weaknesses or "").lower()

    def test_no_website(self):
        lead = self.make_lead(website=None)
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert "no website" in (weaknesses or "").lower()
        assert wc >= 1

    def test_no_contact(self):
        lead = self.make_lead(owner_email=None, phone=None)
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert "no contact" in (weaknesses or "").lower()
        assert wc >= 1

    def test_multiple_weaknesses_accumulate(self):
        lead = self.make_lead(
            google_rating=1.5,
            google_review_count=100,
            website=None,
            owner_email=None,
            phone=None,
        )
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert wc >= 3

    def test_systemic_complaints(self):
        lead = self.make_lead(google_rating=3.0, google_review_count=100)
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert wc >= 1
        assert "systemic" in (weaknesses or "").lower()

    def test_no_email_weakness(self):
        lead = self.make_lead(google_rating=4.5, owner_email=None)
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert "no email" in (weaknesses or "").lower()
        assert wc >= 1

    def test_below_average_rating(self):
        lead = self.make_lead(google_rating=3.7, owner_email="test@example.com")
        weaknesses, wc = _compute_review_weaknesses(lead)
        assert "below-average" in (weaknesses or "").lower()
        assert wc >= 1
