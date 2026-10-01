# Documentation

Everything that is not code. Start here.

## Reference

| Document | What it covers |
|---|---|
| [`architecture.md`](architecture.md) | **Start here** — the repo mapped: processes, pipeline stages, every module's job, and the traps |
| [`data-sources.md`](data-sources.md) | What business data we can gather, where it comes from, and where the gaps are |
| [`roadmap.md`](roadmap.md) | Agency-grade roadmap — what the product is aiming at |
| [`context.md`](context.md) | Session context / handoff notes |

## Audits

Written against real runs, with every finding reproduced before it was fixed.

| Document | What it covers |
|---|---|
| [`audits/latency-and-llm.md`](audits/latency-and-llm.md) | Where pipeline time actually goes, and how the LLM stage works |
| [`audits/lifecycle.md`](audits/lifecycle.md) | Crash, timeout, delete and dedup failure modes — what breaks a job |
| [`audits/status-report.md`](audits/status-report.md) | Earlier status audit and procedure report |

## Vertical research

[`verticals/`](verticals/) — per-industry notes that feed
`backend/services/vertical_profiles.py`, which frames the pitch prompt per niche.

- [`construction_us.md`](verticals/construction_us.md)
- [`construction_subtrades_us.md`](verticals/construction_subtrades_us.md)
- [`hospitality_us.md`](verticals/hospitality_us.md)
- [`restaurants_us.md`](verticals/restaurants_us.md)

## Design notes (Obsidian vaults)

These folders contain `.canvas` files with internal node links, so **do not
rename them or move files inside them** — the canvases reference paths directly.

- [`tor-proxy/`](tor-proxy/) — proxy rotation, circuit isolation, health
- [`scrapers/`](scrapers/) — per-source scraper design
- [`Lead-Generation/`](Lead-Generation/) — broader product vault

## Conventions

- Code references docs by path (e.g. `docs/verticals/*.md` in
  `business_intel.py`). If you move a document, grep for its old name first.
- Audits are dated and describe the state at the time of writing; they are a
  record, not a spec. The roadmap is the forward-looking document.
