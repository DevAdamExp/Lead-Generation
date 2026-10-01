"""detect_marketing_stack — marketing-active-buyer signal from on-page ad/marketing tags."""
from backend.services.business_intel import detect_marketing_stack


def test_facebook_pixel_means_paid_ads():
    html = '<script>!function(){fbq("init","123")}</script><script src="https://connect.facebook.net/en_US/fbevents.js"></script>'
    stack, runs_ads = detect_marketing_stack(html)
    assert "Facebook Pixel" in stack
    assert runs_ads is True


def test_analytics_only_not_paid_ads():
    stack, runs_ads = detect_marketing_stack('<script src="https://www.google-analytics.com/analytics.js"></script>')
    assert stack == ["Google Analytics"]
    assert runs_ads is False  # measurement, not a paid-ad pixel


def test_google_ads_is_paid():
    stack, runs_ads = detect_marketing_stack('<img src="https://googleads.g.doubleclick.net/pagead/x">')
    assert "Google Ads" in stack and runs_ads is True


def test_empty_neutral():
    assert detect_marketing_stack("") == ([], False)
    assert detect_marketing_stack("<p>plain site</p>") == ([], False)
