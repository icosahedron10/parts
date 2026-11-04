# Implementation milestone — 2026-11-03 (America/Chicago)

The library, CLI, offline discovery, SQLite cache, bounded shared transport, specialist, restricted Deep Agents harness, frozen scorer, resumable census, packaged indexes, and Case IH fixtures are implemented. The default model remains the requested `gpt-5.6-luna`; the updated local credential authenticated successfully. Credentials were preserved locally and excluded from Git. The user requested a local repository after GitHub rejected creation under the guide's owner; no remote was created.

| Check | Observed result |
|---|---|
| Windows offline suite | 226 passed |
| Linux offline suite (Ubuntu / Python 3.12) | 226 passed |
| Ruff | Passed |
| Frozen benchmark | 400 queries, original hashes verified |
| Original parser fixtures | All 11 manifest hashes verified |
| Discovery compatibility | 180 candidate sets and 147 alias-order comparisons preserved |
| Installed wheel outside checkout | Loaded CASE CE 1,339 and Case IH 23 records |
| Cold/warm CX350B radiator source smoke | 2 cold requests, 0 warm requests |
| CASE CE 20-case smoke, v4 | 18/20; all ambiguity/no-match checks passed |
| Full CASE CE exam | Running; thresholds remain unproven |
| Case IH 25-case evaluation, v2 | 25/25 reference recovery and cited assembly support; zero unsupported handoff URLs |
| Case IH explicit evidence review, v2 | 24/25 passed; implementation-agent review, not an independent human review |

The original failed smoke and later iterations remain in their own durable reports. The first run exposed valid Responses API content blocks being rejected by the parent harness; this was fixed and regression-tested. Later failures exposed variant/assembly selection and parent URL transcription problems. Specialist guidance now emphasizes inspected service assemblies and excludes unverified neighboring part numbers. The harness serializes cited URLs as exact handles and validates their restoration; it never repairs a guessed URL or changes an answer using the key.

The accepted v4 smoke retains two retrieval misses (`Q034.year_first`, `Q062.year_first`) for review. Passing the smoke does not establish the full-exam thresholds or semantic accuracy. Case IH uses shared parsing with observed `%26` sitemap paths and nonorderable `NLS`/`REF` rows. Its 25 authored questions cover tractors, combines, and round balers, with answers and lineage outside inference.

Case IH v2 has one retained semantic failure (`IH024`): the part and quantity are correct, but callout 57 belongs to a related assembly while the requested double-cam assembly uses callout 12. The separate evidence review records each case and its report fingerprint. A mechanical flag for `A184441` is retained: the inspected radiator row explicitly says it is replaced, but the frozen helper recognizes assembly part rows rather than note-only replacement references. No answer or scorer rule was rewritten to remove that flag. The first Case IH run and the two engine-qualified regression checks are preserved separately.

Explicit engine designations now remain qualifications in the specialist task instead of becoming a second machine-model constraint. The parent receives citation-role guidance derived only from public handoff URLs. Offline runner tests use isolated temporary caches; a repeated Linux check initially collided with a live Windows cache before that test isolation was fixed. Both platforms subsequently passed all 226 tests.

Local checks ran on Windows and Linux. The same offline checks are configured in GitHub Actions; no hosted CI run is claimed for this local repository. Live catalog calls and paid model runs are explicitly invoked local commands, never CI jobs.
