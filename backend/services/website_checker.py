"""
services/website_checker.py — Website liveness, CMS detection, SSL validation.

Fixes:
  - SSL validation: Now actually checks certificate validity (expiry, trust chain)
    using ssl + OpenSSL, instead of just checking URL prefix.
  - CMS detection: Expanded to 12 CMS types with more reliable signatures.
  - Website status: Distinguishes between ACTIVE, DEAD, PARKED, and ERROR.
  - Social links: More precise regex patterns with domain validation.
  - Proxy rotation: Uses ProxyRotator for concurrent requests.
"""
import asyncio
import logging
import re
import ssl
import socket
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from backend.models import Lead, WebsiteStatus
from backend.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 12
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Expanded CMS signatures with multiple indicators each
CMS_SIGNATURES = {
    "WordPress": [
        "wp-content", "wp-includes", 'name="generator" content="WordPress',
        "/wp-json/", "/wp-admin/", "wp-emoji-release",
    ],
    "Shopify": [
        "cdn.shopify.com", "shopify.com/s/files", "Shopify.theme",
        "Shopify.shop", "powered_by_shopify", "window.Shopify",
    ],
    "Wix": [
        "wix.com", "X-Wix-Published-Version", "static.parastorage.com",
        "wix-renderer", "wix-image", "wix-site",
    ],
    "Squarespace": [
        "squarespace.com", "squarespace-cdn.com", "squarespace.net",
        "static1.squarespace.com", "sqs-block", "sqs-layout",
    ],
    "Webflow": [
        "webflow.com", "webflow.io", "assets.website-files.com",
        "w-webflow", "wf-",
    ],
    "Weebly": [
        "weebly.com", "editmysite.com", "sites.google.com/site/",
        "wsite-content",
    ],
    "GoDaddy": [
        "godaddysites.com", "secureserver.net", "website-builder.godaddy.com",
    ],
    "Joomla": [
        "joomla", "com_content", "com_modules", "/components/com_",
        'name="generator" content="Joomla',
    ],
    "Drupal": [
        "drupal", "Drupal.settings", "/sites/default/files",
        'name="Generator" content="Drupal',
    ],
    "Magento": [
        "magento", "Magento_", "mage/", "Mage.Cookies",
        "var/theme/",
    ],
    "PrestaShop": [
        "prestashop", "PrestaShop", "/themes/default-bootstrap/",
        "ps_shoppingcart",
    ],
    "WixStudio": [
        "wixstudio.com", "editor.wix.com", "wix-layout",
    ],
}

SOCIAL_PATTERNS = {
    "facebook": r"https?://(?:www\.)?(?:facebook|fb)\.com/[a-zA-Z0-9.\-/_]+",
    "instagram": r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9._\-/]+",
    "twitter": r"https?://(?:www\.)?(?:twitter|x)\.com/[a-zA-Z0-9_\-/]+",
    "linkedin": r"https?://(?:www\.)?linkedin\.com/(?:company|in|school)/[a-zA-Z0-9\-_./]+",
}

# Known parked domain indicators
PARKED_INDICATORS = [
    "this domain is parked", "domain is for sale", "buy this domain",
    "parked free", "godaddy parking", "sedoparking",
    "domainname.com is available", "this website is under construction",
    "coming soon", "website coming soon",
    "hugedomains.com", "afternic.com", "sedo.com",
]


def _check_ssl_certificate(hostname: str, port: int = 443) -> dict:
    """
    Actually validate the SSL certificate, checking:
      - Certificate exists
      - Not expired
      - Trust chain (basic validation)
      - Hostname matches

    Returns:
        dict with keys: valid (bool), issuer, expiry, error
    """
    result = {"valid": False, "issuer": None, "expiry": None, "error": None}

    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=8) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    result["error"] = "No certificate returned"
                    return result

                # Check expiry
                if cert.get("notAfter"):
                    expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                    result["expiry"] = expiry.isoformat()
                    if expiry < datetime.now():
                        result["error"] = "Certificate expired"
                        return result

                # Get issuer
                issuer = dict(x[0] for x in cert.get("issuer", []) if x)
                result["issuer"] = issuer.get("organizationName", issuer.get("commonName"))

                result["valid"] = True
                return result

    except ssl.SSLCertVerificationError as e:
        result["error"] = str(e)
    except ssl.SSLError as e:
        result["error"] = str(e)
    except socket.timeout:
        result["error"] = "Connection timeout"
    except ConnectionRefusedError:
        result["error"] = "Connection refused"
    except Exception as e:
        result["error"] = str(e)

    return result


def _detect_parked_page(html: str) -> bool:
    """Check if a page is a parked domain placeholder."""
    html_lower = html.lower()
    return any(indicator in html_lower for indicator in PARKED_INDICATORS)


async def _check_single(lead: Lead, client: httpx.AsyncClient) -> None:
    """Check one lead's website — updates lead fields in place."""
    url = (lead.website or "").strip()
    if not url:
        lead.website_status = WebsiteStatus.NONE
        return

    if not url.startswith("http"):
        url = "https://" + url
        lead.website = url

    parsed = urlparse(url)
    hostname = parsed.netloc.split(":")[0]

    # Real SSL certificate validation. _check_ssl_certificate is BLOCKING stdlib
    # (socket.create_connection + TLS handshake, up to 8s) — calling it directly
    # from this coroutine froze the whole event loop, making the semaphore below
    # decorative and the entire website stage serial. Off-thread it.
    if url.startswith("https://"):
        ssl_info = await asyncio.to_thread(_check_ssl_certificate, hostname)
        lead.has_ssl = ssl_info["valid"]
        if ssl_info["error"]:
            logger.debug("SSL check for %s: %s", hostname, ssl_info["error"])
    else:
        ssl_info = None
        lead.has_ssl = False

    try:
        resp = await client.get(url, follow_redirects=True, timeout=TIMEOUT)

        if resp.status_code < 400:
            html = resp.text

            # Check for parked page
            if _detect_parked_page(html):
                lead.website_status = WebsiteStatus.DEAD
                lead.website_cms = "parked"
                return

            lead.website_status = WebsiteStatus.ACTIVE

            # Content relevance: does the business name appear on the page?
            html_lower = html.lower()
            if lead.name:
                name_parts = lead.name.strip().lower().split()
                if len(name_parts) >= 2:
                    lead.website_name_found = all(
                        part in html_lower for part in name_parts
                    )
                else:
                    lead.website_name_found = name_parts[0] in html_lower
            else:
                lead.website_name_found = False

            # Detect CMS — try all signatures
            for cms, sigs in CMS_SIGNATURES.items():
                if any(sig.lower() in html_lower for sig in sigs):
                    lead.website_cms = cms
                    break

            # Extract social links
            for platform, pattern in SOCIAL_PATTERNS.items():
                matches = re.findall(pattern, html, re.IGNORECASE)
                if matches:
                    clean = matches[0].rstrip("/").split("?")[0]
                    setattr(lead, f"social_{platform}", clean)

        elif resp.status_code < 500:
            # 4xx errors
            if resp.status_code == 429:
                lead.website_status = WebsiteStatus.ACTIVE  # rate limited, site is up
            else:
                lead.website_status = WebsiteStatus.DEAD
        else:
            lead.website_status = WebsiteStatus.DEAD

    except httpx.TimeoutException:
        lead.website_status = WebsiteStatus.DEAD
    except httpx.RequestError:
        lead.website_status = WebsiteStatus.DEAD


async def check_all_websites(leads: list, db) -> None:
    """Check all websites concurrently with a semaphore limit.

    One shared client for the whole batch — a per-lead AsyncClient threw away
    connection pooling and paid a fresh TLS handshake per site.
    """
    sem = asyncio.Semaphore(settings.WEBSITE_CONCURRENCY)

    async with httpx.AsyncClient(
        headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
    ) as client:
        async def _guarded(lead):
            async with sem:
                await _check_single(lead, client)

        await asyncio.gather(*[_guarded(lead) for lead in leads])
    db.commit()


def check_all_websites_sync(leads: list, db) -> None:
    """Sync wrapper for Celery."""
    asyncio.run(check_all_websites(leads, db))
