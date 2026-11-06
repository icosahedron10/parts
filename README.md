# CNH parts agent

A small Python 3.12 library for bounded, cited parts research in US English CNH catalogs. Machine discovery is local; part evidence comes from the storefront or an unexpired SQLite cache. Each lookup returns exactly `key_facts`, `sources`, and `store_url` as validated JSON.

The repository is hosted privately at [icosahedron10/parts](https://github.com/icosahedron10/parts). The [milestone report](reports/MILESTONE-2026-11-03.md) preserves the earlier baseline. Current navigation validation is tracked separately in [navigation-validation.json](reports/navigation-validation.json); the 20-case parent review reached 19/20, while the Sol 400-case comparison was stopped following the owner's objection to its cost; the earlier quota-interrupted report remains preserved. All six parent argument-fidelity probes passed. CASE CE ships 1,339 historical catalogs; Case IH ships 23 observed catalogs across three equipment families. Both bundled indexes remain frozen and partial. Expanded discovery and prepared directories are local opt-in data; [navigation-coverage.json](reports/navigation-coverage.json) records their coverage gaps and preparation traffic.

```powershell
uv sync --locked
# Copy .env.example to .env only if you do not already have a private .env.
uv run cnh-parts --machine "my 2010 CASE CX350B" --part radiator --brand casece
```

Set `OPENAI_API_KEY` locally. `PARTS_MODEL` defaults to `gpt-6.1-terra`, with `PARTS_REASONING_EFFORT=high` (`none`, `low`, and `medium` are also accepted). The model must support provider-native strict structured output; authentication errors and unavailable models fail explicitly. The CLI loads `.env`; the library reads the process environment. Credentials, caches, and temporary files are excluded from Git.

```python
import asyncio
from dotenv import load_dotenv
from cnh_parts_agent import open_catalog, create_parts_model, make_lookup_parts_tool


async def main():
    load_dotenv()
    async with open_catalog() as catalog:
        lookup_parts = make_lookup_parts_tool(catalog=catalog, model=create_parts_model())
        result = await lookup_parts.ainvoke(
            {
                "machine_query": "my 2010 CASE CX350B",
                "part_query": "radiator",
                "brand": "casece",
            }
        )
        print(result)


asyncio.run(main())
```

Give an orchestrator this tool and instruct it to preserve the user's machine wording, suffix, variant, and year. Pass `brand` only when known (`casece` or `caseih`). Present catalog-choice questions to the user and submit their exact candidate label as `selected_machine_label`; the orchestrator must not choose a catalog itself. Missing model information produces clarification. A missing year is allowed when the remaining evidence is sufficient.

`open_catalog(settings=None)` owns one process-wide HTTP client, request limiter, cooldown, and SQLite cache. Nested contexts with identical settings share those resources. `Settings` in `cnh_parts_agent.settings` accepts `cache_path`, `index_dir`, `model`, and `user_agent`; `PARTS_` environment variables configure the same fields. Default indexes are included in the wheel; `PARTS_INDEX_DIR` replaces their directory. Run one application process. The proof of concept has no HTTP service, UI, Redis, embeddings, or distributed coordination.

Every lookup gets a fresh specialist, eight-call budget, reference and URL handles, and middleware state. Host-side machine discovery and initial local-directory loading are free; specialist browsing, cached calls, and rejected calls count. The complete lookup, including inference, has a 180-second deadline by default. Assembly evidence is required before presenting supported assembly findings. URL validation covers prose as well as structured fields; a nonempty `store_url` must belong to a cited, inspected assembly occurrence. Recognized catalog failures are explicit findings; persistent malformed completions, fabricated links, unexpected exceptions, and hard timeouts propagate.

A specialist completion containing invalid JSON gets one retry of that model call, within the same deadline. Already executed tools and their budgets are preserved. Both failed-generation usage and raw output are retained in private diagnostics. Schema, citation and evidence failures are not retried or repaired.

The transport permits one request at a time, at least one second between starts plus jitter, HTTPS on the allowed hostname through every redirect, and a decoded 32 MiB response limit. Default timeouts are 10 seconds to connect, 30 seconds to read, and 60 seconds for a complete operation, with at most three attempts. Shared cooldowns that cannot fit the remaining deadline fail promptly. Successful catalog evidence caches for 30 days by default (`PARTS_EVIDENCE_TTL_SECONDS=2592000`); empty results cache for one hour. Existing expiry timestamps and observation dates are preserved. Navigation has no automatic expiry. Cache identity includes storefront, operation arguments, and parser version; pagination is applied after complete-result caching.

Timeouts are configurable through `Settings` or the matching environment variables in `.env.example`: `PARTS_SOURCE_CONNECT_TIMEOUT_SECONDS`, `PARTS_SOURCE_READ_TIMEOUT_SECONDS`, `PARTS_SOURCE_OPERATION_TIMEOUT_SECONDS`, `PARTS_TIMEOUT_SECONDS` (whole lookup), and `PARTS_MODEL_TIMEOUT_SECONDS` (60 seconds per model request). A shorter outer deadline still cancels work in progress. The evaluation parent allows the lookup deadline plus two model deadlines and five seconds of overhead: 305 seconds by default. These relaxed defaults supersede the original plan's tighter timers at the user's request; earlier evaluation reports retain their original configuration.

`PARTS_SOURCE_MAX_RESPONSE_BYTES` configures the decoded catalog payload limit (default 33,554,432 bytes / 32 MiB, up to 128 MiB). The client parses and caches the complete response before paging its catalog rows to the specialist; exceeding the limit returns an explicit error rather than a truncated result. Private request diagnostics record decoded byte counts. `PARTS_MODEL_MAX_OUTPUT_TOKENS` configures the model's output allowance (default 8,192 tokens per call, up to 32,768). This is an output ceiling, not an input-context limit or a requested answer length. The handoff remains concise and source-qualified.

The supplied CASE CE index contains 1,339 historical observations and has partial coverage. Dates remain attached to individual records; using the snapshot does not refresh them. Unknown production dates preserve uncertainty, while contradictory known years exclude a catalog. RapidFuzz only orders eligible candidates. Removing Qdrant and FastEmbed preserved all 180 supplied candidate sets and 147 alias-order comparisons. The possessive “my 2010” remains a year; an explicit `MY19` remains a catalog qualifier.

Offline development checks:

```powershell
uv run ruff check .
uv run pytest -q
uv run python -m evals.benchmark.verify
uv build
uv run python -m scripts.verify_wheel dist/cnh_parts_agent-0.1.0-py3-none-any.whl
```

GitHub Actions runs these checks on Windows and Linux. Tests block external socket connections. Live requests and paid model evaluations are local commands only:

```powershell
uv run python -m scripts.live_smoke --out reports/catalog-smoke-YYYY-MM-DD.json
uv run python -m evals.run --live --full --cases evals/smoke.jsonl --out reports/smoke-YYYY-MM-DD.jsonl
uv run python -m evals.score_smoke --report reports/smoke-YYYY-MM-DD.jsonl --out reports/smoke-score-YYYY-MM-DD.json
uv run python -m evals.run --live --sample 20 --seed 20261104 --out reports/exam-YYYY-MM-DD.jsonl
uv run python -m evals.benchmark.score --report reports/exam-YYYY-MM-DD.jsonl --answers evals/benchmark/answers.jsonl --output reports/exam-score-YYYY-MM-DD.json
```

Use each command's `--help` for exact options. Reports refuse overwrites. The in-process Deep Agents harness exposes and permits only `lookup_parts`, uses fresh conversation state for each query, and shares the cache across cases. The runner reads only IDs and questions; answer keys and lineage are used by the separate scorer. Private diagnostics retain raw observations, model usage, requests, cache events, configuration, and fingerprints. Failed and interrupted cases remain in the denominator. Authentication, unavailable-model and explicit exhausted-credit failures stop further scheduled inference; transient throttling is recorded separately. Treat raw evaluation records as private if questions contain private information.

Routine evaluation samples 20 cases without replacement, recording the seed and exact schedule, with randomized 30–90 second pauses between lookups. Use a fresh recorded seed for a new spot check or the same seed for a controlled comparison. Full runs require explicit `--full` and owner authorization. Samples do not establish the historical 400-case thresholds; manually review every sampled answer.

The frozen 400-query exam measures recovery of previously exposed references, not independent semantic accuracy or serial-specific fitment. Reference recovery, cited assembly support, source drift, and manual semantic review are distinct. Frozen queries, answers, lineage, and the original eleven parser fixtures retain their manifests and hashes; do not rewrite them to improve a score.

A separate [50-query general holdout](evals/holdout/README.md) tests `lookup_parts` directly,
without the orchestrator. It freezes new inputs and behavioral review criteria before inference
and reports execution, mechanical evidence checks, and case-by-case semantic review separately.

Prepare local assembly navigation with the shared paced transport:

```powershell
uv run python -m scripts.prepare_navigation --snapshots reports/toc-snapshots --max-requests 250
# Repeat without --snapshots to resume the same SQLite checkpoints.
# Expanded discovery is opt-in; frozen bundled indexes remain unchanged for comparison.
$env:PARTS_INDEX_DIR = ".cache/navigation-indexes"
```

Preparation uses randomized 1–1.25 second intervals between HTTP attempt starts, including retries and redirects. Configure these with `--interval-min` and `--interval-max`. Interactive lookup deadlines remain unchanged.

Preparation checkpoints successful raw responses in SQLite and publishes each complete
catalog directory atomically. Batches count retries and redirects. Repeat until the
report says `complete: true`, or investigate a reported source/parser failure.
Completion covers traversed sitemap routes, not every catalog CNH may hold. Reports
separate discovered, prepared and incomplete catalogs and HTTP attempts. Run preparation
and lookup commands serially so only one paced transport runs at once.

The six historical parsed group captures retain their source hashes. Their original
raw group bytes were not retained, so reserialized JSON cannot reverify those hashes.
Machine-page hashes, source routes, catalog/group ownership and contents references
are validated before import. Historical files remain unchanged.

The specialist receives compact titles and observed handles for up to 400 assemblies.
Larger directories expose groups with local `list_assemblies` pagination (up to 200 rows).
It inspects parts tables on demand and follows figure references. Missing or insufficient
navigation uses bounded assembly-title, part-description or part-number search, retaining
recovered routes as partial navigation. Failed assembly routes are marked stale; targeted
search can repair them without an interactive directory crawl. One cold parts request
and zero warm requests is the normal prepared single-assembly path, not a guarantee for
every identification. The public lookup arguments and three-field handoff are unchanged.

The earlier standalone census remains available:

```powershell
uv run python -m scripts.census --brand caseih --output data/indexes/caseih.json --checkpoint .cache/caseih-census.json --max-requests 40 --series magnum
# Use the same arguments plus --resume to continue a checkpoint.
```

The census validates records before atomic replacement, retains prior records not revisited, and renews observation timestamps only for pages fetched again. `--series` restricts scope; partial coverage is retained. `--fixture-dir` captures sanitized sitemap and series fixtures. `scripts.capture_fixture` captures explicit URLs; `scripts.capture_operation` also validates the observation through the shared operation parser. Both retain provenance and hashes.

The parent harness serializes the public handoff's cited URLs as per-run handles before passing them to its model and restores them exactly afterward. This prevents long source identifiers from being retyped incorrectly. Unknown handles and fabricated literal URLs fail; no answer repair or gold-based correction is performed. The library's public `lookup_parts` result always contains exact observed URLs.

If a process is forcibly terminated, first confirm it is no longer writing its report, then run `uv run python -m evals.recover reports/interrupted-run.jsonl`. This appends explicit `not_run` records for the missing schedule suffix, preserving existing records and the full denominator. Ordinary cancellation is recorded by the runner itself.

The authorized Terra spot check completed 20/20 requests: manual evidence review supports all 20 part identifications and split-argument qualifiers, with one malformed Markdown citation (an observed target). See [sample review](reports/navigation-terra-sample-20-review.json). Three original mechanical citation flags were scorer Markdown-context false positives; the original score is preserved alongside a corrected offline score. This sample does not demonstrate full-exam thresholds or expanded-brand accuracy. Catalog preparation remains incomplete and continues serially.

New Holland Agriculture (`newhollandag`) and Construction (`newhollandce`) are registered US storefronts. The CLI and lookup tool accept these brand hints; generic “New Holland” or “NH” wording searches installed New Holland indexes without mixing CASE results. Model identifiers retain decimal distinctions such as T7.270 versus T7.230. Until an index is collected and installed, New Holland lookups fail explicitly with `machine_index_missing`.

Preparation completes the existing CASE Construction and Case IH stages first, then discovers and prepares New Holland Agriculture, followed by New Holland Construction. All stages share one serial transport, a combined 250-attempt batch budget, 1–1.25-second request-start spacing, server backoff, and existing SQLite checkpoints. The running worker retains its 120–300-second pauses between batches. No additional model evaluations are part of collection. Use `PARTS_INDEX_DIR=.cache/navigation-indexes` to enable collected indexes; the historical bundled CASE indexes remain unchanged.

New Holland storefront links were observed in retained MyCNHStore sitemap HTML. Offline tests cover routing, source isolation, decimal model identity, and staged collection/resume. Live New Holland sitemap/group compatibility will be checked when its queued stage begins; unexpected source formats fail explicitly and retain checkpoints. Total New Holland coverage is unknown until discovery finishes, so the earlier CASE-only completion percentage does not represent the expanded scope.
