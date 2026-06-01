# How to run and use the CoreX Sales Python tools

Quick reference for **`finder_places.py`** (stage 1) and **`lead_qualifier.py`** (stage 2).

**Related documentation:** [README.md](README.md) (hub) · [DOCUMENTATION.md](DOCUMENTATION.md) · [ROADMAP.md](ROADMAP.md) · [AUDIT.md](AUDIT.md)

*If commands, env vars, or dependencies change here, align [DOCUMENTATION.md](DOCUMENTATION.md) and the status tables in [AUDIT.md](AUDIT.md).*

---

## Prerequisites

- **Python 3.9+** (or similar; avoid versions without recent stdlib features used in the repo).
- A virtual environment is recommended.

### Running tests (smoke / no Google API calls)

From the project directory, use the same environment where dependencies are installed:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

You should see **13 tests OK**. They cover imports, helper logic, CLI usage, missing API key handling, and a fast `lead_qualifier` run (row with no website). They do **not** call Google Places or crawl real lead sites.

End-to-end checks (Places + live HTTP) need a valid **`GOOGLE_MAPS_API_KEY`** and network access; run those manually when needed.

### Install dependencies

**Finder only:**

```bash
pip install googlemaps
```

**Qualifier only:**

```bash
pip install requests beautifulsoup4
```

**Both stages in one environment:**

```bash
pip install googlemaps requests beautifulsoup4
```

---

## Stage 1 — Finder (`finder_places.py`)

### API key

Create a Google Cloud API key with **Places API** enabled (billing as required by Google).

Export the key before running:

```bash
export GOOGLE_MAPS_API_KEY="your_key_here"
```

**macOS/Linux:** `export` as above.  
**Windows PowerShell:** `$env:GOOGLE_MAPS_API_KEY = "your_key_here"`

If the variable is missing, the script exits with a clear error.

### Seed CSV format

File: e.g. **`cities.csv`**

| Column   | Meaning |
|----------|----------------|
| `province` | Province code or name (passed through to output) |
| `city`     | City name (required on each row you want searched) |

See the included **`cities.csv`** for examples.

### Run the finder

```bash
python finder_places.py cities.csv leads_raw.csv
```

- **Argument 1:** path to the seed CSV (cities).
- **Argument 2:** path to the **output** CSV (this becomes the **input** to the qualifier).

### Optional behavior (edit constants in `finder_places.py`)

- **`MAX_OUTPUT_ROWS`** — Stop after this many **unique** rows (after dedupe). Set to **`0`** to process all cities/queries with no row cap.
- **`MAX_TEXT_SEARCH_PAGES`** — Max Places Text Search pages per query (Google typically allows about three).
- Pagination waits and retry delays are configured near the top of the file.

### Progress output

You should see lines such as:

- `[1/N] Searching City, Province`
- `Found X results`
- `Current unique rows: Y`
- Pagination messages when extra pages are fetched or retried.
- `Wrote Z rows to leads_raw.csv`

---

## Stage 2 — Qualifier (`lead_qualifier.py`)

### Input CSV

Minimum useful columns:

- **`business_name`**
- **`website`** (domain or URL; the script normalizes to `https://` if needed)

The finder output already includes **`city`** and **`province`**; the qualifier **preserves** them and adds **`province_normalized`**, scores, and upload/platform fields.

### Run the qualifier

```bash
python lead_qualifier.py leads_raw.csv leads_enriched.csv
```

- **Argument 1:** input CSV (e.g. finder output).
- **Argument 2:** enriched output CSV.

The script prints progress per row and writes the enriched file when finished.

### Runtime behavior

- Adds a short delay between HTTP requests to be polite to target sites.
- May request secondary paths (contact, upload, etc.) per site—see **`CONTACT_PATHS`** in `lead_qualifier.py`.

---

## Full pipeline (copy-paste)

From the project directory, with **`GOOGLE_MAPS_API_KEY`** set:

```bash
python finder_places.py cities.csv leads_raw.csv
python lead_qualifier.py leads_raw.csv leads_enriched.csv
```

Open **`leads_enriched.csv`** in a spreadsheet for sorting (province, city, WordPress flags, priority score, etc.).

---

## Troubleshooting

| Issue | What to check |
|--------|----------------|
| Finder: `Missing GOOGLE_MAPS_API_KEY` | Export the variable in the same shell you use to run Python. |
| Finder: empty or few rows | Queries, API quotas, or `MAX_OUTPUT_ROWS` capping results. |
| Qualifier: many sites “not reachable” | Network, blocking, or non-HTML responses; check `reachable` / `notes` columns. |
| Google: `INVALID_REQUEST` on pagination | Usually timing; the finder waits and retries; remaining pages for that query may be skipped. **If you previously saw 0 results per city but the API key works:** the `googlemaps` client used to *raise* on `INVALID_REQUEST`, which discarded page1 — use the latest `finder_places.py` with `_places_search` / `_place_details` wrappers. |

For design intent and scope, see **[DOCUMENTATION.md](DOCUMENTATION.md)**. For priorities and review status, see **[ROADMAP.md](ROADMAP.md)** and **[AUDIT.md](AUDIT.md)**.
