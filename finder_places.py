#!/usr/bin/env python3
"""
finder_places.py — CoreXUpload Prints, stage 1 (Finder)

WHAT THIS DOES
- Reads a seed CSV of Canadian cities (province, city).
- Runs Google Places Text Search for print-related queries per city (up to API page limit).
- Optionally fills missing website/phone via Place Details (one request per place).
- Dedupes and writes a CSV ready for lead_qualifier.py (stage 2).
- Stops early when MAX_OUTPUT_ROWS unique rows is reached (set to 0 for no limit).

SETUP — API key
- Create a Google Cloud API key with Places API enabled.
- Export it before running:

    export GOOGLE_MAPS_API_KEY="your_key_here"

  (On Windows PowerShell: $env:GOOGLE_MAPS_API_KEY = "your_key_here")

RUN — Finder (this script)
    python finder_places.py cities.csv leads_raw.csv

RUN — Finder, one CSV per province (no row cap; resume via .checkpoint files)
    python finder_places.py --all-provinces cities_canada.csv output/
    python finder_places.py --all-provinces cities_canada.csv output/ --daily-limit 800
    python finder_places.py --all-provinces cities_canada.csv output_bc/ --bc-only

RUN — Qualifier (existing script, stage 2)
    python lead_qualifier.py leads_raw.csv leads_enriched.csv

DEPENDENCIES
- Standard library + googlemaps only for this file:

    pip install googlemaps
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, MutableMapping, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from src.config.env import load_project_env

load_project_env()

import googlemaps
from googlemaps.exceptions import ApiError

# Reuse province normalization from lead_qualifier (same 2-letter CA codes).
# Decision: finder now depends on lead_qualifier at import time; keep requirements.txt complete.
from lead_qualifier import normalize_canadian_province

# -----------------------------
# Configuration
# -----------------------------

# Text search queries; {city} and {province} come from the seed CSV.
SEARCH_QUERY_TEMPLATES = [
    "print shop in {city} {province} Canada",
    "commercial printer in {city} {province} Canada",
    "fine art printing in {city} {province} Canada",
    "giclee printing in {city} {province} Canada",
]

SOURCE_LABEL = "google_places_text_search"

# Wait after receiving next_page_token before pagetoken request (Google: token often invalid if called too soon).
NEXT_PAGE_DELAY_SEC = 3.5

# INVALID_REQUEST on pagination: how many HTTP attempts per page (each page waits above first).
PAGINATION_MAX_ATTEMPTS = 3

# Pause between pagination retries when Google returns INVALID_REQUEST.
PAGINATION_RETRY_DELAY_SEC = 1.5

# Places Text Search returns at most 3 pages (~60 results) per query; this is the API limit.
MAX_TEXT_SEARCH_PAGES = 3

# Stop after this many unique rows (after dedupe). Set to 0 to run all cities/queries with no row cap.
MAX_OUTPUT_ROWS = 100

# `--all-provinces` mode processes groups in this order (2-letter codes).
ALL_PROVINCES_ORDER = ["BC", "AB", "ON", "QC", "MB", "SK", "NS", "NB", "NL", "PE"]

# Bias toward Canada in Places.
PLACES_REGION = "ca"

# Valid Place Details fields only (`types` is not allowed on Details — keep text-search `types`).
# Audit (legacy Places API via googlemaps):
# - Text Search returns by default (no fields param): e.g. geometry, icon, name, opening_hours,
#   photos, place_id, types, vicinity, formatted_address, business_status, plus_code, rating,
#   user_ratings_total, price_level (when available). We map all useful B2B fields below.
# - Place Details adds: contact, url, price_level, editorial_summary, primary `type`, serves_*,
#   vicinity, geometry, etc. No `products` catalog field in this API surface.
# - Skipped as low value for print B2B: plus_code, wheelchair_accessible_entrance, icon,
#   delivery/dine_in/takeout/curbside_pickup/reservable (restaurant ops; not mapping).
# - Always one Details request per place_id (cached) so detail-only columns populate.
PLACE_DETAIL_FIELDS = [
    "place_id",
    "name",
    "formatted_address",
    "formatted_phone_number",
    "international_phone_number",
    "website",
    "business_status",
    "opening_hours",
    "rating",
    "user_ratings_total",
    "url",
    "price_level",
    "vicinity",
    "geometry",
    "type",
    "editorial_summary",
    "serves_beer",
    "serves_breakfast",
    "serves_brunch",
    "serves_dinner",
    "serves_lunch",
    "serves_vegetarian_food",
    "serves_wine",
]

# Google atmosphere flags we export as individual CSV booleans (mixed-use / café+print signals).
SERVES_FIELD_KEYS = [
    "serves_beer",
    "serves_breakfast",
    "serves_brunch",
    "serves_dinner",
    "serves_lunch",
    "serves_vegetarian_food",
    "serves_wine",
]

# Stable column order for review and for piping into lead_qualifier.py.
OUTPUT_FIELDNAMES = [
    "business_name",
    "website",
    "address",
    "city",
    "province",
    "zip",
    "country",
    "source",
    "search_query",
    "place_id",
    "vicinity",
    "latitude",
    "longitude",
    "formatted_phone_number",
    "international_phone_number",
    "rating",
    "user_ratings_total",
    "price_level",
    "google_maps_url",
    "business_status",
    "opening_hours_available",
    "photos_count",
    "primary_type",
    "editorial_summary",
    "types",
    "serves_beer",
    "serves_breakfast",
    "serves_brunch",
    "serves_dinner",
    "serves_lunch",
    "serves_vegetarian_food",
    "serves_wine",
]


# -----------------------------
# Data classes
# -----------------------------


@dataclass
class CitySeed:
    province: str
    city: str


class QuotaExceeded(Exception):
    """Place Details daily limit reached (--all-provinces --daily-limit)."""


# -----------------------------
# Helpers
# -----------------------------


def load_api_key() -> str:
    load_project_env()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise SystemExit("Missing GOOGLE_MAPS_API_KEY environment variable.")
    return api_key.strip()


def geocode_center(
    client: googlemaps.Client, address: str
) -> Optional[Tuple[float, float]]:
    """Return lat/lng for an address, or None if Geocoding API is unavailable."""
    try:
        resp = client.geocode(address)
    except ApiError as exc:
        status = getattr(exc, "status", None) or str(exc)
        print(
            f"  ! Geocode skipped ({status}): using address-in-query search instead. "
            "Enable Geocoding API on your key for tighter radius bias."
        )
        return None
    if not resp:
        print(f"  ! Could not geocode {address!r}; using address-in-query search instead.")
        return None
    loc = resp[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


def load_query_templates(path: str) -> List[str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("--queries-json must contain a JSON array of query strings.")
    return [str(q).strip() for q in raw if str(q).strip()]


def read_cities_csv(path: str) -> List[CitySeed]:
    seeds: List[CitySeed] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            province = (row.get("province") or "").strip()
            city = (row.get("city") or "").strip()
            if city:
                seeds.append(CitySeed(province=province, city=city))
    return seeds


# Canadian postal code pattern (used before comma-splitting remainder).
_CA_POSTAL_RE = re.compile(r"\b([A-Z]\d[A-Z])\s*(\d[A-Z]\d)\b", re.IGNORECASE)


def parse_formatted_address_ca(formatted: str) -> Dict[str, str]:
    """Best-effort split of Google `formatted_address` for Canadian rows.

    Heuristic only: formats vary. `country` is always CA for this pipeline.
    """
    out = {"address": "", "city": "", "province": "", "zip": "", "country": "CA"}
    t = (formatted or "").strip()
    if not t:
        return out

    t = re.sub(r",?\s*Canada\s*$", "", t, flags=re.IGNORECASE).strip()
    zm = _CA_POSTAL_RE.search(t)
    zip_c = ""
    if zm:
        zip_c = f"{zm.group(1).upper()} {zm.group(2).upper()}"
        t = (t[: zm.start()] + t[zm.end() :]).strip()
        t = re.sub(r",\s*$", "", t).strip()

    parts = [p.strip() for p in t.split(",") if p.strip()]
    prov_raw = ""
    if len(parts) >= 3:
        out["address"] = parts[0]
        out["city"] = parts[1]
        prov_raw = parts[2]
    elif len(parts) == 2:
        out["address"] = parts[0]
        out["city"] = parts[1]
    elif len(parts) == 1:
        out["address"] = parts[0]

    out["province"] = normalize_canadian_province(prov_raw) if prov_raw else ""
    out["zip"] = zip_c
    return out


def _opening_hours_available(place: dict) -> str:
    """true/false: Google returned usable opening-hours metadata (search or details)."""
    oh = place.get("opening_hours")
    if not oh or not isinstance(oh, dict):
        return "false"
    if oh.get("weekday_text"):
        return "true"
    if oh.get("periods"):
        return "true"
    if oh.get("open_now") is not None:
        return "true"
    return "false"


def _csv_bool(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return ""


def _photos_count(place: dict) -> str:
    photos = place.get("photos")
    if isinstance(photos, list):
        return str(len(photos))
    return ""


def _lat_lng(place: dict) -> Tuple[str, str]:
    geo = place.get("geometry") or {}
    loc = geo.get("location") or {}
    lat, lng = loc.get("lat"), loc.get("lng")
    if lat is None or lng is None:
        return "", ""
    return str(lat), str(lng)


def _editorial_summary_text(place: dict) -> str:
    es = place.get("editorial_summary")
    if isinstance(es, dict):
        t = (es.get("overview") or "").replace("\n", " ").strip()
        return t
    if isinstance(es, str):
        return es.strip()
    return ""


def _serves_flags_row(place: dict) -> Dict[str, str]:
    return {k: _csv_bool(place.get(k)) for k in SERVES_FIELD_KEYS}


def _primary_type(place: dict) -> str:
    """Details field `type` when present; else first entry of text-search `types` list."""
    t = (place.get("type") or "").strip()
    if t:
        return t
    types = place.get("types")
    if isinstance(types, list) and types:
        return str(types[0]).strip()
    return ""


def _places_search(client: googlemaps.Client, **kwargs) -> dict:
    """Call Places Text Search; return a status/result dict.

    The googlemaps library raises ApiError for INVALID_REQUEST and most errors instead of
    returning JSON. If we let that propagate, the caller loses page-1 results already fetched.
    """
    try:
        return client.places(**kwargs)
    except ApiError as exc:
        msg = exc.message if getattr(exc, "message", None) else str(exc)
        return {
            "status": exc.status,
            "error_message": msg or "",
            "results": [],
        }


def _place_details(client: googlemaps.Client, place_id: str, fields: List[str]) -> dict:
    """Place Details; same ApiError normalization as text search."""
    try:
        return client.place(place_id, fields=fields)
    except ApiError as exc:
        msg = exc.message if getattr(exc, "message", None) else str(exc)
        return {
            "status": exc.status,
            "error_message": msg or "",
            "result": {},
        }


def fetch_text_search_pages(
    client: googlemaps.Client,
    query: str,
    *,
    location: Optional[Tuple[float, float]] = None,
    radius: Optional[int] = None,
) -> List[dict]:
    """Run Text Search and follow next_page_token up to Google's page limit.

    Does not raise: a failed query returns [] so the rest of cities/queries still run.

    Pagination rules (Google Places):
    - Never call `places(page_token=...)` in the same moment as receiving the token; wait first.
    - On INVALID_REQUEST, retry a few times with short delays, then skip that page only.
    """
    all_results: List[dict] = []
    search_kwargs: Dict[str, object] = {"query": query, "region": PLACES_REGION}
    if location is not None:
        search_kwargs["location"] = location
    if radius is not None:
        search_kwargs["radius"] = radius
    resp = _places_search(client, **search_kwargs)
    st = resp.get("status")
    if st == "ZERO_RESULTS":
        return all_results
    if st != "OK":
        msg = (resp.get("error_message") or "").strip()
        print(f"  ! Text search skipped ({st}): {query[:56]}... {msg}".strip())
        return all_results

    all_results.extend(resp.get("results") or [])

    # Page 1 is the response above; Google allows up to MAX_TEXT_SEARCH_PAGES total.
    pages_fetched = 1
    while resp.get("next_page_token") and pages_fetched < MAX_TEXT_SEARCH_PAGES:
        next_page = pages_fetched + 1
        print(
            f"  Pagination: next_page_token present — waiting {NEXT_PAGE_DELAY_SEC}s "
            f"before requesting page {next_page}/{MAX_TEXT_SEARCH_PAGES}..."
        )
        time.sleep(NEXT_PAGE_DELAY_SEC)

        next_page_token = resp["next_page_token"]
        page_resp: Optional[dict] = None

        for attempt in range(1, PAGINATION_MAX_ATTEMPTS + 1):
            # Pagination must use page_token only (no query string).
            page_resp = _places_search(client, query=None, page_token=next_page_token)
            last_status = page_resp.get("status") or ""
            if last_status == "OK":
                if attempt > 1:
                    print(f"  Pagination: page {next_page} OK on attempt {attempt}/{PAGINATION_MAX_ATTEMPTS}.")
                break

            if last_status == "INVALID_REQUEST" and attempt < PAGINATION_MAX_ATTEMPTS:
                print(
                    f"  Pagination: INVALID_REQUEST on page {next_page} "
                    f"(attempt {attempt}/{PAGINATION_MAX_ATTEMPTS}) — "
                    f"retrying in {PAGINATION_RETRY_DELAY_SEC}s..."
                )
                time.sleep(PAGINATION_RETRY_DELAY_SEC)
                continue

            err = (page_resp.get("error_message") or "").strip()
            print(
                f"  Pagination: stopping extra pages for this query after status={last_status} "
                f"(page {next_page}, attempt {attempt}/{PAGINATION_MAX_ATTEMPTS}) {err}".strip()
            )
            page_resp = None
            break

        if not page_resp or page_resp.get("status") != "OK":
            print(
                f"  Pagination: skipping remaining pages for this query; "
                f"keeping {len(all_results)} result(s) collected so far."
            )
            break

        resp = page_resp
        all_results.extend(resp.get("results") or [])
        pages_fetched += 1

    return all_results


def _merge_place_and_detail(place: dict, detail: dict) -> dict:
    """Merge Details into text-search hit; keep `types` from text search only."""
    types_value = place.get("types", [])
    merged = {**place, **detail}
    merged["types"] = types_value
    return merged


def enrich_place_with_details(
    client: googlemaps.Client,
    place: dict,
    cache: Dict[str, dict],
    quota: Optional[MutableMapping[str, int]] = None,
) -> dict:
    """Merge Place Details once per place_id (cached) for B2B columns not in Text Search alone.

    Details request must not include `types` (invalid on Details); category types stay
    from the text search result via _merge_place_and_detail.

    Optional `quota`: ``{"limit": int, "used": int}`` — increment ``used`` per Details HTTP
    call (miss only); if ``used >= limit`` before a call, raises QuotaExceeded.
    """
    place_id = (place.get("place_id") or "").strip()
    if not place_id:
        return dict(place)

    if place_id in cache:
        return _merge_place_and_detail(place, cache[place_id])

    if quota is not None:
        lim = int(quota.get("limit") or 0)
        if lim and int(quota.get("used") or 0) >= lim:
            raise QuotaExceeded()

    resp = _place_details(client, place_id, PLACE_DETAIL_FIELDS)
    if quota is not None:
        quota["used"] = int(quota.get("used") or 0) + 1
    # Details can fail per-place (e.g. NOT_FOUND); keep text-search row as-is.
    if resp.get("status") != "OK":
        cache[place_id] = {}
        return dict(place)

    detail = dict(resp.get("result") or {})
    cache[place_id] = detail
    return _merge_place_and_detail(place, detail)


def google_maps_url_for(place: dict) -> str:
    explicit = (place.get("url") or "").strip()
    if explicit:
        return explicit
    place_id = (place.get("place_id") or "").strip()
    if place_id:
        return f"https://www.google.com/maps/search/?api=1&query_place_id={place_id}"
    return ""


def place_hit_to_row(
    place: dict,
    *,
    seed: CitySeed,
    search_query: str,
) -> Dict[str, str]:
    """Build one output row dict aligned with OUTPUT_FIELDNAMES."""
    types_value = place.get("types", [])
    types_str = ";".join(types_value) if isinstance(types_value, list) else str(types_value)

    rating = place.get("rating")
    urt = place.get("user_ratings_total")
    formatted_address = (place.get("formatted_address") or "").strip()
    parsed = parse_formatted_address_ca(formatted_address)

    # Decision: prefer parsed address fields; fall back to search seed for city/province when parse is empty.
    city_out = parsed["city"] or seed.city
    prov_out = parsed["province"] or normalize_canadian_province(seed.province)

    fmt_phone = (place.get("formatted_phone_number") or "").strip()
    intl_phone = (place.get("international_phone_number") or "").strip()

    lat_s, lng_s = _lat_lng(place)
    price_level = place.get("price_level")
    price_level_s = "" if price_level is None else str(price_level)
    serves = _serves_flags_row(place)

    row: Dict[str, str] = {
        "business_name": (place.get("name") or "").strip(),
        "website": (place.get("website") or "").strip(),
        "address": parsed["address"],
        "city": city_out,
        "province": prov_out,
        "zip": parsed["zip"],
        "country": "CA",
        "source": SOURCE_LABEL,
        "search_query": search_query,
        "place_id": (place.get("place_id") or "").strip(),
        "formatted_address": formatted_address,
        "vicinity": (place.get("vicinity") or "").strip(),
        "latitude": lat_s,
        "longitude": lng_s,
        "formatted_phone_number": fmt_phone,
        "international_phone_number": intl_phone,
        "rating": "" if rating is None else str(rating),
        "user_ratings_total": "" if urt is None else str(urt),
        "price_level": price_level_s,
        "google_maps_url": google_maps_url_for(place),
        "business_status": (place.get("business_status") or "").strip(),
        "opening_hours_available": _opening_hours_available(place),
        "photos_count": _photos_count(place),
        "primary_type": _primary_type(place),
        "editorial_summary": _editorial_summary_text(place),
        "types": types_str,
    }
    row.update(serves)
    return row


def normalize_website_key(url: str) -> str:
    """Host-level key for deduping (no scheme/path noise)."""
    u = (url or "").strip().lower()
    if not u:
        return ""
    u = u.removeprefix("https://").removeprefix("http://")
    u = u.split("/")[0]
    if u.startswith("www."):
        u = u[4:]
    return u


def name_address_key(name: str, address: str) -> str:
    return f"{name.strip().lower()}|{address.strip().lower()}"


def dedupe_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Dedupe by place_id, then website host, then name + formatted_address."""
    seen_place: set = set()
    seen_web: set = set()
    seen_name_addr: set = set()
    out: List[Dict[str, str]] = []

    for r in rows:
        pid = (r.get("place_id") or "").strip()
        if pid and pid in seen_place:
            continue

        wkey = normalize_website_key(r.get("website") or "")
        if wkey and wkey in seen_web:
            continue

        nak = name_address_key(r.get("business_name") or "", r.get("formatted_address") or "")
        if nak != "|" and nak in seen_name_addr:
            continue

        if pid:
            seen_place.add(pid)
        if wkey:
            seen_web.add(wkey)
        if nak != "|":
            seen_name_addr.add(nak)
        out.append(r)

    return out


def write_output_csv(path: str, rows: List[Dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _persist_leads_sqlite(conn: object, rows: List[Dict[str, str]]) -> None:
    import db as dbmod

    for r in rows:
        dbmod.upsert_lead(conn, r)  # type: ignore[arg-type]


def run_finder_for_seeds(
    client: googlemaps.Client,
    seeds: List[CitySeed],
    output_path: str,
    max_output_rows: int,
    db_conn: Optional[object] = None,
    *,
    query_templates: Optional[List[str]] = None,
    location_bias: Optional[Tuple[float, float]] = None,
    radius_meters: Optional[int] = None,
) -> int:
    """Execute the finder loop for `seeds`, write `output_path`, return unique row count."""
    detail_cache: Dict[str, dict] = {}
    collected: List[Dict[str, str]] = []
    total_n = len(seeds)
    reached_cap = False
    templates = query_templates or SEARCH_QUERY_TEMPLATES

    for idx, seed in enumerate(seeds, start=1):
        prov_disp = seed.province or "?"
        print(f"[{idx}/{total_n}] Searching {seed.city}, {prov_disp}")

        city_hits = 0
        for template in templates:
            if "{city}" in template or "{province}" in template:
                query = template.format(city=seed.city, province=seed.province)
            else:
                query = template
            try:
                hits = fetch_text_search_pages(
                    client,
                    query,
                    location=location_bias,
                    radius=radius_meters,
                )
            except Exception as exc:
                print(f"  ! Query failed ({query[:40]}...): {exc}")
                continue

            for place in hits:
                enriched = enrich_place_with_details(client, place, detail_cache)
                collected.append(place_hit_to_row(enriched, seed=seed, search_query=query))
                city_hits += 1

            unique = dedupe_rows(collected)
            if max_output_rows and len(unique) >= max_output_rows:
                collected = unique[:max_output_rows]
                print(f"Reached MAX_OUTPUT_ROWS={max_output_rows}, stopping early.")
                reached_cap = True
                break

            time.sleep(0.25)

        print(f"Found {city_hits} results")
        print(f"Current unique rows: {len(dedupe_rows(collected))}")

        if reached_cap:
            break

    unique = dedupe_rows(collected)
    if max_output_rows:
        unique = unique[:max_output_rows]
    write_output_csv(output_path, unique)
    print(f"Wrote {len(unique)} rows to {output_path}")
    if db_conn is not None:
        _persist_leads_sqlite(db_conn, unique)
    return len(unique)


def _province_csv_is_empty_or_missing(path: str) -> bool:
    if not os.path.isfile(path):
        return True
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return True
        for _ in reader:
            return False
        return True


def _read_output_csv_rows(path: str) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [{k: (row.get(k) or "") for k in OUTPUT_FIELDNAMES} for row in rows]


def _read_checkpoint_cities(path: str) -> set:
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def _append_checkpoint_line(path: str, city: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(city + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def _append_error_log(log_path: str, province_code: str, city: str, message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts}\t{province_code}\t{city}\t{message}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def run_resumable_province(
    client: googlemaps.Client,
    *,
    prov_code: str,
    prov_seeds: List[CitySeed],
    out_path: str,
    checkpoint_path: str,
    errors_log_path: str,
    quota: Optional[MutableMapping[str, int]],
    db_conn: Optional[object] = None,
) -> Tuple[int, int, int, bool]:
    """Run or resume one province: per-city checkpoint, incremental CSV, errors log.

    Appends a city name to the checkpoint file **after** that city finishes successfully
    (a crash mid-city does not append, so resume retries that city).

    Returns:
        (lead_count, cities_ok_count, cities_err_count, quota_exceeded)
        cities_ok_count is ``n_total - cities_err_count`` for the province seed list.
    """
    detail_cache: Dict[str, dict] = {}
    collected: List[Dict[str, str]] = []
    completed_cities = set()

    csv_nonempty = os.path.isfile(out_path) and not _province_csv_is_empty_or_missing(out_path)
    if csv_nonempty and os.path.isfile(checkpoint_path):
        collected = _read_output_csv_rows(out_path)
        completed_cities = _read_checkpoint_cities(checkpoint_path)

    n_total = len(prov_seeds)
    cities_err = 0
    quota_exceeded = False

    for pos, seed in enumerate(prov_seeds, start=1):
        if seed.city in completed_cities:
            continue

        print(
            f"  [{pos}/{n_total}] {seed.city} {prov_code} — "
            f"running {len(SEARCH_QUERY_TEMPLATES)} queries..."
        )
        city_hits = 0
        city_failed = False
        try:
            for template in SEARCH_QUERY_TEMPLATES:
                query = template.format(city=seed.city, province=seed.province)
                try:
                    hits = fetch_text_search_pages(client, query)
                except Exception as exc:
                    raise RuntimeError(f"text search failed: {exc!r}") from exc

                for place in hits:
                    try:
                        enriched = enrich_place_with_details(client, place, detail_cache, quota=quota)
                    except QuotaExceeded:
                        raise
                    except Exception as exc:
                        raise RuntimeError(f"enrich/row failed: {exc!r}") from exc
                    collected.append(place_hit_to_row(enriched, seed=seed, search_query=query))
                    city_hits += 1

                collected = dedupe_rows(collected)
                time.sleep(0.25)
        except QuotaExceeded:
            uq = dedupe_rows(collected)
            write_output_csv(out_path, uq)
            if db_conn is not None:
                _persist_leads_sqlite(db_conn, uq)
            quota_exceeded = True
            break
        except Exception as exc:
            cities_err += 1
            city_failed = True
            msg = str(exc) or repr(exc)
            print(f"  ! City error {seed.city}: {msg}")
            _append_error_log(errors_log_path, prov_code, seed.city, msg)
        else:
            print(f"  [{pos}/{n_total}] {seed.city} {prov_code} — {city_hits} leads found")

        if quota_exceeded:
            break

        if not city_failed:
            _append_checkpoint_line(checkpoint_path, seed.city)
            uq = dedupe_rows(collected)
            write_output_csv(out_path, uq)
            if db_conn is not None:
                _persist_leads_sqlite(db_conn, uq)

    if quota_exceeded:
        return len(dedupe_rows(collected)), n_total - cities_err, cities_err, True

    unique = dedupe_rows(collected)
    write_output_csv(out_path, unique)
    if db_conn is not None:
        _persist_leads_sqlite(db_conn, unique)
    n_leads = len(unique)

    # Drop checkpoint only when every city in the seed list finished without error this cycle,
    # so a province with errors keeps .checkpoint + CSV and remains resumable (not "skip" next run).
    if cities_err == 0 and os.path.isfile(checkpoint_path):
        try:
            os.remove(checkpoint_path)
        except OSError:
            pass

    return n_leads, n_total - cities_err, cities_err, False


def _seeds_by_province(all_seeds: List[CitySeed]) -> Tuple[Dict[str, List[CitySeed]], List[CitySeed]]:
    """Group seeds by normalized 2-letter province; return (groups, skipped_unknown)."""
    groups: Dict[str, List[CitySeed]] = {code: [] for code in ALL_PROVINCES_ORDER}
    skipped: List[CitySeed] = []
    for seed in all_seeds:
        code = normalize_canadian_province(seed.province)
        if not code or code not in groups:
            skipped.append(seed)
            continue
        groups[code].append(seed)
    return groups, skipped


def _parse_finder_options(argv: List[str]) -> Tuple[List[str], Optional[str], Optional[str], Optional[int]]:
    """Strip optional --queries-json / --geo-center / --geo-radius-m flags."""
    queries_json: Optional[str] = None
    geo_center: Optional[str] = None
    geo_radius_m: Optional[int] = None
    rest: List[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--queries-json" and i + 1 < len(argv):
            queries_json = argv[i + 1]
            i += 2
            continue
        if arg == "--geo-center" and i + 1 < len(argv):
            geo_center = argv[i + 1]
            i += 2
            continue
        if arg == "--geo-radius-m" and i + 1 < len(argv):
            try:
                geo_radius_m = int(argv[i + 1])
            except ValueError:
                print("Error: --geo-radius-m must be an integer.")
                raise SystemExit(1)
            i += 2
            continue
        rest.append(arg)
        i += 1
    return rest, queries_json, geo_center, geo_radius_m


def main() -> int:
    argv = sys.argv[1:]
    import db as dbmod

    argv, db_path = dbmod.strip_db_arg(argv)
    db_conn: Optional[object] = None
    bc_only = "--bc-only" in argv
    argv = [a for a in argv if a != "--bc-only"]

    daily_limit: Optional[int] = None
    if len(argv) >= 2 and argv[-2] == "--daily-limit":
        try:
            daily_limit = int(argv[-1])
        except ValueError:
            print("Error: --daily-limit must be an integer.")
            return 1
        if daily_limit < 1:
            print("Error: --daily-limit must be at least 1.")
            return 1
        argv = argv[:-2]

    argv, queries_json, geo_center, geo_radius_m = _parse_finder_options(argv)

    if len(argv) == 3 and argv[0] == "--all-provinces":
        cities_path = argv[1]
        output_dir = argv[2]
        output_path = ""
    elif len(argv) == 2:
        cities_path, output_path = argv[0], argv[1]
        output_dir = ""
    else:
        print(
            "Usage:\n"
            "  python finder_places.py cities.csv output.csv\n"
            "  python finder_places.py --all-provinces cities_canada.csv output/\n"
            "  python finder_places.py --all-provinces cities_canada.csv output/ --daily-limit 800\n"
            "  python finder_places.py --all-provinces cities_canada.csv output_bc/ --bc-only"
        )
        return 1

    if bc_only and not output_dir:
        print("Error: --bc-only is only valid with --all-provinces.")
        return 1

    if daily_limit is not None and not output_dir:
        print("Error: --daily-limit is only valid with --all-provinces.")
        return 1

    api_key = load_api_key()
    all_seeds = read_cities_csv(cities_path)
    if not all_seeds:
        print("No cities found in seed CSV (need non-empty city column).")
        return 1

    client = googlemaps.Client(key=api_key)

    query_templates = load_query_templates(queries_json) if queries_json else None
    location_bias: Optional[Tuple[float, float]] = None
    if geo_center:
        location_bias = geocode_center(client, geo_center)
        if location_bias is not None:
            print(f"Geo bias: {geo_center} → {location_bias[0]:.5f}, {location_bias[1]:.5f}")
            if geo_radius_m:
                print(f"Search radius: {geo_radius_m} m")

    if db_path:
        dbmod.init_db(db_path)
        db_conn = dbmod.get_connection(db_path)

    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            groups, skipped = _seeds_by_province(all_seeds)
            for seed in skipped:
                print(
                    f"Warning: skipping city {seed.city!r} — unknown or non-target province {seed.province!r}"
                )

            errors_log_path = os.path.join(output_dir, "errors.log")
            quota: Optional[MutableMapping[str, int]] = None
            if daily_limit is not None:
                quota = {"limit": daily_limit, "used": 0}

            province_run_order = ["BC"] if bc_only else ALL_PROVINCES_ORDER

            for prov_code in province_run_order:
                prov_seeds = groups[prov_code]
                out_name = f"leads_{prov_code.lower()}.csv"
                out_path = os.path.join(output_dir, out_name)
                checkpoint_path = os.path.join(output_dir, f"leads_{prov_code.lower()}.checkpoint")

                if not prov_seeds:
                    print(f"Province {prov_code} skipped — no cities in seed.")
                    continue

                csv_nonempty = os.path.isfile(out_path) and not _province_csv_is_empty_or_missing(out_path)
                if csv_nonempty and not os.path.isfile(checkpoint_path):
                    print(f"Warning: {out_path} already exists — skipping province {prov_code}.")
                    continue

                if os.path.isfile(checkpoint_path) and (
                    not os.path.isfile(out_path) or _province_csv_is_empty_or_missing(out_path)
                ):
                    try:
                        os.remove(checkpoint_path)
                    except OSError:
                        pass
                    print(
                        f"Warning: stale checkpoint for {prov_code} removed "
                        f"(CSV missing or empty); starting province fresh."
                    )

                n_leads, n_ok, n_err, hit_quota = run_resumable_province(
                    client,
                    prov_code=prov_code,
                    prov_seeds=prov_seeds,
                    out_path=out_path,
                    checkpoint_path=checkpoint_path,
                    errors_log_path=errors_log_path,
                    quota=quota,
                    db_conn=db_conn,
                )

                if hit_quota:
                    lim = int((quota or {}).get("limit") or 0)
                    used = int((quota or {}).get("used") or 0)
                    print(
                        f"Warning: daily Place Details limit reached ({used}/{lim} calls). "
                        f"Progress saved; checkpoints preserved. Exiting."
                    )
                    return 0

                err_w = "error" if n_err == 1 else "errors"
                print(
                    f"Province {prov_code} complete — {n_leads} leads written. "
                    f"{n_ok}/{len(prov_seeds)} cities OK, {n_err} {err_w}."
                )

            return 0
        finally:
            if db_conn is not None:
                db_conn.close()

    try:
        run_finder_for_seeds(
            client,
            all_seeds,
            output_path,
            max_output_rows=MAX_OUTPUT_ROWS,
            db_conn=db_conn,
            query_templates=query_templates,
            location_bias=location_bias,
            radius_meters=geo_radius_m,
        )
    finally:
        if db_conn is not None:
            db_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
