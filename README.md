# CNH parts agent

A small Python 3.12 library for bounded, cited parts research in US English CNH catalogs. Machine discovery is local; part evidence comes from the storefront or an unexpired SQLite cache. Each lookup returns exactly `key_facts`, `sources`, and `store_url` as validated JSON.

The repository is local by the owner's decision on 2026-11-03. See the [milestone report](reports/MILESTONE-2026-11-03.md) for measured acceptance status. CASE CE's full-exam thresholds remain unmet; with the increased timeout, payload and output allowances, the latest smoke run passes at 19/20 with no tool errors. CASE CE ships 1,339 historical catalogs; Case IH ships 23 observed catalogs across three equipment families. Both indexes are partial.

```powershell
uv sync --locked
# Copy .env.example to .env only if you do not already have a private .env.
uv run cnh-parts --machine "my 2010 CASE CX350B" --part radiator --brand casece
```

Set `OPENAI_API_KEY` locally. `PARTS_MODEL` defaults to the guide's `gpt-5.6-luna` profile. The model must support provider-native strict structured output; authentication errors and unavailable models fail explicitly. The CLI loads `.env`; the library reads the process environment. Credentials, caches, and temporary files are excluded from Git.

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

Every lookup gets a fresh specialist, eight-call budget, reference and URL handles, and middleware state. Discovery, cached calls, and rejected calls count. The complete lookup, including inference, has a 180-second deadline by default. Assembly evidence is required before presenting supported assembly findings. URL validation covers prose as well as structured fields; a nonempty `store_url` must be an observed product URL. Recognized catalog failures are explicit findings; malformed completions, fabricated links, unexpected exceptions, and hard timeouts propagate.

The transport permits one request at a time, at least one second between starts plus jitter, HTTPS on the allowed hostname through every redirect, and a decoded 32 MiB response limit. Default timeouts are 10 seconds to connect, 30 seconds to read, and 60 seconds for a complete operation, with at most three attempts. Shared cooldowns that cannot fit the remaining deadline fail promptly. Groups and assemblies cache for seven days, searches and relationships for one day, and empty results for one hour. Cache identity includes storefront, operation arguments, and parser version; pagination is applied after complete-result caching.

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
uv run python -m evals.run --live --cases evals/smoke.jsonl --out reports/smoke-YYYY-MM-DD.jsonl
uv run python -m evals.score_smoke --report reports/smoke-YYYY-MM-DD.jsonl --out reports/smoke-score-YYYY-MM-DD.json
uv run python -m evals.run --live --out reports/exam-YYYY-MM-DD.jsonl
uv run python -m evals.benchmark.score --report reports/exam-YYYY-MM-DD.jsonl --answers evals/benchmark/answers.jsonl --output reports/exam-score-YYYY-MM-DD.json
```

Use each command's `--help` for exact options. Reports refuse overwrites. The in-process Deep Agents harness exposes and permits only `lookup_parts`, uses fresh conversation state for each query, and shares the cache across cases. The runner reads only IDs and questions; answer keys and lineage are used by the separate scorer. Private diagnostics retain raw observations, model usage, requests, cache events, configuration, and fingerprints. Failed and interrupted cases remain in the denominator. Treat raw evaluation records as private if questions contain private information.

The frozen 400-query exam measures recovery of previously exposed references, not independent semantic accuracy or serial-specific fitment. Reference recovery, cited assembly support, source drift, and manual semantic review are distinct. Frozen queries, answers, lineage, and the original eleven parser fixtures retain their manifests and hashes; do not rewrite them to improve a score.

Refresh a catalog index explicitly using the shared transport:

```powershell
uv run python -m scripts.census --brand caseih --output data/indexes/caseih.json --checkpoint .cache/caseih-census.json --max-requests 40 --series magnum
# Use the same arguments plus --resume to continue a checkpoint.
```

The census validates records before atomic replacement, retains prior records not revisited, and renews observation timestamps only for pages fetched again. `--series` restricts scope; partial coverage is retained. `--fixture-dir` captures sanitized sitemap and series fixtures. `scripts.capture_fixture` captures explicit URLs; `scripts.capture_operation` also validates the observation through the shared operation parser. Both retain provenance and hashes.

The parent harness serializes the public handoff's cited URLs as per-run handles before passing them to its model and restores them exactly afterward. This prevents long source identifiers from being retyped incorrectly. Unknown handles and fabricated literal URLs fail; no answer repair or gold-based correction is performed. The library's public `lookup_parts` result always contains exact observed URLs.

If a process is forcibly terminated, first confirm it is no longer writing its report, then run `uv run python -m evals.recover reports/interrupted-run.jsonl`. This appends explicit `not_run` records for the missing schedule suffix, preserving existing records and the full denominator. Ordinary cancellation is recorded by the runner itself.
