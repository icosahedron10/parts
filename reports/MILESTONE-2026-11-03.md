# Implementation milestone — 2026-11-03 (America/Chicago)

The POC library and CLI are implemented and committed locally. CASE CE acceptance is **not complete**: the full exam scored 356/400 reference recovery and 330/400 cited assembly support, below the required 370/335. Subsequent fixes have offline regressions, but source read timeouts prevented a successful final smoke check, so another full exam was not launched. The updated OpenAI credential works; no model substitution was used. The user requested a local repository, and no remote exists.

| Check | Observed result |
|---|---|
| Windows offline suite | 235 passed |
| Linux offline suite (Ubuntu / Python 3.12) | 235 passed |
| Ruff and frozen benchmark integrity | Passed |
| Original parser fixtures | All 11 hashes preserved |
| Discovery compatibility | 180 candidate sets and 147 alias-order comparisons preserved |
| Installed wheel outside checkout | Loaded CASE CE 1,339 and Case IH 23 records |
| CX350B radiator source smoke | Initial run: 2 cold requests, 0 warm; final bounded recheck failed with ReadTimeout after 2 requests |
| CASE CE 20-case smoke | Earlier v4: 18/20; latest v5: 5/20, with all 3 ambiguity/no-match checks passing and source timeouts affecting 15 retrieval cases |
| Full CASE CE exam | 356/400 reference recovery, 330/400 cited assembly support, 7 execution failures, zero unsupported handoff URLs |
| Latest Case IH 25-case evaluation, v3 | 20/25 reference recovery and cited assembly support; zero unsupported handoff URLs |
| Latest Case IH explicit evidence review, v3 | 20/25 passed; five source-timeout misses retained; implementation-agent review, not independent human approval |

## Implemented scope

The package exposes `open_catalog`, `create_parts_model`, and `make_lookup_parts_tool`. Each lookup returns exactly the validated `key_facts`, `sources`, and `store_url` fields. Fresh specialist state, exact catalog selection, lookup-owned handles, an eight-call budget and a 30-second outer deadline apply to every invocation. Recognized source failures remain explicit; fabricated URLs, malformed completions, unexpected exceptions and hard timeouts propagate.

The shared catalog implementation provides the four live operations, complete-result SQLite caching, local pagination, per-key fill coalescing, one paced HTTP client, shared cooldowns, redirect/origin validation and bounded response sizes. Machine discovery uses the preserved dated index, hard model/variant/year constraints and deterministic RapidFuzz ordering. The brand-aware census is resumable and preserves dates for records not reobserved. Case IH uses the same parsers and transport, with 23 observed catalogs across tractors, combines and round balers. Both indexes are partial.

The Deep Agents harness exposes and permits only `lookup_parts`. It supplies original questions without answers or lineage, starts fresh conversation state per case and shares the catalog cache. Private observations, request counts, cache events, model usage and fingerprints are retained for the separate scorer. Reports refuse overwrites and retain errors/interrupted cases in their denominator. The 400-query exam and original eleven parser fixtures remain unchanged. Live evaluations are explicit local commands; CI contains only offline Windows/Linux checks. No hosted CI run is claimed for this local repository.

## Measured fixes and remaining limits

The full exam exposed valid prose-only citations being rejected during parent serialization. All 398 saved public handoffs now round-trip exactly in an offline replay. The parent sees only the public handoff, serialized with exact URL handles and citation-role guidance; no answer-key repair is performed.

Captured regressions added shared-parser support for storefront-relative instruction-product URLs and observed `/sa/` assembly suffixes returned inside a US catalog search. The parser keeps the exact selected catalog and part identity. Component lookups reject pictorial navigation indexes before requesting parts rows. Assembly inspection remains required, but a title-overlap heuristic can no longer block another already observed assembly whose rows match the requested component. Completed model-response usage is recorded immediately, so later graph failures do not discard it; earlier reports may undercount usage on failed graphs.

The first sixteen-case regression run recovered 15 references but only six cited contexts and exposed the title-heuristic restriction. The second recovered 13 references and nine cited contexts: electrical assembly inspection became possible, but transient read timeouts and a newly observed regional suffix still caused misses. These subset results are diagnostic and are never combined with the first exam to manufacture a passing score. The frozen scorer and evidence helper have no semantic changes; their raw hashes changed when Python source line endings were normalized to LF.

Case IH's earlier v2 run recovered 25/25 references and cited contexts, with 24/25 passing explicit agent review. Its retained failure transplanted a related assembly's callout into the requested double-cam application. Latest v3 inspects the correct double-cam assembly and preserves callout/quantity distinctions. Its five misses are unanswered fan/cooling requests affected by source timeouts. A mechanical flag for note-only replacement reference `A184441` remains; the inspected radiator note supports its replacement direction, and neither the key nor scorer was changed to erase the flag.

Historical sanitized fixtures retain their original bytes, including an encoding artifact in some descriptions. Future fixture capture writes ASCII character references so removing charset metadata cannot corrupt Unicode row text. Live descriptions were reviewed against their actual observations. No independent human fitment review, inventory check or serial-specific accuracy is claimed.

## Reports and next acceptance check

- [Full exam score](exam-score-2026-11-03.json)
- [Latest CASE CE smoke](smoke-score-2026-11-03-v5.json)
- [Failed bounded catalog recheck](catalog-smoke-2026-11-03-recheck.json)
- [Latest Case IH score](caseih-score-2026-11-03-v3.json)
- [Case IH case-by-case evidence review](caseih-review-2026-11-03-v3.json)
- [Second diagnostic regression score](exam-regressions-score-2026-11-03-v2.json)

When catalog requests again complete within the specified limits, rerun the cold/warm catalog check, the 20-case smoke and a new complete 400-query exam using the README commands and new report names. The first exam's misses and all later unsuccessful runs remain preserved. CASE CE's 18/20 smoke and 370/335 full-exam thresholds still need to be demonstrated on the final implementation. Source drift and semantic-review findings must remain distinct from reference recovery.
