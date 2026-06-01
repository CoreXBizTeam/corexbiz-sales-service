#!/usr/bin/env python3
"""
lead_qualifier.py

Purpose (V1 — WordPress-focused, Canada-friendly):
- Read a CSV of business leads (optional `city` / `province` columns preserved)
- Normalize Canadian provinces to 2-letter codes in `province_normalized` when possible
- Visit each website, detect WordPress / WooCommerce, uploads, email
- Output column order supports sorting: province → city → WP → Woo → score

Usage:
    python lead_qualifier.py input.csv output.csv
"""

from __future__ import annotations

import asyncio
import csv
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp
import requests
from bs4 import BeautifulSoup

# -----------------------------
# Configuration
# -----------------------------

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 12
SLEEP_BETWEEN_REQUESTS = 0.75

# Concurrent HTTP cap for async qualifier (homepage + secondaries share this pool).
MAX_CONCURRENT = 15

# CSV column order: sort-friendly fields first, then core enrichment, then the rest.
OUTPUT_COLUMN_PREFIX = [
    "province_normalized",
    "city",
    "province",
    "prov",
    "business_name",
    "website",
    "normalized_url",
    "wordpress_detected",
    "woocommerce_detected",
    "wordpress_sort",
    "woocommerce_sort",
    "priority_score_sort",
    "priority_score",
    "priority_segment",
    "final_url",
    "reachable",
    "http_status",
    "platform",
    "ecommerce",
    "upload_present",
    "upload_signals",
    "email_found",
    "fit_segment",
    "fit_score",
    "notes",
]

# Strip these headers (any case) when present; province text used for normalization.
_GEO_HEADER_CANDIDATES = frozenset({"city", "province", "prov"})

# Lowercased, punctuation-light keys → two-letter codes (see normalize_canadian_province).
CANADIAN_PROVINCE_ALIASES: Dict[str, str] = {
    "on": "ON",
    "ont": "ON",
    "ontario": "ON",
    "bc": "BC",
    "b c": "BC",
    "british columbia": "BC",
    "colombie britannique": "BC",
    "ab": "AB",
    "alta": "AB",
    "alberta": "AB",
    "sk": "SK",
    "sask": "SK",
    "saskatchewan": "SK",
    "mb": "MB",
    "man": "MB",
    "manitoba": "MB",
    "qc": "QC",
    "pq": "QC",
    "que": "QC",
    "quebec": "QC",
    "nb": "NB",
    "n b": "NB",
    "new brunswick": "NB",
    "nouveau brunswick": "NB",
    "ns": "NS",
    "n s": "NS",
    "nova scotia": "NS",
    "nouvelle ecosse": "NS",
    "nouvelle-ecosse": "NS",
    "pe": "PE",
    "p e i": "PE",
    "pei": "PE",
    "p e": "PE",
    "prince edward island": "PE",
    "ile du prince edouard": "PE",
    "nl": "NL",
    "nf": "NL",
    "newfoundland": "NL",
    "newfoundland and labrador": "NL",
    "nfld": "NL",
    "terre neuve": "NL",
    "terre neuve et labrador": "NL",
    "nt": "NT",
    "n w t": "NT",
    "nwt": "NT",
    "northwest territories": "NT",
    "territoires du nord ouest": "NT",
    "nu": "NU",
    "nunavut": "NU",
    "yt": "YT",
    "yk": "YT",
    "yukon": "YT",
    "yukon territory": "YT",
}

CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/upload",
    "/upload-artwork",
    "/artwork-upload",
    "/file-upload",
    "/send-files",
    "/send-artwork",
    "/artwork",
    "/submit",
    "/upload-files",
]

EMAIL_REGEX = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)

# Visible copy / headings for print-shop file handoff (matched in page text + raw HTML).
UPLOAD_KEYWORDS = [
    "upload artwork",
    "upload file",
    "file upload",
    "send files",
    "send your files",
    "submit files",
    "submit artwork",
    "artwork upload",
    "upload design",
    "upload your design",
    "upload your files",
    "upload your artwork",
    "drag and drop",
    "drop files",
    "upload image",
    "upload pdf",
    "upload vector",
    "print ready",
    "print-ready",
    "hi-res",
    "hires",
    "attach file",
    "transfer files",
    "send us your file",
    "send us your artwork",
]

# Script / CSS / data attributes (URLs and inline HTML).
UPLOAD_TECH_HINTS = [
    "filepond",
    "uppy",
    "dropzone",
    "uploadcare",
    "pintura",
    "cloudinary",
    "transloadit",
    "fine-uploader",
    "plupload",
    "filestack",
    "tus-js",
]

# Link href or anchor text hints for artwork / file submission pages.
UPLOAD_LINK_HREF_MARKERS = [
    "upload",
    "artwork",
    "send-file",
    "sendfile",
    "file-upload",
    "your-files",
    "submit-file",
    "graphics",
    "/files",
]

UPLOAD_LINK_LABEL_MARKERS = [
    "send files",
    "send file",
    "upload design",
    "your artwork",
    "submit artwork",
    "upload your",
    "file upload",
    "upload artwork",
    "transfer files",
]

# ---- Platform fingerprint markers (V1: WordPress first, others minimal) --------

WORDPRESS_HTML_MARKERS = [
    "wp-content",
    "wp-json",
    "/wp-includes/",
    "wp-block-",
    "wp-emoji-release",
    "xmlrpc.php",
    "wp-login.php",
    "/wp-admin",
    "wordpress",
]

WOOCOMMERCE_HTML_MARKERS = [
    "woocommerce",
    "wc-ajax",
    "wc-cart-fragments",
    "add_to_cart_button",
    "wc-block-",
]

# Secondary: only evaluated when WordPress is not detected (light touch).
SHOPIFY_MARKERS = ["cdn.shopify.com", "shopify-section", "myshopify.com", "x-shopid"]
WIX_MARKERS = ["wixstatic.com", "parastorage.com", "x-wix-"]
SQUARESPACE_MARKERS = [
    "static1.squarespace.com",
    "squarespace-cdn",
    "data-squarespace-",
]

# -----------------------------
# Data classes
# -----------------------------

@dataclass
class FetchResult:
    ok: bool
    url: str
    final_url: str
    status_code: Optional[int]
    html: str
    # Raw response headers for platform fingerprinting (e.g. X-ShopId, X-Wix-*)
    headers: Dict[str, str] = None  # type: ignore[assignment]
    error: str = ""

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}


# -----------------------------
# Helpers
# -----------------------------

def normalize_canadian_province(raw: str) -> str:
    """Map a province/territory string to a 2-letter code, or '' if unknown.

    Handles English/French names, common abbreviations, and punctuation variants.
    """
    if not raw or not str(raw).strip():
        return ""
    s = str(raw).strip().lower()
    s = s.replace(".", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # Normalize accented e for Québec variants
    s = s.replace("é", "e")

    if len(s) == 2 and s.upper() in {"ON", "BC", "AB", "SK", "MB", "QC", "NB", "NS", "PE", "NL", "NT", "NU", "YT"}:
        return s.upper()
    return CANADIAN_PROVINCE_ALIASES.get(s, "")


def _strip_geo_fields(row: Dict[str, str]) -> None:
    """Trim whitespace on city / province / prov columns without renaming headers."""
    for key in list(row.keys()):
        if key.lower() in _GEO_HEADER_CANDIDATES:
            row[key] = (row[key] or "").strip()


def _province_raw_from_row(row: Dict[str, str]) -> str:
    # Accept common CSV header variants; first non-empty wins.
    for key in ("province", "Province", "prov", "Prov", "PROV", "state", "State"):
        v = (row.get(key) or "").strip()
        if v:
            return v
    return ""


def apply_location_and_sort_columns(row: Dict[str, str]) -> None:
    """Set province_normalized and stable sort keys (mutates row in place)."""
    _strip_geo_fields(row)
    row["province_normalized"] = normalize_canadian_province(_province_raw_from_row(row))

    wp = row.get("wordpress_detected", "no").lower() == "yes"
    woo = row.get("woocommerce_detected", "no").lower() == "yes"
    try:
        ps = int(str(row.get("priority_score") or "0").strip())
    except ValueError:
        ps = 0
    # Descending-friendly: 1 = yes / higher signal,0 = no (sort these columns descending first)
    row["wordpress_sort"] = "1" if wp else "0"
    row["woocommerce_sort"] = "1" if woo else "0"
    row["priority_score_sort"] = str(max(0, min(999, ps))).zfill(3)


def normalize_url(raw_url: str) -> str:
    raw_url = (raw_url or "").strip()
    if not raw_url:
        return ""
    if not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url
    return raw_url


def fetch_url(session: requests.Session, url: str) -> FetchResult:
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        content_type = response.headers.get("Content-Type", "")
        html = response.text if "html" in content_type.lower() else ""
        # Preserve all response headers; lowercased keys for uniform lookup
        raw_headers = {k.lower(): v for k, v in response.headers.items()}
        return FetchResult(
            ok=response.ok,
            url=url,
            final_url=response.url,
            status_code=response.status_code,
            html=html,
            headers=raw_headers,
            error="",
        )
    except requests.RequestException as exc:
        return FetchResult(
            ok=False,
            url=url,
            final_url="",
            status_code=None,
            html="",
            error=str(exc),
        )


async def fetch_url_async(
    session: aiohttp.ClientSession,
    url: str,
    semaphore: asyncio.Semaphore,
) -> FetchResult:
    """Async HTTP GET; same semantics as :func:`fetch_url` (timeout, HTML-only body, headers)."""
    async with semaphore:
        try:
            async with session.get(
                url,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                html = ""
                if "html" in content_type.lower():
                    html = await response.text()
                raw_headers = {k.lower(): v for k, v in response.headers.items()}
                final_u = str(response.url)
                status = response.status
                # Match requests.Response.ok: True for any status below 400.
                ok = status < 400
                return FetchResult(
                    ok=ok,
                    url=url,
                    final_url=final_u,
                    status_code=status,
                    html=html,
                    headers=raw_headers,
                    error="",
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            return FetchResult(
                ok=False,
                url=url,
                final_url="",
                status_code=None,
                html="",
                error=str(exc),
            )


def soup_text(soup: BeautifulSoup) -> str:
    return soup.get_text(" ", strip=True).lower()


def collect_asset_urls(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    for tag in soup.find_all(["script", "link", "img", "a"]):
        attr = "href" if tag.name in ("link", "a") else "src"
        value = tag.get(attr)
        if value:
            urls.append(urljoin(base_url, value))

    return urls


def _extract_generator(soup: BeautifulSoup) -> str:
    """Return the lowercased content of <meta name="generator"> if present."""
    tag = soup.find("meta", attrs={"name": re.compile(r"^generator$", re.I)})
    if tag and tag.get("content"):
        return tag["content"].strip().lower()
    return ""


def _any_marker_in(markers: List[str], *haystacks: str) -> bool:
    """True when at least one marker substring appears in any haystack."""
    for marker in markers:
        for hay in haystacks:
            if marker in hay:
                return True
    return False


def detect_platform(
    html: str,
    soup: BeautifulSoup,
    final_url: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, str, bool, bool]:
    """Detect platform with WordPress/WooCommerce first; other hosts only if not WP.

    Returns (platform, ecommerce, wordpress_detected, woocommerce_detected).
    Booleans reflect high-confidence signals (same basis as WooCommerce in ecommerce).
    """
    html_l = html.lower()
    text_l = soup_text(soup)
    assets = " ".join(collect_asset_urls(soup, final_url)).lower()
    hdrs = headers or {}
    generator = _extract_generator(soup)

    # V1: establish WordPress / WooCommerce before any hosted competitor checks.
    wp_from_generator = "wordpress" in generator or "woocommerce" in generator
    woo_from_generator = "woocommerce" in generator
    woo_html = _any_marker_in(WOOCOMMERCE_HTML_MARKERS, html_l, assets)
    wp_html = _any_marker_in(WORDPRESS_HTML_MARKERS, html_l, assets, text_l)

    wordpress_detected = bool(wp_from_generator or wp_html or woo_html)
    woocommerce_detected = bool(woo_from_generator or woo_html)

    platform = "Unknown"
    ecommerce = "Unknown"

    if wordpress_detected:
        platform = "WordPress"
        if woocommerce_detected:
            ecommerce = "WooCommerce"
        elif "shop" in text_l or "cart" in text_l or "checkout" in text_l:
            ecommerce = "Possible"
        else:
            ecommerce = "None/Unknown"
    else:
        # Minimal secondary fingerprint: one combined blob (HTML, assets, headers, URL).
        hdr_blob = " ".join(hdrs.keys()) + " " + " ".join(hdrs.values()).lower()
        blob = " ".join([html_l, assets, hdr_blob, final_url.lower()])
        if any(m in blob for m in SHOPIFY_MARKERS):
            platform = "Shopify"
            ecommerce = "Shopify"
        elif any(m in blob for m in WIX_MARKERS):
            platform = "Wix"
            ecommerce = "None/Unknown"
        elif any(m in blob for m in SQUARESPACE_MARKERS):
            platform = "Squarespace"
            ecommerce = "None/Unknown"
        elif "shop" in text_l or "cart" in text_l or "checkout" in text_l:
            ecommerce = "Possible"
        else:
            ecommerce = "None/Unknown"

    return platform, ecommerce, wordpress_detected, woocommerce_detected


def detect_upload_signals(html: str, soup: BeautifulSoup, final_url: str) -> Tuple[bool, List[str]]:
    html_l = html.lower()
    text_l = soup_text(soup)
    assets_l = " ".join(collect_asset_urls(soup, final_url)).lower()

    signals: List[str] = []

    for keyword in UPLOAD_KEYWORDS:
        if keyword in text_l or keyword in html_l:
            signals.append(keyword)

    for hint in UPLOAD_TECH_HINTS:
        if hint in assets_l or hint in html_l:
            signals.append(hint)

    # Explicit file pickers (common on print order / quote flows).
    if soup.find("input", attrs={"type": re.compile(r"^file$", re.I)}):
        signals.append("form:file-input")

    for form in soup.find_all("form"):
        enc = (form.get("enctype") or "").lower()
        if "multipart" in enc:
            signals.append("form:multipart")
            break

    for link in soup.find_all("a", href=True):
        href = link["href"].lower()
        label = link.get_text(" ", strip=True).lower()
        href_hit = any(m in href for m in UPLOAD_LINK_HREF_MARKERS)
        label_hit = any(m in label for m in UPLOAD_LINK_LABEL_MARKERS)
        # Short CTAs (e.g. nav item "Upload") — keep overlap with phrase list above.
        if href_hit or label_hit or "upload" in label or "artwork" in label:
            signals.append(f"link:{label[:60] or href[:60]}")

    deduped = sorted(set(signals))
    return (len(deduped) > 0), deduped


def find_email_in_soup(soup: BeautifulSoup, html: str) -> str:
    # First: explicit mailto
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            return href.split("mailto:", 1)[1].split("?", 1)[0].strip()

    # Second: visible email in HTML/text
    matches = EMAIL_REGEX.findall(html)
    if matches:
        return matches[0].strip()

    return ""


def _merge_secondary_page_into_findings(findings: Dict[str, str], result: FetchResult) -> None:
    """Apply one secondary FetchResult to findings (same rules as try_secondary_pages loop)."""
    if not result.ok or not result.html:
        return

    soup = BeautifulSoup(result.html, "html.parser")

    email = find_email_in_soup(soup, result.html)
    if email and not findings["secondary_email"]:
        findings["secondary_email"] = email

    has_upload, upload_signals = detect_upload_signals(result.html, soup, result.final_url)
    if has_upload and not findings["secondary_upload_signals"]:
        findings["secondary_upload_signals"] = "; ".join(upload_signals[:8])


def try_secondary_pages(session: requests.Session, base_url: str) -> Dict[str, str]:
    """
    Try a few likely pages to find upload/contact clues and email.
    """
    findings = {
        "secondary_email": "",
        "secondary_upload_signals": "",
    }

    for path in CONTACT_PATHS:
        url = urljoin(base_url, path)
        result = fetch_url(session, url)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        _merge_secondary_page_into_findings(findings, result)

        if findings["secondary_email"] and findings["secondary_upload_signals"]:
            break

    return findings


async def try_secondary_pages_async(
    session: aiohttp.ClientSession,
    base_url: str,
    semaphore: asyncio.Semaphore,
) -> Dict[str, str]:
    """Fetch all CONTACT_PATHS concurrently; merge in path order with same early-stop as sync."""

    async def fetch_path(path: str) -> Tuple[str, FetchResult]:
        url = urljoin(base_url, path)
        res = await fetch_url_async(session, url, semaphore)
        return (path, res)

    path_results = await asyncio.gather(*[fetch_path(p) for p in CONTACT_PATHS])
    by_path = dict(path_results)

    findings = {
        "secondary_email": "",
        "secondary_upload_signals": "",
    }
    for path in CONTACT_PATHS:
        _merge_secondary_page_into_findings(findings, by_path[path])
        if findings["secondary_email"] and findings["secondary_upload_signals"]:
            break

    return findings


def score_fit(platform: str, ecommerce: str, has_upload: bool, reachable: bool) -> Tuple[str, int]:
    """Legacy fit_score / fit_segment (small additive scale) for existing CSV consumers."""
    score = 0

    if reachable:
        score += 1
    if platform == "WordPress":
        score += 3
    if ecommerce == "WooCommerce":
        score += 2
    if not has_upload:
        score += 2
    if has_upload:
        score -= 1

    if platform == "WordPress" and ecommerce == "WooCommerce" and not has_upload:
        segment = "Strong Fit: WP + WooCommerce without visible upload"
    elif platform == "WordPress" and not has_upload:
        segment = "Strong Fit: WP without visible upload"
    elif platform == "WordPress" and has_upload:
        segment = "Possible Fit: WP with visible upload"
    elif platform in {"Shopify", "Wix", "Squarespace"}:
        segment = "Secondary Fit: non-WP platform"
    else:
        segment = "Review Manually"

    return segment, score


def compute_priority(
    wordpress_detected: bool,
    woocommerce_detected: bool,
    has_upload: bool,
    reachable: bool,
) -> Tuple[str, int]:
    """V1 priority tier: favors WP+Woo+no upload, then WP+no upload."""
    if not reachable:
        return "Unreachable", 0
    if wordpress_detected and woocommerce_detected and not has_upload:
        return "P1: WordPress + WooCommerce, no upload", 100
    if wordpress_detected and not has_upload:
        return "P2: WordPress, no upload", 85
    if wordpress_detected and woocommerce_detected and has_upload:
        return "P3: WordPress + WooCommerce, upload present", 60
    if wordpress_detected and has_upload:
        return "P4: WordPress, upload present", 50
    return "P5: Not WordPress (V1 secondary)", 15


def enrich_row(session: requests.Session, row: Dict[str, str]) -> Dict[str, str]:
    business_name = (row.get("business_name") or "").strip()
    website = normalize_url(row.get("website", ""))

    result_row = dict(row)
    result_row.update({
        "normalized_url": website,
        "final_url": "",
        "reachable": "no",
        "http_status": "",
        "platform": "Unknown",
        "ecommerce": "Unknown",
        "upload_present": "unknown",
        "upload_signals": "",
        "email_found": "",
        "fit_segment": "",
        "fit_score": "",
        "wordpress_detected": "no",
        "woocommerce_detected": "no",
        "priority_segment": "",
        "priority_score": "",
        "notes": "",
    })

    try:
        if not website:
            result_row["notes"] = "Missing website"
            result_row["priority_segment"] = "Unreachable"
            result_row["priority_score"] = "0"
            return result_row

        fetched = fetch_url(session, website)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        if not fetched.ok or not fetched.html:
            result_row["final_url"] = fetched.final_url
            result_row["http_status"] = str(fetched.status_code or "")
            result_row["notes"] = fetched.error or "Page not reachable or not HTML"
            result_row["priority_segment"] = "Unreachable"
            result_row["priority_score"] = "0"
            return result_row

        soup = BeautifulSoup(fetched.html, "html.parser")
        platform, ecommerce, wp_ok, woo_ok = detect_platform(
            fetched.html, soup, fetched.final_url, fetched.headers
        )
        has_upload, upload_signals = detect_upload_signals(fetched.html, soup, fetched.final_url)
        email = find_email_in_soup(soup, fetched.html)

        secondary = try_secondary_pages(session, fetched.final_url)

        if not email:
            email = secondary["secondary_email"]

        upload_signal_text = "; ".join(upload_signals[:8])
        if secondary["secondary_upload_signals"]:
            if upload_signal_text:
                upload_signal_text += "; " + secondary["secondary_upload_signals"]
            else:
                upload_signal_text = secondary["secondary_upload_signals"]

        has_upload = has_upload or bool(secondary["secondary_upload_signals"])

        segment, score = score_fit(
            platform=platform,
            ecommerce=ecommerce,
            has_upload=has_upload,
            reachable=True,
        )
        pri_seg, pri_score = compute_priority(
            wordpress_detected=wp_ok,
            woocommerce_detected=woo_ok,
            has_upload=has_upload,
            reachable=True,
        )

        result_row.update({
            "final_url": fetched.final_url,
            "reachable": "yes",
            "http_status": str(fetched.status_code or ""),
            "platform": platform,
            "ecommerce": ecommerce,
            "upload_present": "yes" if has_upload else "no",
            "upload_signals": upload_signal_text,
            "email_found": email,
            "fit_segment": segment,
            "fit_score": str(score),
            "wordpress_detected": "yes" if wp_ok else "no",
            "woocommerce_detected": "yes" if woo_ok else "no",
            "priority_segment": pri_seg,
            "priority_score": str(pri_score),
            "notes": business_name,
        })

        return result_row
    finally:
        # Geo + sort keys after all branches (Canada-wide columns, no extra fetching).
        apply_location_and_sort_columns(result_row)


async def enrich_row_async(
    session: aiohttp.ClientSession,
    row: Dict[str, str],
    semaphore: asyncio.Semaphore,
) -> Dict[str, str]:
    """Same enrichment as :func:`enrich_row`; uses async HTTP with shared concurrency cap."""
    business_name = (row.get("business_name") or "").strip()
    website = normalize_url(row.get("website", ""))

    result_row = dict(row)
    result_row.update({
        "normalized_url": website,
        "final_url": "",
        "reachable": "no",
        "http_status": "",
        "platform": "Unknown",
        "ecommerce": "Unknown",
        "upload_present": "unknown",
        "upload_signals": "",
        "email_found": "",
        "fit_segment": "",
        "fit_score": "",
        "wordpress_detected": "no",
        "woocommerce_detected": "no",
        "priority_segment": "",
        "priority_score": "",
        "notes": "",
    })

    try:
        if not website:
            result_row["notes"] = "Missing website"
            result_row["priority_segment"] = "Unreachable"
            result_row["priority_score"] = "0"
            return result_row

        fetched = await fetch_url_async(session, website, semaphore)

        if not fetched.ok or not fetched.html:
            result_row["final_url"] = fetched.final_url
            result_row["http_status"] = str(fetched.status_code or "")
            result_row["notes"] = fetched.error or "Page not reachable or not HTML"
            result_row["priority_segment"] = "Unreachable"
            result_row["priority_score"] = "0"
            return result_row

        soup = BeautifulSoup(fetched.html, "html.parser")
        platform, ecommerce, wp_ok, woo_ok = detect_platform(
            fetched.html, soup, fetched.final_url, fetched.headers
        )
        has_upload, upload_signals = detect_upload_signals(fetched.html, soup, fetched.final_url)
        email = find_email_in_soup(soup, fetched.html)

        secondary = await try_secondary_pages_async(session, fetched.final_url, semaphore)

        if not email:
            email = secondary["secondary_email"]

        upload_signal_text = "; ".join(upload_signals[:8])
        if secondary["secondary_upload_signals"]:
            if upload_signal_text:
                upload_signal_text += "; " + secondary["secondary_upload_signals"]
            else:
                upload_signal_text = secondary["secondary_upload_signals"]

        has_upload = has_upload or bool(secondary["secondary_upload_signals"])

        segment, score = score_fit(
            platform=platform,
            ecommerce=ecommerce,
            has_upload=has_upload,
            reachable=True,
        )
        pri_seg, pri_score = compute_priority(
            wordpress_detected=wp_ok,
            woocommerce_detected=woo_ok,
            has_upload=has_upload,
            reachable=True,
        )

        result_row.update({
            "final_url": fetched.final_url,
            "reachable": "yes",
            "http_status": str(fetched.status_code or ""),
            "platform": platform,
            "ecommerce": ecommerce,
            "upload_present": "yes" if has_upload else "no",
            "upload_signals": upload_signal_text,
            "email_found": email,
            "fit_segment": segment,
            "fit_score": str(score),
            "wordpress_detected": "yes" if wp_ok else "no",
            "woocommerce_detected": "yes" if woo_ok else "no",
            "priority_segment": pri_seg,
            "priority_score": str(pri_score),
            "notes": business_name,
        })

        return result_row
    finally:
        apply_location_and_sort_columns(result_row)


async def enrich_all_rows_async(
    rows: List[Dict[str, str]],
    *,
    db_conn: Optional[sqlite3.Connection] = None,
    db_upsert_qualified: Optional[Callable[[sqlite3.Connection, Dict[str, str]], None]] = None,
) -> List[Dict[str, str]]:
    """Process all rows concurrently (bounded by :data:`MAX_CONCURRENT`); preserve input order."""
    total = len(rows)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    slot: List[Optional[Dict[str, str]]] = [None] * total
    db_lock: Optional[asyncio.Lock] = asyncio.Lock() if db_conn is not None else None
    db_saved_count = 0

    if db_conn is not None:
        if db_upsert_qualified is None:
            raise ValueError("db_upsert_qualified is required when db_conn is set")

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:

        async def process_one(idx: int, row: Dict[str, str]) -> None:
            nonlocal db_saved_count
            i = idx + 1
            name = row.get("business_name", "").strip() or f"row {i}"
            print(f"[{i}/{total}] Processing: {name}")
            try:
                slot[idx] = await enrich_row_async(session, row, sem)
            except Exception as exc:
                failed_row = dict(row)
                failed_row["notes"] = f"Unhandled error: {exc}"
                failed_row.setdefault("wordpress_detected", "no")
                failed_row.setdefault("woocommerce_detected", "no")
                failed_row.setdefault("priority_segment", "Error")
                failed_row.setdefault("priority_score", "0")
                apply_location_and_sort_columns(failed_row)
                slot[idx] = failed_row
            if db_conn is not None and db_lock is not None:
                assert db_upsert_qualified is not None
                assert slot[idx] is not None
                async with db_lock:
                    try:
                        db_upsert_qualified(db_conn, slot[idx])
                        db_saved_count += 1
                        if db_saved_count % 50 == 0:
                            print(
                                f"DB: saved {db_saved_count} rows to qualified_leads",
                                flush=True,
                            )
                    except Exception as db_exc:
                        print(
                            f"DB upsert failed (row {i}, {name!r}): {db_exc}",
                            flush=True,
                        )
                        raise

        await asyncio.gather(*(process_one(i, r) for i, r in enumerate(rows)))

    out: List[Dict[str, str]] = []
    for i in range(total):
        r = slot[i]
        assert r is not None
        out.append(r)
    return out


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_csv(path: str, rows: List[Dict[str, str]]) -> None:
    if not rows:
        raise ValueError("No rows to write.")

    all_keys: set = set()
    for row in rows:
        all_keys.update(row.keys())
    ordered = [c for c in OUTPUT_COLUMN_PREFIX if c in all_keys]
    rest = sorted(k for k in all_keys if k not in ordered)
    fieldnames = ordered + rest

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    argv = sys.argv[1:]
    import db as dbmod

    argv, db_path = dbmod.strip_db_arg(argv)
    if len(argv) != 2:
        print("Usage: python lead_qualifier.py input.csv output.csv")
        return 1

    input_csv = argv[0]
    output_csv = argv[1]

    rows = read_csv(input_csv)
    if not rows:
        print("Input CSV is empty.")
        return 1

    db_conn: Optional[sqlite3.Connection] = None
    if db_path:
        dbmod.init_db(db_path)
        dbmod.migrate_db(db_path)
        db_conn = dbmod.get_connection(db_path)

    try:
        enriched_rows = asyncio.run(
            enrich_all_rows_async(
                rows,
                db_conn=db_conn,
                db_upsert_qualified=dbmod.upsert_qualified_lead if db_conn is not None else None,
            )
        )

        write_csv(output_csv, enriched_rows)
        print(f"Done. Wrote {len(enriched_rows)} rows to {output_csv}")
    finally:
        if db_conn is not None:
            db_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
