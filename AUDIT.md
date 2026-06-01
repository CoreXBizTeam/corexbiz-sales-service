# Audit — CoreX Sales Python

**Purpose:** Snapshot of **what is implemented**, **known gaps**, and **feedback** mapped to **[ROADMAP.md](ROADMAP.md)**. Reconcile this file whenever you ship or descope features.

**Related:** [README.md](README.md) · [DOCUMENTATION.md](DOCUMENTATION.md) · [USAGE.md](USAGE.md) · [ROADMAP.md](ROADMAP.md)

---

## Summary verdict

The pipeline is **fit for purpose as a v1**: cheap to operate, easy to read, good separation of discovery vs qualification. It is **not** a complete “all Canadian print shops” engine—coverage and precision are bounded by **Places + manual city seeds + shallow HTTP**.

---

## Status matrix (implementation vs roadmap)

| Area | Status | Roadmap | Notes |
|------|--------|---------|--------|
| Finder: Places text search + queries | **Done** | P0-2 | Fixed query templates |
| Finder: deduplication | **Done** | P0-3 | Heuristic; chains may duplicate |
| Finder: Place Details + `types` rule | **Done** | P0-4 | Details omit `types`; merge preserves text-search `types` |
| Finder: pagination handling | **Done** | P0-5 | Delay + retries; no full-run crash |
| Finder: output row cap for testing | **Done** | P0-6 | `MAX_OUTPUT_ROWS` |
| Qualifier: WP / Woo priority | **Done** | P0-7 | |
| Qualifier: Canada geo + sort columns | **Done** | P0-8 | |
| Qualifier: upload heuristics | **Done** | P0-9 | Shallow; SPAs / auth flows weak |
| Qualifier: scoring columns | **Done** | P0-10 | |
| Tests + requirements | **Done** | P0-11 | Smoke only; no live Places in CI |
| Skip seen `place_id` / run ledger | **Not done** | P1-1 | Cost savings on reruns |
| External config (YAML/env) | **Not done** | P1-2 | Constants in code today |
| Qualifier fast mode | **Not done** | P1-3 | |
| Structured logging | **Not done** | P1-4 | |
| HTTP backoff / 429 handling | **Partial** | P1-5 | Fixed delays; no smart backoff |
| Second discovery source | **Not done** | P2-1 | |
| Nearby Search variant | **Not done** | P2-2 | |
| Query tuning / A-B | **Not done** | P2-3 | |
| Deep enrichment pass | **Not done** | P3-1 | |
| CRM integrations | **Not done** | P3-2 | |

**Legend:** **Done** = matches current repo. **Partial** = some behavior exists, roadmap item not fully met. **Not done** = not implemented.

---

## Strengths (feedback)

- **Clear boundaries:** Finder does Places; Qualifier does HTTP. Easy to reason about and test.
- **Resilience:** Single bad query or pagination page does not kill the full Finder run; Qualifier tolerates unreachable sites per row.
- **Sales-ready CSV:** Deduped leads + enrichment columns align with spreadsheet review and **[lead_qualifier](lead_qualifier.py)** input contract.
- **Low complexity:** No database or async—appropriate for a small automation.

---

## Weaknesses & risks (feedback)

| Risk | Impact | Roadmap hook |
|------|--------|--------------|
| **Places-only discovery** | Misses or mis-ranks businesses weak on Maps | P2-* |
| **Manual city seed** | Gaps between cities unless you maintain `cities.csv` | P2-2, ops process |
| **API cost at scale** | Linear in cities × queries × details | P1-1, P1-2 |
| **Shallow qualifier** | Under-detects upload flows behind JS/login | P3-1 |
| **Dedupe heuristics** | Edge cases (same brand, two `place_id`s) | P1-1, manual review |

---

## Recommendations (what to do next)

1. **Short term:** Keep **[DOCUMENTATION.md](DOCUMENTATION.md)** and this audit in sync after any script change; run **`python -m unittest discover -s tests -v`** (see **[USAGE.md](USAGE.md)**).
2. **Medium term:** Implement **P1-1** (run ledger) if reruns are expensive; **P1-3** if qualifier runtime hurts.
3. **Long term:** Add **P2-1** only if Places recall is insufficient; avoid scope creep into full-site scraping in the core scripts.

---

## Audit changelog (manual)

When you edit behavior, add one line:

| Date | Change |
|------|--------|
| *YYYY-MM-DD* | *Example: Raised MAX_OUTPUT_ROWS default; update AUDIT + ROADMAP if needed* |

*(Maintainers: append rows here when shipping meaningful changes.)*
