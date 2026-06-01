# Roadmap — CoreX Sales Python

Priorities for the Finder + Qualifier pipeline. Status should stay aligned with **[AUDIT.md](AUDIT.md)** (audit drives “done / partial / not started”).

**Related:** [README.md](README.md) · [DOCUMENTATION.md](DOCUMENTATION.md) · [USAGE.md](USAGE.md) · [AUDIT.md](AUDIT.md)

---

## Phase 0 — Baseline (shipped)

| ID | Item | Notes |
|----|------|--------|
| P0-1 | Two-script pipeline (Finder / Qualifier) | No merged monolith |
| P0-2 | Finder: seed CSV `province`,`city` + Places text search | Canada bias (`region=ca`) |
| P0-3 | Finder: dedupe (`place_id` → website → name+address) | |
| P0-4 | Finder: Place Details when website/phone missing | `types` from text search only |
| P0-5 | Finder: pagination delay + retry on `INVALID_REQUEST` | Soft fail per page |
| P0-6 | Finder: `MAX_OUTPUT_ROWS` test cap | `0` = unlimited |
| P0-7 | Qualifier: WordPress / Woo-first platform logic | |
| P0-8 | Qualifier: Canada `province_normalized` + sort helpers | |
| P0-9 | Qualifier: print-oriented upload detection | Keywords, forms, libraries |
| P0-10 | Qualifier: priority + legacy fit columns | CSV compatible |
| P0-11 | Smoke tests + `requirements.txt` | See USAGE.md |

---

## Phase 1 — Quality & operations (next)

| ID | Item | Notes |
|----|------|--------|
| P1-1 | Run ledger / skip already-seen `place_id` | Optional CSV or sidecar; reduce API cost |
| P1-2 | Config file or env for queries, caps, timeouts | Fewer edits inside `.py` |
| P1-3 | Qualifier: optional “fast mode” (no secondary pages) | Large lists |
| P1-4 | Structured logging (JSON lines) | Optional; keep stdout human-readable |
| P1-5 | Rate-limit / backoff for Qualifier HTTP | Respect429 / retries |

---

## Phase 2 — Discovery breadth

| ID | Item | Notes |
|----|------|--------|
| P2-1 | Second discovery source (export same CSV shape) | Directories, manual lists—still no scraper scope creep in core |
| P2-2 | Geocode city → Nearby Search variant | Compare precision vs text-only |
| P2-3 | Query A/B or per-region templates | Tune recall/precision |

---

## Phase 3 — Enrichment depth (shortlist only)

| ID | Item | Notes |
|----|------|--------|
| P3-1 | “Deep pass” for top N leads | More URLs or single-purpose checks |
| P3-2 | CRM / sheet export conventions | Column maps only unless you add integrations |

---

## How to update this file

1. Add or move rows between phases when priorities change.  
2. Mark reality in **[AUDIT.md](AUDIT.md)** so “shipped” matches the codebase.  
3. If commands or env vars change, update **[USAGE.md](USAGE.md)** and **[DOCUMENTATION.md](DOCUMENTATION.md)**.
