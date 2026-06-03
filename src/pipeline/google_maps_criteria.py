"""Map Generate List wizard criteria to Google Maps finder inputs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GoogleMapsFinderPlan:
    cities_csv: Path
    query_templates: List[str]
    geo_center: Optional[str] = None
    geo_radius_meters: Optional[int] = None
    provinces: Optional[List[str]] = None
    # When True, finder may call Geocoding API for lat/lng bias (requires API enablement).
    geocode_bias: bool = False


def _extract_keywords(criteria: Dict[str, Any], *, list_name: str = "") -> List[str]:
    raw = criteria.get("keywords")
    if isinstance(raw, list):
        keywords = [str(k).strip() for k in raw if str(k).strip()]
        if keywords:
            return keywords
    intent = criteria.get("intent")
    if isinstance(intent, dict):
        ik = intent.get("keywords")
        if isinstance(ik, list):
            keywords = [str(k).strip() for k in ik if str(k).strip()]
            if keywords:
                return keywords
    name = str(list_name or "").strip()
    if name:
        return [name]
    firm = criteria.get("firmographics")
    if isinstance(firm, dict):
        industry = str(firm.get("industry") or "").strip()
        if industry:
            return [industry]
    if isinstance(intent, dict):
        for key in ("category_label", "category_path"):
            label = str(intent.get(key) or "").strip()
            if label:
                return [label.split(">")[-1].strip()]
    return []


def _location_block(criteria: Dict[str, Any]) -> Dict[str, Any]:
    loc = criteria.get("location")
    return dict(loc) if isinstance(loc, dict) else {}


def _radius_meters(location: Dict[str, Any]) -> int:
    unit = str(location.get("radius_unit") or "km").strip().lower()
    try:
        value = float(location.get("radius_value"))
    except (TypeError, ValueError):
        value = 25.0
    if unit in ("mi", "mile", "miles"):
        return max(500, int(value * 1609.34))
    return max(500, int(value * 1000))


def _write_geo_seed(work_dir: Path, center: str, province: str = "") -> Path:
    seed = work_dir / "geo_seed.csv"
    with seed.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["province", "city"])
        writer.writeheader()
        writer.writerow({"province": province, "city": center})
    return seed


def _write_region_seed(work_dir: Path, region_name: str, country: str) -> Path:
    label = region_name.strip() or country.strip() or "Canada"
    return _write_geo_seed(work_dir, label)


def build_query_templates(keywords: List[str], *, geo_bias: bool) -> List[str]:
    """Return finder query strings; empty list means use finder defaults."""
    if not keywords:
        return []
    if geo_bias:
        return list(keywords)
    return [f"{kw} in {{city}} {{province}} Canada" for kw in keywords]


def build_radius_query_templates(keywords: List[str], center: str) -> List[str]:
    """
    Places Text Search queries scoped to an address.

    Embeds the address in the query so Geocoding API is not required.
    """
    center = center.strip()
    if not center:
        return build_query_templates(keywords, geo_bias=True)
    if not keywords:
        return []
    return [f"{kw} near {center}" for kw in keywords]


def build_google_maps_finder_plan(
    criteria: Dict[str, Any],
    *,
    root: Path,
    work_dir: Path,
    default_cities: Path,
    list_name: str = "",
) -> GoogleMapsFinderPlan:
    """
    Translate UI criteria (Google Ads–style audience fields) into finder inputs.

    Signals consumed:
    - location.scope: radius | country | region
    - location.radius_center / criteria.postal_code
    - location.radius_value / radius_unit
    - location.countries, location.region_or_city
    - intent.keywords / criteria.keywords
    - criteria.provinces (legacy province filter on cities.csv)
    """
    location = _location_block(criteria)
    scope = str(location.get("scope") or criteria.get("geo_region") or "").strip().lower()
    keywords = _extract_keywords(criteria, list_name=list_name)
    provinces = criteria.get("provinces") or []
    province_filter = [str(p).strip() for p in provinces if str(p).strip()]

    center = (
        str(location.get("radius_center") or criteria.get("postal_code") or "").strip()
    )
    region_name = str(location.get("region_or_city") or "").strip()
    country = str(location.get("country") or "Canada").strip()

    if scope == "radius" and center:
        radius_m = _radius_meters(location)
        seed = _write_geo_seed(work_dir, center)
        return GoogleMapsFinderPlan(
            cities_csv=seed,
            query_templates=build_radius_query_templates(keywords, center),
            geo_center=center,
            geo_radius_meters=radius_m,
            provinces=province_filter or None,
            geocode_bias=False,
        )

    if scope in ("region", "city") and (region_name or center):
        label = region_name or center
        seed = _write_region_seed(work_dir, label, country)
        return GoogleMapsFinderPlan(
            cities_csv=seed,
            query_templates=build_query_templates(keywords, geo_bias=False),
            provinces=province_filter or None,
        )

    if scope == "country":
        countries = location.get("countries") or []
        extra = location.get("countries_extra") or []
        labels = [str(c).strip() for c in list(countries) + list(extra) if str(c).strip()]
        if labels and labels != ["CA"]:
            seed = _write_geo_seed(work_dir, ", ".join(labels))
            return GoogleMapsFinderPlan(
                cities_csv=seed,
                query_templates=build_query_templates(keywords, geo_bias=True),
                geo_center=", ".join(labels),
                geo_radius_meters=500_000,
                provinces=province_filter or None,
            )

    cities_file = Path(str(criteria.get("cities_file") or default_cities))
    if not cities_file.is_absolute():
        cities_file = root / cities_file
    return GoogleMapsFinderPlan(
        cities_csv=cities_file,
        query_templates=build_query_templates(keywords, geo_bias=False),
        provinces=province_filter or None,
    )


def write_queries_json(path: Path, templates: List[str]) -> None:
    path.write_text(json.dumps(templates), encoding="utf-8")
