"""
services/vertical_profiles.py — map a lead's `business_category` to a US vertical
research/pitch profile (encoded from docs/verticals/*.md) so the pitch LLM is framed for
THAT niche instead of generic. A roofer gets pitched like a roofer.

# ponytail: data, not abstraction. One keyword matcher; add a trade by appending a
# Profile, never by touching logic. Order matters — specific trades before fallbacks.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    keywords: tuple[str, ...]
    revenue_model: str  # recurring | one-shot | insurance | mixed
    pains: tuple[str, ...]
    pitch_angles: tuple[str, ...]
    signals: tuple[str, ...]
    opener_hint: str

    def prompt_context(self) -> str:
        return (
            f"VERTICAL: {self.name} (US). Revenue model: {self.revenue_model}.\n"
            f"Typical pains: {'; '.join(self.pains)}.\n"
            f"What an agency sells them: {'; '.join(self.pitch_angles)}.\n"
            f"Weigh these signals when reasoning: {'; '.join(self.signals)}.\n"
            f"Opener framing: {self.opener_hint}"
        )


# Specific sub-trades first; general construction, then restaurant/hospitality; FALLBACK last.
PROFILES: tuple[Profile, ...] = (
    Profile(
        "Roofing", ("roof",), "insurance",
        ("lead-hungry & commoditized", "must be first to call (speed-to-lead)",
         "financing needed on big tickets", "storm-driven demand spikes"),
        ("Local SEO / map pack", "review generation", "instant lead response",
         "financing display", "storm-triggered campaigns"),
        ("Google review count & recency vs local competitors", "financing offered?",
         "24/7?", "gallery/photos present?"),
        "lead with a reviews/map-pack gap vs a named nearby competitor, or a recent local storm.",
    ),
    Profile(
        "HVAC", ("hvac", "heating", "air conditioning", "cooling", "furnace", "ac repair"),
        "recurring",
        ("seasonal swings & idle techs off-season", "low maintenance-plan attach",
         "missed after-hours calls", "price competition on installs"),
        ("maintenance-plan growth & customer LTV", "off-season demand campaigns",
         "review velocity", "instant lead response"),
        ("does the site advertise a service/maintenance plan?", "review count & recency",
         "24/7 emergency?"),
        "if no service-plan signup is visible, lead with the recurring revenue they're missing.",
    ),
    Profile(
        "Plumbing", ("plumb",), "mixed",
        ("emergency response is everything", "missed after-hours calls",
         "high-intent 'near me' competition", "price shopping"),
        ("speed-to-lead / call capture", "map pack for 'plumber near me'",
         "review generation", "service-plan upsell"),
        ("review count & recency", "24/7?", "map-pack visibility"),
        "lead with after-hours/'near me' call capture they're likely losing.",
    ),
    Profile(
        "Electrical", ("electric",), "mixed",
        ("licensing-gated", "resi vs commercial split", "project-flow lumpiness"),
        ("panel-upgrade / EV-charger demand campaigns", "GC-partnership funnel",
         "review generation", "Local SEO"),
        ("EV charger / solar tie-in language?", "review count", "commercial vs residential focus"),
        "lead with a high-demand service (EV chargers, panel upgrades) they under-promote.",
    ),
    Profile(
        "Solar", ("solar", "photovoltaic"), "one-shot",
        ("very long sales cycle", "heavy permitting/interconnection paperwork",
         "incentive-deadline (ITC) timing", "high lead cost"),
        ("incentive-deadline urgency campaigns", "financing", "long-cycle lead nurture", "reputation"),
        ("financing offered?", "review trust signals", "any lead-nurture/CRM signal?"),
        "lead with incentive-deadline urgency or the missing follow-up on a long sales cycle.",
    ),
    Profile(
        "Restoration (water/fire/mold)", ("restoration", "water damage", "fire damage",
                                          "mold", "disaster"), "insurance",
        ("must be the FIRST call", "insurance billing complexity", "24/7 demand"),
        ("emergency call capture + speed-to-lead", "insurance-agent referral funnel",
         "review generation"),
        ("24/7?", "'insurance claims welcome' language?", "review count & recency"),
        "lead with being first-call for emergencies and the agent-referral funnel.",
    ),
    Profile(
        "Landscaping / Lawn", ("landscap", "lawn", "hardscape", "irrigation", "lawn care",
                               "tree service"), "recurring",
        ("maintenance churn", "route density economics", "seasonal labor"),
        ("recurring-plan retention", "neighborhood/route-density campaigns",
         "design-build upsell", "reviews"),
        ("recurring maintenance plan advertised?", "service area / neighborhood density",
         "before/after gallery?"),
        "lead with recurring-plan retention or winning a whole neighborhood (route density).",
    ),
    Profile(
        "Concrete / Masonry / Paving", ("concrete", "masonry", "paving", "asphalt", "driveway"),
        "one-shot",
        ("weather-dependent", "needs visual proof", "seasonal gaps"),
        ("before/after galleries", "off-season booking campaigns", "review generation"),
        ("photo gallery present?", "review count", "seasonality of the area"),
        "lead with visual-proof galleries and filling the off-season pipeline.",
    ),
    Profile(
        "Painting", ("paint",), "one-shot",
        ("low ticket → needs volume", "commoditized", "fast-quote competition"),
        ("review + before/after proof", "fast quoting", "volume lead gen"),
        ("review count & recency", "before/after gallery?", "quote-request path on site?"),
        "lead with reviews + before/after proof and faster quoting to win on volume.",
    ),
    Profile(
        "Remodeling / General Contractor", ("remodel", "kitchen", "bath", "general contractor",
                                            "construction", "builder", "contractor", "renovation"),
        "one-shot",
        ("long sales cycle", "design-trust hurdle", "financing on big tickets",
         "lead quality"),
        ("portfolio site + galleries", "lead nurturing for the long cycle",
         "financing display", "Houzz/Instagram presence"),
        ("gallery/portfolio present?", "financing offered?", "review count",
         "any follow-up/CRM signal?"),
        "lead with a portfolio/trust gap or the missing nurture on a long, high-ticket cycle.",
    ),
    Profile(
        "Restaurant", ("restaurant", "cafe", "coffee", "bar", "grill", "pizzeria", "pizza",
                       "diner", "eatery", "bakery", "bistro", "pub", "taco", "sushi", "kitchen"),
        "mixed",
        ("delivery-app commissions 15-30%", "thin margins", "reputation swings",
         "no repeat-customer engine", "empty slow nights"),
        ("direct online ordering (cut app commissions)", "reservations/waitlist",
         "review management", "email/SMS loyalty", "social content + photography"),
        ("on DoorDash/UberEats AND no own-site ordering?", "Google+Yelp rating/recency",
         "Instagram followers & last post", "price band & cuisine"),
        "lead with the dollar value of delivery-app commissions and a direct-order link.",
    ),
    Profile(
        "Hospitality / Lodging", ("hotel", "motel", "inn", "lodge", "resort", "hostel",
                                  "suites", "bed and breakfast", "b&b", "guest house"),
        "mixed",
        ("OTA commissions 15-25%", "low direct-booking share", "occupancy gaps/seasonality",
         "reputation", "ADA/PCI exposure"),
        ("direct-booking site + booking engine", "Google Hotel Ads / metasearch",
         "reputation management", "guest email/CRM", "upsell tooling"),
        ("on Booking/Expedia AND weak own-site booking engine?", "room count / size",
         "independent vs franchise", "Google+TripAdvisor rating/recency"),
        "lead with reclaimed OTA commission $ from winning bookings direct (skip franchises).",
    ),
)

FALLBACK = Profile(
    "Local business", (), "mixed",
    ("invisible online", "few/stale reviews", "weak or no website", "no repeat-customer engine"),
    ("Local SEO / Google profile", "review generation", "website that converts",
     "email/SMS follow-up"),
    ("review count & recency", "website quality/SSL", "social presence"),
    "lead with the single most obvious online gap you can observe.",
)


def match_profile(category: str | None) -> Profile:
    """First profile whose keyword appears in the (lowercased) category; else FALLBACK."""
    c = (category or "").lower()
    if c:
        for p in PROFILES:
            if any(k in c for k in p.keywords):
                return p
    return FALLBACK


if __name__ == "__main__":  # ponytail: one runnable check
    assert match_profile("Roofing Contractor").name == "Roofing"
    assert match_profile("Joe's HVAC & Heating").revenue_model == "recurring"
    assert match_profile("Italian Restaurant").name == "Restaurant"
    assert match_profile("Seaside Motel").name == "Hospitality / Lodging"
    assert match_profile("Acme Kitchen Remodeling").revenue_model == "one-shot"
    assert match_profile(None).name == "Local business"
    assert match_profile("Pet Grooming").name == "Local business"
    assert match_profile("Roofing Contractor").prompt_context().startswith("VERTICAL: Roofing")
    print("OK: vertical_profiles matching")
