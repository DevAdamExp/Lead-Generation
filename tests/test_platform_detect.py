"""detect_platforms — the commission-bleed wedge signal (delivery apps / OTAs vs
a first-party ordering/booking system)."""
from backend.services.business_intel import detect_platforms


def test_delivery_apps_no_direct():
    html = '<a href="https://www.doordash.com/store/x">Order</a> <a href="https://ubereats.com/y">Uber</a>'
    ext, has_direct = detect_platforms(html)
    assert "DoorDash" in ext and "Uber Eats" in ext
    assert has_direct is False  # the wedge: app-dependent, no direct channel


def test_first_party_ordering():
    html = '<a href="https://acme.toasttab.com/order">Order Online</a>'
    ext, has_direct = detect_platforms(html)
    assert ext == [] and has_direct is True


def test_hotel_ota():
    ext, has_direct = detect_platforms('<a href="https://www.booking.com/hotel/abc">Book</a>')
    assert "Booking.com" in ext and has_direct is False


def test_empty_is_neutral():
    assert detect_platforms("") == ([], False)
    assert detect_platforms("<p>just a plumber</p>") == ([], False)
