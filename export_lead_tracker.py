#!/usr/bin/env python3
"""
export_lead_tracker.py — Convert lead_qualifier output CSV to Google Sheets Lead Tracker layout.

Usage:
    python export_lead_tracker.py output_bc/leads_bc_enriched.csv output_bc/leads_bc_tracker.csv

Reads any enriched CSV from lead_qualifier.py (DictReader; missing columns become empty).
Writes **data rows only** (no header line): same column order as the CoreX Lead Tracker
template (blank first column + CRM columns). Import into a sheet that already has the header,
or paste below row 1. Workflow columns are left empty for manual fill in Sheets.
"""

from __future__ import annotations

import csv
import sys
from typing import Dict, List

import db as _db_for_headers

# Column order matches CoreX Lead Tracker (leading blank column for Sheets).
# Values for dropdown columns align with "Dropdown Values" reference (controlled vocabulary).
# Single source of truth: db.TRACKER_CSV_HEADERS (also used for SQLite tracker_rows).
LEAD_TRACKER_FIELDNAMES: List[str] = list(_db_for_headers.TRACKER_CSV_HEADERS)


def _s(row: Dict[str, str], *keys: str) -> str:
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            return v
    return ""


def _int_score(raw: str) -> int:
    try:
        return int(str(raw).strip())
    except ValueError:
        return -1


def _map_business_type(row: Dict[str, str]) -> str:
    """Map to Lead Tracker dropdown: Print Shop, Fine Art Printer, Commercial Printer, Photo Lab, Design Studio."""
    hay = _haystack(row)
    pt = (_s(row, "primary_type") or "").lower()
    sq = (_s(row, "search_query") or "").lower()
    name = (_s(row, "business_name") or "").lower()

    if ("design studio" in name or pt == "design" or "design agency" in hay) and "print" not in hay:
        return "Design Studio"
    if "photo lab" in hay or "photo" in pt or ("lab" in name and "photo" in name):
        return "Photo Lab"
    if (
        "art_gallery" in pt
        or "giclee" in hay
        or "fine art" in hay
        or "art gallery" in hay
        or "art print" in hay
    ):
        return "Fine Art Printer"
    if "commercial printer" in sq or "commercial print" in hay:
        return "Commercial Printer"
    return "Print Shop"


def _map_fit_tier(fit_segment: str) -> str:
    """Tier 1 (Ideal), Tier 2 (Good), Tier 3 (Low)."""
    t = (fit_segment or "").lower()
    if "strong fit" in t:
        return "Tier 1 (Ideal)"
    if "secondary fit" in t or "possible fit" in t:
        return "Tier 2 (Good)"
    if "review manually" in t or "unreachable" in t:
        return "Tier 3 (Low)"
    if fit_segment.strip():
        return "Tier 2 (Good)"
    return ""


def _map_priority_score_sheet(priority_score: str) -> str:
    """1 - Low … 4 - Urgent (sheet scale); qualifier uses higher score = better lead."""
    s = _int_score(priority_score)
    if s <= 0:
        return "1 - Low"
    if s < 50:
        return "1 - Low"
    if s < 60:
        return "2 - Medium"
    if s < 85:
        return "3 - High"
    return "4 - Urgent"


def _map_website_platform(platform: str) -> str:
    p = (platform or "").strip()
    if not p or p.lower() == "unknown":
        return ""
    allowed = ("WordPress", "Squarespace", "Wix", "Shopify", "Custom", "Other")
    for a in allowed:
        if p.lower() == a.lower():
            return a
    return "Other"


def _map_ecommerce_platform(ecom: str) -> str:
    e = (ecom or "").strip()
    if not e or e.lower() == "unknown":
        return ""
    allowed = ("Shopify", "WooCommerce", "Magento", "BigCommerce", "None")
    for a in allowed:
        if e.lower() == a.lower():
            return a
    return ""


def _map_has_ecommerce(ecom: str) -> str:
    """Yes / No / Unknown — matches Yes/No column vocabulary."""
    if not (ecom or "").strip() or (ecom or "").strip().lower() == "unknown":
        return "Unknown"
    if (ecom or "").strip().lower() in {"none", "no"}:
        return "No"
    return "Yes"


def _map_yes_no_optional(val: str) -> str:
    v = (val or "").strip().lower()
    if v == "yes":
        return "Yes"
    if v == "no":
        return "No"
    return ""


def _haystack(row: Dict[str, str]) -> str:
    parts = [
        _s(row, "business_name"),
        _s(row, "types"),
        _s(row, "primary_type"),
        _s(row, "upload_signals"),
        _s(row, "editorial_summary"),
    ]
    return " ".join(parts).lower()


def _keyword_yes_no(hay: str, *words: str) -> str:
    if any(w in hay for w in words):
        return "Yes"
    return ""


def enriched_row_to_tracker(row: Dict[str, str]) -> Dict[str, str]:
    hay = _haystack(row)
    website = _s(row, "website", "normalized_url")
    phone = _s(row, "formatted_phone_number", "international_phone_number")
    ecom = _s(row, "ecommerce")
    fit_seg = _s(row, "fit_segment")
    pri_seg = _s(row, "priority_segment")
    pri_score = _s(row, "priority_score")

    notes_core = _s(row, "notes")
    extra = []
    if _s(row, "reachable") == "no":
        extra.append("Unreachable site")
    if _s(row, "wordpress_detected").lower() == "yes":
        extra.append("WordPress detected")
    if _s(row, "upload_present").lower() == "yes":
        extra.append("Upload UI detected")
    if pri_seg and pri_seg not in notes_core:
        extra.append(f"Qualifier: {pri_seg}")
    if fit_seg and fit_seg not in notes_core and _map_fit_tier(fit_seg) == "":
        extra.append(f"Fit: {fit_seg}")
    notes_out = notes_core
    if extra:
        suffix = " | " + "; ".join(extra)
        if suffix.strip(" |") not in notes_core:
            notes_out = (notes_core + suffix).strip(" |")

    out: Dict[str, str] = {k: "" for k in LEAD_TRACKER_FIELDNAMES}
    out[""] = ""
    out["Company Name"] = _s(row, "business_name")
    out["Website"] = website
    out["Contact Email"] = _s(row, "email_found")
    out["Contact Phone"] = phone
    out["Business Type"] = _map_business_type(row)
    out["Has E-commerce?"] = _map_has_ecommerce(ecom)
    out["E-Commerce Platform"] = _map_ecommerce_platform(ecom)
    out["Offers Framing?"] = _map_yes_no_optional(_keyword_yes_no(hay, "frame", "framing", "matting"))
    out["Offers Canvas?"] = _map_yes_no_optional(_keyword_yes_no(hay, "canvas", "giclee"))
    out["Offers Acrylic?"] = _map_yes_no_optional(_keyword_yes_no(hay, "acrylic", "plexi", "plexiglass"))
    out["Fit Tier"] = _map_fit_tier(fit_seg)
    out["Notes"] = notes_out
    out["Address"] = _s(row, "address")
    out["City"] = _s(row, "city")
    out["State"] = _s(row, "province_normalized", "province")
    out["Zip"] = _s(row, "zip")
    out["Country"] = _s(row, "country") or "CA"
    src = _s(row, "source")
    out["Lead Source"] = f"{src} + qualifier" if src else "qualifier"
    out["Priority Score"] = _map_priority_score_sheet(pri_score)
    out["Segment"] = ""
    out["Website Platform"] = _map_website_platform(_s(row, "platform"))
    out["Target Segment"] = ""
    out["Source"] = "Outbound"
    return out


def read_enriched(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_tracker(path: str, rows: List[Dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEAD_TRACKER_FIELDNAMES, extrasaction="ignore")
        w.writerows(rows)


def main() -> int:
    argv = sys.argv[1:]
    import db as dbmod

    argv, db_path = dbmod.strip_db_arg(argv)
    if len(argv) != 2:
        print("Usage: python export_lead_tracker.py input_enriched.csv output_tracker.csv")
        return 1
    inp, outp = argv[0], argv[1]
    data = read_enriched(inp)
    if not data:
        print("Input CSV is empty.")
        return 1
    tracker_rows = [enriched_row_to_tracker(r) for r in data]
    write_tracker(outp, tracker_rows)
    print(f"Wrote {len(tracker_rows)} rows to {outp}")

    if db_path:
        dbmod.init_db(db_path)
        conn = dbmod.get_connection(db_path)
        try:
            dbmod.log_export(conn, len(tracker_rows), outp, "export_lead_tracker")
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
