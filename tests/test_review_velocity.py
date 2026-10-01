"""review_velocity — free reviews/year proxy from review_count + year_founded."""
from backend.services.business_intel import review_velocity


def test_velocity_math():
    assert review_velocity(60, 2014, now_year=2024) == 6.0
    assert review_velocity(14, 2012, now_year=2024) == 1.2  # 14/12 ≈ 1.17 → 1.2 (the wedge)


def test_unknown_returns_none():
    assert review_velocity(None, 2010, now_year=2024) is None      # no reviews
    assert review_velocity(50, None, now_year=2024) is None        # unknown founding
    assert review_velocity(50, 2030, now_year=2024) is None        # implausible future year


def test_min_one_year_no_div_zero():
    assert review_velocity(10, 2024, now_year=2024) == 10.0        # opened this year → /1
