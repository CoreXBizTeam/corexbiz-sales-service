# CoreX Sales — Python tools (documentation)

This folder holds a **two-stage pipeline** for CoreXUpload Prints outbound research: **find candidate print businesses**, then **qualify their websites** (WordPress / WooCommerce focus, Canada-friendly columns).

There is **no shared database** and **no merged mega-script**—each stage is a separate, simple Python file.

**Related documentation:** [README.md](README.md) (hub) · [USAGE.md](USAGE.md) · [ROADMAP.md](ROADMAP.md) · [AUDIT.md](AUDIT.md)

*When you change features, update this file plus [AUDIT.md](AUDIT.md) and [ROADMAP.md](ROADMAP.md); if CLI or deps change, update [USAGE.md](USAGE.md).*

---

## What each stage is supposed to do

### Stage 1 — `finder_places.py` (Finder)

**Purpose:** Build the **input list** of leads from Google Places.

- Read a seed CSV of **`province`** and **`city`** rows (e.g. `cities.csv`).
- For each city, run a fixed set of **text search queries** (print shop, commercial printer, fine art printing, giclee printing — scoped to Canada).
- Collect place results, **deduplicate** (by `place_id`, then website host, then name + address).
- Optionally call **Place Details** when **website** or **phone** is missing from text search (one details request per `place_id`, cached).
- Write a **CSV** that includes at least **`business_name`**, **`website`**, **`city`**, **`province`** so Stage 2 can consume it unchanged.

**Not in scope for the finder:** scraping websites, WordPress detection, or discovering cities automatically (no “discovery” logic beyond Places search).

### Stage 2 — `lead_qualifier.py` (Qualifier)

**Purpose:** **Enrich** each lead by visiting the **website** and scoring fit for outreach.

- Read CSV with **`business_name`** and **`website`** (plus optional **`city`** / **`province`** — preserved and normalized where applicable).
- Fetch the homepage (and a small set of secondary paths for contact/upload hints).
- Detect **WordPress** and **WooCommerce** with priority; other platforms are detected only in a **minimal** way when WordPress is not found.
- Detect **upload / artwork / file** signals (print-oriented keywords, file inputs, multipart forms, common JS upload libraries).
- Extract a visible **email** when possible.
- Output columns include legacy **`fit_segment`** / **`fit_score`**, V1 **`priority_segment`** / **`priority_score`**, **`wordpress_detected`**, **`woocommerce_detected`**, Canada-oriented **`province_normalized`**, and sort helpers for spreadsheets.

**Not in scope for the qualifier:** calling Google Places, building city lists, or heavy crawling.

---

## What is implemented so far (summary)

| Area | Implementation notes |
|------|----------------------|
| **Finder CSV in/out** | Seed: `province`, `city`. Output: fixed column set + extras (`place_id`, `search_query`, ratings, `google_maps_url`, etc.). |
| **Google Places** | `googlemaps` client; text search + delayed **`next_page_token`** pagination with retries on `INVALID_REQUEST`; soft failures so one bad query/page does not stop the run. |
| **Place Details** | Valid **Details** fields only — **`types` is not** requested on Details; **`types`** in the output CSV come from **text search** only. |
| **Finder caps** | **`MAX_OUTPUT_ROWS`** (e.g. 100 for testing; `0` = no row cap). **`MAX_TEXT_SEARCH_PAGES`** reflects Places’ usual **~3 pages per query**. |
| **Qualifier platform logic** | WordPress / Woo first; Shopify / Wix / Squarespace only if not WordPress. |
| **Qualifier Canada columns** | Strips geo fields; **`province_normalized`** (2-letter codes where possible); sort columns for province / city / WP / Woo / score. |
| **Qualifier upload heuristics** | Keywords, tech hints (FilePond, Uppy, etc.), links, `<input type="file">`, `multipart` forms. |
| **Dependencies** | Finder: stdlib + **`googlemaps`**. Qualifier: **`requests`**, **`beautifulsoup4`**. |

---

## Sample / supporting files

- **`cities.csv`** — Example seed rows (BC/AB/ON cities) for the finder.
- **`lead_qualifier.py`** — Stage 2 only; do not replace with the finder.
- **`finder_places.py`** — Stage 1 only; do not merge into the qualifier.

---

## Intended end-to-end flow

1. Maintain or generate a **city/province seed** CSV.
2. Run **`finder_places.py`** → raw leads CSV.
3. Run **`lead_qualifier.py`** on that CSV → enriched CSV for review / outreach.

Planned and future work are tracked in **[ROADMAP.md](ROADMAP.md)**; implementation status vs gaps is in **[AUDIT.md](AUDIT.md)**.
