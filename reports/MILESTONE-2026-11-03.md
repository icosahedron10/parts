# Implementation milestone — 2026-11-03 (America/Chicago)

The POC library and CLI are implemented and committed locally. CASE CE acceptance is **not complete**: the recorded full exam scored 356/400 reference recovery and 330/400 cited assembly support, below the required 370/335. At the user's request, timeouts, catalog payload capacity and model output allowance were subsequently increased. Latest smoke v7 passes at 19/20 with no tool errors or unsupported handoff URLs; a new full exam has not been run. The updated OpenAI credential works; no model substitution was used. The user requested a local repository, and no remote exists.

| Check | Observed result |
|---|---|
| Windows offline suite | 244 passed |
| Linux offline suite (Ubuntu / Python 3.12) | 244 passed |
| Ruff and frozen benchmark integrity | Passed |
| Original parser fixtures | All 11 hashes preserved |
| Discovery compatibility | 180 candidate sets and 147 alias-order comparisons preserved |
| Installed wheel outside checkout | Loaded CASE CE 1,339 and Case IH 23 records |
| CX350B radiator source smoke | Latest relaxed-timeout check: 2 cold requests, 0 warm; earlier failed recheck retained |
| CASE CE 20-case smoke | Latest v7: 19/20, all 3 ambiguity/no-match checks pass, zero tool errors and unsupported handoff URLs |
| Large catalog response check | Accepted 5,798,273 decoded bytes (5.53 MiB), parsed all 204 cable matches with complete coverage for that search scope |
| Full CASE CE exam (earlier configuration) | 356/400 reference recovery, 330/400 cited assembly support, 7 execution failures, zero unsupported handoff URLs |
| Latest Case IH 25-case evaluation, v3 | 20/25 reference recovery and cited assembly support; zero unsupported handoff URLs |
| Latest Case IH explicit evidence review, v3 | 20/25 passed; five source-timeout misses retained; implementation-agent review, not independent human approval |

## Implemented scope

The package exposes `open_catalog`, `create_parts_model`, and `make_lookup_parts_tool`. Each lookup returns exactly the validated `key_facts`, `sources`, and `store_url` fields. Fresh specialist state, exact catalog selection, lookup-owned handles, an eight-call budget and a configurable 180-second outer deadline apply to every invocation. Recognized source failures remain explicit; fabricated URLs, malformed completions, unexpected exceptions and hard timeouts propagate.

The shared catalog implementation provides the four live operations, complete-result SQLite caching, local pagination, per-key fill coalescing, one paced HTTP client, shared cooldowns, redirect/origin validation and bounded response sizes. Machine discovery uses the preserved dated index, hard model/variant/year constraints and deterministic RapidFuzz ordering. The brand-aware census is resumable and preserves dates for records not reobserved. Case IH uses the same parsers and transport, with 23 observed catalogs across tractors, combines and round balers. Both indexes are partial.

The Deep Agents harness exposes and permits only `lookup_parts`. It supplies original questions without answers or lineage, starts fresh conversation state per case and shares the catalog cache. Private observations, request counts, cache events, model usage and fingerprints are retained for the separate scorer. Reports refuse overwrites and retain errors/interrupted cases in their denominator. The 400-query exam and original eleven parser fixtures remain unchanged. Live evaluations are explicit local commands; CI contains only offline Windows/Linux checks. No hosted CI run is claimed for this local repository.

## Measured fixes and remaining limits

The full exam exposed valid prose-only citations being rejected during parent serialization. All 398 saved public handoffs now round-trip exactly in an offline replay. The parent sees only the public handoff, serialized with exact URL handles and citation-role guidance; no answer-key repair is performed.

Captured regressions added shared-parser support for storefront-relative instruction-product URLs and observed `/sa/` assembly suffixes returned inside a US catalog search. The parser keeps the exact selected catalog and part identity. Component lookups reject pictorial navigation indexes before requesting parts rows. Assembly inspection remains required, but a title-overlap heuristic can no longer block another already observed assembly whose rows match the requested component. Completed model-response usage is recorded immediately, so later graph failures do not discard it; earlier reports may undercount usage on failed graphs.

The first sixteen-case regression run recovered 15 references but only six cited contexts and exposed the title-heuristic restriction. The second recovered 13 references and nine cited contexts: electrical assembly inspection became possible, but transient read timeouts and a newly observed regional suffix still caused misses. These subset results are diagnostic and are never combined with the first exam to manufacture a passing score. The frozen scorer and evidence helper have no semantic changes; their raw hashes changed when Python source line endings were normalized to LF.

Case IH's earlier v2 run recovered 25/25 references and cited contexts, with 24/25 passing explicit agent review. Its retained failure transplanted a related assembly's callout into the requested double-cam application. Latest v3 inspects the correct double-cam assembly and preserves callout/quantity distinctions. Its five misses are unanswered fan/cooling requests affected by source timeouts. A mechanical flag for note-only replacement reference `A184441` remains; the inspected radiator note supports its replacement direction, and neither the key nor scorer was changed to erase the flag.

Historical sanitized fixtures retain their original bytes, including an encoding artifact in some descriptions. Future fixture capture writes ASCII character references so removing charset metadata cannot corrupt Unicode row text. Live descriptions were reviewed against their actual observations. No independent human fitment review, inventory check or serial-specific accuracy is claimed.

## Reports and next acceptance check

The user explicitly superseded the original tight timers on 2026-11-03. Defaults are now 10 seconds to connect, 30 seconds to read, 60 seconds per catalog operation, 180 seconds per complete lookup and 60 seconds per model request. The parent evaluation deadline is derived from the lookup plus two model requests and five seconds of overhead (305 seconds by default). Settings and environment overrides are documented in `.env.example`; the private credential file was not changed. Pacing, the eight-call budget and request/redirect limits remain enforced. Offline tests cover the longer permitted cooldown, shorter per-call deadlines, configuration reaching HTTP/model clients, cancellation and parent deadline coordination.

Smoke v6 used 29 source requests and recorded no timeouts or unsupported handoff URLs. Its three misses were `Q034` (radiator retrieval route), `Q053` (broad cable search exceeded the former decoded 4 MiB limit), and `Q062` (a cited alternative harness bracket did not recover the frozen reference).

The user then requested more room for large catalog responses and model output. The defaults are now 32 MiB of decoded catalog content and 8,192 output tokens per model call, configurable through `PARTS_SOURCE_MAX_RESPONSE_BYTES` and `PARTS_MODEL_MAX_OUTPUT_TOKENS`. The client still parses and caches complete operation results before local pagination. The decoded-byte cap is checked while streaming, and exceeding it produces an explicit error. Tests verify compressed and uncompressed content, exact cap boundaries, response cleanup, complete-result caching/pagination and the actual provider-native output-token parameter.

The direct cable probe recovered all 204 matches from a 5.53 MiB response that exceeded the old cap. Smoke v7 subsequently passed `Q053`, following a cable search and an assembly inspection. Its only miss was `Q062`; it used five source requests with a shared warm cache, recorded no tool errors, and returned zero unsupported handoff URLs. No answer key or scorer rule was changed. The 19/20 smoke result does not establish a new full-exam score.

- [Full exam score](exam-score-2026-11-03.json)
- [Latest CASE CE smoke](smoke-score-2026-11-03-v7.json)
- [Large cable-response probe](large-catalog-response-2026-11-03.json)
- [Latest relaxed-timeout catalog check](catalog-smoke-2026-11-03-relaxed-timeouts.json)
- [Failed bounded catalog recheck](catalog-smoke-2026-11-03-recheck.json)
- [Latest Case IH score](caseih-score-2026-11-03-v3.json)
- [Case IH case-by-case evidence review](caseih-review-2026-11-03-v3.json)
- [Second diagnostic regression score](exam-regressions-score-2026-11-03-v2.json)

The cold/warm catalog and 18/20 smoke checks now pass. CASE CE's 370/335 full-exam thresholds still need to be demonstrated on the final implementation. The first exam's misses and all later unsuccessful runs remain preserved. Source drift and semantic-review findings must remain distinct from reference recovery; a diagnostic subset or mixed-run result cannot replace a complete fresh exam.
