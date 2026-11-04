# cnh-parts-agent: POC Implementation Guide

**Repo:** `icosahedron10/cnh-parts-agent` (private)
**Package:** `cnh_parts_agent`
**Status:** plan, written 2026-11-03. Nothing is built yet.
**Source of the old work:** `C:\Users\madse\Documents\parts-subagent` (not under version control). Paths below that start with `old:` point there.

---

## 1. What we're building

A Python package that gives a LangChain **Deep Agents** orchestrator one capability: "find this part for this machine in the MyCNHStore parts catalog, with links to back it up."

```
Orchestrator (Deep Agent)
   │  lookup_parts(machine_query="CX350B", part_query="radiator")
   ▼
Parts specialist (LangChain agent, its own context)
   │  discovery runs first, then 5 narrow catalog tools, budget of 8 calls / 30 s
   ▼
Catalog client (no LLM)                 Machine discovery (no LLM, no network)
   │  polite HTTP + SQLite cache            local per-brand machine index
   ▼
www.mycnhstore.com/us/en/{brand}/...
   │
   ▼
PartsHandoff JSON back to the orchestrator: { key_facts, sources, store_url }
```

The orchestrator only ever sees the final three-field answer. All the noisy search results and assembly pages stay inside the specialist.

### POC definition of done

1. The orchestrator can call `lookup_parts` and get a correct, cited answer for CASE Construction (`casece`) machines.
2. On the 400-query exam it scores **at least 370/400 on reference recovery**. That's the old system's Sept 9 result.
3. A second MyCNHStore brand works through config, fixtures and an index, with no new scraper code.
4. Offline tests run green in GitHub Actions. Live scraping and model calls never run in CI.
5. The site never sees more than 1 request per second from us. We back off on any denial.

### Non-goals for the POC

- Multi-worker or multi-process deployment (no Redis, no shared cache coordination)
- An HTTP API, web UI or Streamlit app. The orchestrator *is* the host.
- Per-user quotas, auth or caller identity
- Inventory, pricing or ordering
- Serial-number or PIN-level fitment guarantees. We cite the catalog; we don't certify fitment.
- Running on Microsoft Foundry. We stay on OpenAI for now (see §8.4).

---

## 2. Lessons from the first attempt

The old tree works (535 tests pass), but it stalled because it was hardened like a production service before it had a product to serve. Rules for this rewrite:

1. **Assets over code.** The benchmark, fixtures, machine index and parsing knowledge are what's valuable. Port the logic, don't copy the scaffolding.
2. **Smallest thing that's measurably correct.** Every milestone ends with a test or an eval number, not a verification report.
3. **One way to run it.** A library, plus a CLI for smoke tests. No second or third host.
4. **Simplify contracts.** Drop cursor snapshots, schema versions, cache metadata and signed references. Keep what the model and the orchestrator actually use.
5. **Git from the first commit.** The old folder had no history.

---

## 3. What carries over

### Assets (copy as-is)

| Asset | Old location | New location | Notes |
|---|---|---|---|
| 400-query exam | `old:benchmark/{queries,answers,lineage}.jsonl`, `manifest.json` | `evals/benchmark/` | Byte-for-byte. `verify.py` checks the hashes. |
| Exam scorer | `old:benchmark/score.py`, `verify.py` | `evals/benchmark/` | Port, then adapt it to the new runner's record format. |
| CASE CE machine index | `old:data/machine-index.json` | `data/indexes/casece.json` | 1,339 records, observed Sept 7, 2026. |
| Parser fixtures | `old:tests/fixtures/m0/*` | `tests/fixtures/casece/` | 11 files: HTML and JSON pages for each operation. |
| Discovery test cases | `old:tests/fixtures/machine_model_alias_cases.json`, `machine_semantic_cases.json` | `tests/fixtures/discovery/` | These are the acceptance tests for the new matcher. |

### Code (port, rewrite or drop)

| Old module | Action | New module | Notes |
|---|---|---|---|
| `contracts/handoff.py` | **Port** | `agent/handoff.py` | `PartsHandoff`, URL handle resolution and catalog-choice rendering. Keep the three fields exactly. |
| `services/adapters/cnh_parse.py` | **Port** | `catalog/parse.py` | Replace the hardcoded `HOME_PATH = "/us/en/casece"` with a `Storefront` parameter. |
| `services/adapters/cnh.py` | **Port** | `catalog/operations.py` | URL building and dispatch for each operation. Take the brand from `Storefront`. |
| `services/subagent.py` | **Port** | `agent/specialist.py` | Middleware: `discover_machine`, `clarify_catalog`, `PartsToolBoundary`, `resolve_sources`. |
| `prompts.py` (`CATALOG_PROMPT`) | **Port** | `agent/prompts.py` | Make it brand-aware. Drop the conversational-parent prompt. |
| `services/tools.py`, `langchain_tools.py` | **Port** | `agent/tools.py` | Budget, tool rejection, and `url_N` and ref handles. |
| `services/models.py` | **Port** | `agent/model.py` | Keep the OpenAI profile (§8.4). |
| `services/demo_model.py` | **Port** | `tests/scripted_model.py` | A deterministic model for offline agent tests. |
| `contracts/catalog.py` | **Simplify** | `catalog/models.py` | Keep the row models. Drop `Cursor`, `Page` snapshots, `CacheMetadata` and `schema_version`. |
| `services/transport.py` (30 KB) | **Rewrite** | `catalog/transport.py` | About 150 lines. Keep the limits (§6.2). |
| `services/cache.py` (Redis + Lua) | **Rewrite** | `catalog/cache.py` | SQLite (§6.3). |
| `services/machine_index.py` | **Port** | `discovery/index.py` | Index loader and validation. |
| `services/machine_semantic.py` | **Rewrite** | `discovery/match.py` | Keep the model-code regexes and alias logic. Try it without Qdrant first (§7). |
| `tools/machine_census.py`, `tools/machine_index.py` | **Port** | `scripts/census.py` | Build the index from the sitemap, for each brand. |
| `benchmark/run.py` | **Rewrite** | `evals/run.py` | Run in-process through the eval orchestrator, not HTTP. |
| `managers/*`, `controllers/*`, `app.py`, `runtime.py`, `streamlit_app.py`, `ui_runtime.py`, `conversations.py`, `metrics.py`, `tracing.py` | **Drop** | n/a | Host and UI scaffolding. Use LangSmith for traces if needed. |
| `services/evidence.py`, `sources.py`, `references.py` | **Review, then fold in or drop** | n/a | Check what's still needed once the operations are ported. |

---

## 4. Repo layout

```
cnh-parts-agent/
  pyproject.toml            # uv-managed, Python 3.12
  uv.lock
  .env.example
  .github/workflows/ci.yml
  README.md
  docs/
    IMPLEMENTATION-GUIDE.md # this file
  src/cnh_parts_agent/
    __init__.py             # public API: open_catalog, make_lookup_parts_tool, create_parts_model
    settings.py             # pydantic-settings, env prefix PARTS_
    catalog/
      storefront.py         # Storefront registry (brand slug, paths, region)
      transport.py          # polite fetching
      cache.py              # SQLite cache
      parse.py              # HTML/JSON parsers
      operations.py         # the 4 live operations
      models.py             # row models: Machine, Group, Match, Occurrence, Relationship
      client.py             # Catalog: ties transport + cache + operations together
    discovery/
      index.py              # load + validate per-brand index files
      match.py              # model-code extraction, aliases, year filter, fuzzy ranking
    agent/
      tools.py              # LangChain tools, handles, budget
      specialist.py         # create_agent graph + middleware
      handoff.py            # PartsHandoff + URL restoration
      prompts.py
      model.py              # create_parts_model()
      lookup_tool.py        # make_lookup_parts_tool() for the orchestrator
    cli.py                  # python -m cnh_parts_agent.cli --machine CX350B --part radiator
  data/indexes/
    casece.json
  scripts/
    census.py               # rebuild a brand's machine index from its sitemap
    capture_fixture.py      # save a sanitized page as a test fixture
  evals/
    benchmark/              # frozen 400-query exam + verify/score
    smoke.jsonl             # ~20 query subset for quick checks
    harness.py              # minimal Deep Agents orchestrator for evals
    run.py
  tests/
    fixtures/casece/ ...
    fixtures/discovery/ ...
    scripted_model.py
    test_parse.py, test_transport.py, test_cache.py, test_discovery.py,
    test_tools.py, test_specialist.py, test_handoff.py, test_benchmark_integrity.py
```

---

## 5. Tooling and setup

- **Python 3.12, managed with uv.** `uv init --package`, `uv add ...`, `uv sync`, `uv run pytest`.
- **Runtime dependencies:** `httpx`, `lxml`, `pydantic`, `pydantic-settings`, `python-dotenv`, `langchain`, `langchain-openai`, `rapidfuzz`.
- **Dev dependencies:** `pytest`, `pytest-asyncio`, `respx` (mocks httpx), `ruff`, `deepagents` (only the eval harness and integration tests need it; the package itself doesn't import it).
- **Pin versions** in `uv.lock` when you scaffold. Use the current releases at that time rather than the old tree's pins.
- **CI** (`.github/workflows/ci.yml`), on every push and PR: `uv sync --locked`, `ruff check`, `pytest -m "not live"`, `python -m evals.benchmark.verify`.
- **Test markers:** `@pytest.mark.live` for anything that hits the site or a model. These are skipped by default and run by hand.

`.env.example`:

```
OPENAI_API_KEY=
PARTS_MODEL=gpt-5.6-luna
PARTS_CACHE_PATH=.cache/catalog.sqlite
PARTS_USER_AGENT=cnh-parts-agent/0.1 (+contact: <your email>)
# LANGSMITH_TRACING=true
# LANGSMITH_API_KEY=
```

---

## 6. Catalog client (layer 1, no LLM)

### 6.1 Storefronts

The brand is a path segment. Everything hangs off `https://www.mycnhstore.com/us/en/{brand}`.

```python
@dataclass(frozen=True)
class Storefront:
    brand: str  # "casece"
    label: str  # "CASE Construction"
    market: str = "us"
    language: str = "en"
    region_code: str = "NA"  # sent to searchInModel

    @property
    def source_id(self) -> str:  # "casece-us-en", matches the old id
        return f"{self.brand}-{self.market}-{self.language}"

    @property
    def home(self) -> str:  # https://www.mycnhstore.com/us/en/casece
        return f"{ORIGIN}/{self.market}/{self.language}/{self.brand}"


STOREFRONTS = {"casece": Storefront("casece", "CASE Construction")}
```

Only `casece` is confirmed. Add the other brands in M5 after confirming their slugs on the site and capturing fixtures (§11).

### 6.2 Transport: be polite and bounded

One `httpx.AsyncClient` per process. Rules, carried over from the old `config.py` limits:

| Rule | Value |
|---|---|
| Allowed host | `www.mycnhstore.com` only. HTTPS only. Re-check after redirects. |
| Rate | 1 request/second per host, plus 0–250 ms of jitter |
| Concurrency | 1 in-flight request per host |
| Timeouts | connect 3 s, read 5 s, 10 s total per operation |
| Attempts | up to 3, retrying only on connect errors and 5xx |
| Response size | 4 MiB max, decoded |
| `429` / `503` | Honor `Retry-After` (default 60 s), and the cooldown applies to *all* requests |
| Circuit breaker | After 3 denials (403/429) within 10 minutes, open for 15 minutes and fail fast with `source_cooldown` |
| Headers | An honest User-Agent from settings. Don't spoof a browser. |

Every response records `url`, `observed_at` and `sha256` of the body. That becomes the evidence attached to tool results.

### 6.3 Cache (SQLite)

- **Key:** `sha256(source_id, operation, normalized args, parser_version)`. The user or conversation is never part of the key, so all lookups share results.
- **Stored value:** the *parsed* result JSON plus evidence. Raw HTML isn't stored.
- **Proposed TTLs** (tune later):

  | Operation | TTL |
  |---|---|
  | `list_groups`, `get_assembly` | 7 days |
  | `search_catalog` | 24 hours |
  | `get_part_relationships` | 24 hours |
  | Empty or "not found" results | 1 hour |

- Bumping `PARSER_VERSION` invalidates old entries automatically.
- Coalesce in-process with a per-key `asyncio.Lock`. Cross-process coalescing isn't needed for the POC.
- Interface: `get(key)`, `put(key, value, ttl)`. Redis can slot in later behind the same interface.

### 6.4 Operations and endpoints

From `old:services/adapters/cnh.py`. `{HOME}` = `storefront.home`.

| Operation | Request | Response | Fixture |
|---|---|---|---|
| `list_groups(machine)` | `{ORIGIN}{machine.path}` | HTML | `model.html` |
| `search_catalog(machine, query, mode)` | `{HOME}/dc/searchInModel/{part-number\|part-description\|figure}?modelCode={id}&paramSearch={q}&regionCode=NA` | JSON | `number.json`, `description.json`, `figure.json`, `negative.json`, `capped-metadata.json` |
| `get_assembly(machine, assembly)` | `{ORIGIN}{assembly.path}` | HTML | `assembly-selected.html` |
| `get_part_relationships(part)` | `{HOME}/cn/assemblyRemanAltsSubModal?productCode={part}` | HTML | `sub-from.html`, `sub-to.html`, `empty-alts.html` |
| *(census only)* sitemap | `{HOME}/sitemap-equipment` | HTML | capture in M2 |

`search_machines` does **not** hit the site. It queries the local index (§7). The old live endpoint (`{HOME}/search/?byModel=1&text=...`, fixture `machines.html`) is only worth porting if the census needs it.

**Search modes** map from tool values to site values: `part_number → part-number`, `part_description → part-description`, `assembly_title → figure`.

**Validation to keep** (from `validate_reference`):
- An assembly's path must belong to the selected machine's catalog.
- `NSS` isn't a purchasable part.
- Paths must start with `{home_path}/na/` and contain exactly one `/cn/`.

These checks stop the model from steering requests to arbitrary URLs.

### 6.5 Result shape (simplified)

```python
class OpResult(BaseModel):
    outcome: Literal["ok", "ambiguous", "not_found", "partial"]
    data: MachineData | GroupData | SearchData | AssemblyData | RelationshipData
    coverage: Coverage  # status: complete_for_scope | partial | unknown, plus reason
    total: int  # rows available
    truncated: bool  # true when rows were cut to `limit`
    evidence: list[Evidence]  # source_url, observed_at, content_hash
```

Pagination is a plain `offset` on the tool arguments instead of cursor snapshots. The row models (`Machine`, `Group`, `Match`, `Occurrence`, `Relationship`) come over from `old:contracts/catalog.py` mostly unchanged. They already avoid inferring fitment.

**Keep the coverage notes.** "No displayed substitutions" is *not* proof that none exist, and catalog quantity isn't inventory. The model needs to see those caveats.

---

## 7. Machine discovery (layer 1, no LLM, no network)

### 7.1 Index files

One JSON file per brand in `data/indexes/{brand}.json`, in the same schema as the old `machine-index.json`:

```json
{ "schema_version": 1, "source_id": "casece-us-en", "updated_at": "...",
  "coverage": "...", "records": [ { "machine": { "machine_ref": {...}, "label": "...",
  "production_dates": "...", "qualifications": [...], "source_url": "..." },
  "evidence": {...}, "discovery_query": "sitemap:..." } ] }
```

`scripts/census.py --brand casece` rebuilds a brand's file by walking `{HOME}/sitemap-equipment`, following the same polite transport rules. Run it by hand, not in CI. Write to a temp file, validate it, then swap it in.

### 7.2 Matching

Pipeline, ported from the rule parts of `old:services/machine_semantic.py`:

1. **Normalize** the query: casefold, strip the brand words (`case`, `new holland`, ...), and collapse whitespace and hyphens.
2. **Extract model codes** with the existing regexes (`MODEL_CODE`, `MODEL_PARTS`) and alias map, e.g. `cx350b`, `cx 350 b`, `CX350-B`.
3. **Filter hard constraints first:** model code, then year (it must fall inside the `production_dates` range when a year is given), then any variant words (tier, region, series).
4. **Rank** what's left with `rapidfuzz` token-set similarity on the label.
5. **Return** the top candidates with scores. More than one plausible candidate means `outcome="ambiguous"`, and the specialist asks the user to choose (§8.2).

**Decision gate:** run `tests/fixtures/discovery/*.json` against this matcher.
- If it passes every case the old semantic search passed, ship it and drop Qdrant and FastEmbed.
- If it doesn't, port the old Qdrant + FastEmbed path behind the same `match()` interface.

Embeddings rank candidates; they never create identities. That rule stays either way.

### 7.3 Multiple brands

If the caller doesn't name a brand, discovery searches every loaded index. A model code that matches catalogs in two brands becomes a normal catalog-choice clarification.

---

## 8. Parts specialist (layer 2)

### 8.1 Tools

Five LangChain tools, each wrapping the `Catalog` client:

| Tool | Purpose |
|---|---|
| `search_machines(query, limit, offset)` | Local index lookup (§7) |
| `list_groups(machine_ref)` | Top-level catalog groups for a machine |
| `search_catalog(machine_ref, query, mode, limit, offset)` | Search within one machine's catalog |
| `get_assembly(machine_ref, assembly_ref, limit, offset)` | Rows of one assembly diagram |
| `get_part_relationships(part_ref)` | Displayed replacements and substitutions |

**Handles:** the model never sees raw references or URLs.
- References become short handles like `m1`, `a3` and `p7`.
- URLs become `url_1`, `url_2` and so on.

Both maps belong to one lookup, and `resolve_handoff_urls` turns them back into exact observed URLs at the end. This is the main protection against made-up links, so port it carefully along with its tests.

**Budget:** 8 tool calls and 30 seconds per lookup, shared across all tools. When it runs out, the tool returns `workflow_budget_exhausted` and the model has to answer with what it has.

### 8.2 Middleware (port from `old:services/subagent.py`)

| Middleware | Hook | Job |
|---|---|---|
| `discover_machine` | `before_agent` | Runs `search_machines(machine_query)` before the first model call, through the normal tool path and budget. |
| `clarify_catalog` | `before_model`, can jump to end | If discovery returned more than one catalog and the user hasn't picked one, returns a deterministic catalog-choice handoff. No model call. |
| `PartsToolBoundary` | `wrap_model_call`, `wrap_tool_call` | Shows only the tools allowed in the current state. Rejects out-of-scope calls: wrong machine, part not seen yet, DIA-kit scope conflict, or a pending assembly that has to be inspected first. Records observed parts and assemblies. |
| `resolve_sources` | `after_agent` | Validates `PartsHandoff`, restores the URL handles, and puts the JSON in the final message. |

**Important change from the old code: keep per-lookup state out of middleware instances.** The old `PartsToolBoundary` stored state on `self`, which only works because a new graph was built for every lookup. Keep doing that, and build a fresh specialist per `lookup_parts` call (it's cheap). If you ever compile the graph once (the §9 Option B route), that state has to move into a custom agent `state_schema`.

### 8.3 Prompt and output

- Port `CATALOG_PROMPT` mostly as written. Its five-step procedure encodes the hard-won lessons:
  - Search the parent assembly by title first.
  - Compare qualifiers before opening an assembly.
  - Match rows on description *and* notes.
  - Withhold a part number when the result is unresolved.
- Swap the hardcoded `source_id casece-us-en` for the selected storefront.
- Output is `response_format=ProviderStrategy(PartsHandoff, strict=True)`:

```python
class PartsHandoff(BaseModel):
    key_facts: list[str]  # source-qualified facts, caveats, clarification questions
    sources: list[str]  # observed URLs only
    store_url: str  # observed product URL for the identified part, or ""
```

### 8.4 Model (OpenAI for the POC)

Keep the old inference profile:

```python
def create_parts_model(model: str = "gpt-5.6-luna") -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        use_responses_api=True,
        reasoning={"effort": "none"},
        max_tokens=1536,
        max_retries=0,
        timeout=30,
        store=False,
        include=["reasoning.encrypted_content"],
    )
```

The specialist accepts any `BaseChatModel`, and `create_parts_model()` is only a default. Moving to Microsoft Foundry later means passing a different model object. Per current Microsoft docs, that's `init_chat_model("azure_ai:<deployment>")` or `langchain_azure_ai.chat_models.AzureAIOpenAIApiChatModel`. Nothing else in the package changes. The model you deploy must support strict JSON-schema structured output.

---

## 9. Plugging into the Deep Agents orchestrator

### Option A: `lookup_parts` as a tool (POC default)

The orchestrator gets one typed tool. Inside, the tool runs a fresh specialist and returns only the handoff JSON, so the orchestrator's context stays clean, just like a subagent's would.

```python
# cnh_parts_agent/agent/lookup_tool.py
class PartsLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine_query: str = Field(
        min_length=1,
        max_length=200,
        description="Machine as the user gave it, including model suffix, variant and year.",
    )
    part_query: str = Field(
        min_length=1,
        max_length=1000,
        description="The component wanted, with qualifiers like side, size, function or control type.",
    )
    selected_machine_label: str | None = Field(
        None, description="Exact catalog label the user picked after a catalog-choice clarification."
    )


def make_lookup_parts_tool(*, catalog: Catalog, model: BaseChatModel) -> BaseTool:
    async def lookup_parts(
        machine_query: str, part_query: str, selected_machine_label: str | None = None
    ) -> str:
        handoff = await retrieve_parts(
            catalog,
            model,
            machine_query=machine_query,
            part_query=part_query,
            selected_machine_label=selected_machine_label,
        )
        return handoff.model_dump_json()

    return StructuredTool.from_function(
        coroutine=lookup_parts,
        name="lookup_parts",
        args_schema=PartsLookup,
        description="Look up a part for a specific machine in the CNH parts catalog. "
        "Returns cited facts, source links and a store URL.",
    )
```

Orchestrator side:

```python
from deepagents import create_deep_agent
from cnh_parts_agent import open_catalog, make_lookup_parts_tool, create_parts_model

async with open_catalog() as catalog:
    lookup_parts = make_lookup_parts_tool(catalog=catalog, model=create_parts_model())
    agent = create_deep_agent(
        model=orchestrator_model,
        tools=[lookup_parts],
        system_prompt=ORCHESTRATOR_PROMPT,
    )
```

**Why this is the default:**
- Arguments are typed, so the trusted `machine_query` and `part_query` reach the discovery step exactly as the user gave them.
- It works with any orchestrator, not only Deep Agents.
- The catalog-choice round trip is easy: the orchestrator asks the user, then calls again with `selected_machine_label`.

### Option B: `CompiledSubAgent` (later, if wanted)

Deep Agents can take a prebuilt graph through `CompiledSubAgent(name=..., description=..., runnable=graph)` and run it with its `task` tool. The parent receives the subagent's last message, and `resolve_sources` already puts the handoff JSON there.

The catch: a subagent only gets a free-text task description. You'd need a first step that pulls `machine_query` and `part_query` out of that text, which is one more model call and one more way to drift from what the user said. Per-lookup state would also have to live in graph state (§8.2).

Do this only if the orchestrator team specifically wants it listed as a subagent.

### Orchestrator prompt guidance (ship it in the README)

- Call `lookup_parts` once per machine and part pair.
- Pass the user's machine wording exactly. Don't "fix" model numbers.
- If `key_facts` contains a catalog-choice question, ask the user and call again with `selected_machine_label` set to the exact label they choose.
- Show `sources` as links. Never invent part numbers or URLs that aren't in the handoff.

---

## 10. Evals (layer 3)

### 10.1 What we're measuring

The old exam sends whole user sentences ("I need a radiator for my 2010 CASE CX350B.") through a conversational parent. To compare against the old number, `evals/harness.py` builds a **minimal Deep Agents orchestrator** that has only the `lookup_parts` tool. It stands in for the real orchestrator, and it doubles as the integration test.

### 10.2 Runs

| Run | Command | When |
|---|---|---|
| Integrity | `uv run python -m evals.benchmark.verify` | Every CI run. Offline, free. |
| Smoke | `uv run python -m evals.run --cases evals/smoke.jsonl --out reports/smoke-<date>.jsonl` | After meaningful agent or prompt changes. About 20 cases. |
| Full | `uv run python -m evals.run --cases evals/benchmark/queries.jsonl --out reports/full-<date>.jsonl` | At milestone gates. 400 cases, costs real tokens. |
| Score | `uv run python -m evals.benchmark.score --report <run> --answers evals/benchmark/answers.jsonl` | After any run |

- **Smoke set:** pick about 20 base cases across part types (radiator, cylinders, pumps, seals, brake pedal, hoses), plus at least 2 catalog-ambiguity cases and 1 known-absent part.
- **Pacing:** the runner goes one case at a time. The cache makes repeat runs much cheaper on site requests. Record cache hit rate next to latency so runs can be compared fairly.

### 10.3 Targets

- **Reference recovery:** at least 370/400 (old: 370).
- **Cited assembly support:** at least 335/400 (old: 335).
- **Unsupported URLs in handoffs:** 0. `resolve_sources` should make this impossible, so treat any as a bug.
- **Track but don't gate:** median latency, model tokens per case, and site requests per case.

### 10.4 New brands

The 400-case exam is CASE CE only. For each new brand, write about 25 cases by hand from real catalog pages: question, expected part number, assembly URL. Use the same JSONL schema as `answers.jsonl`. Don't generate them with an LLM.

---

## 11. Milestones

Each milestone is one or more PRs, and it's done when its acceptance check passes.

### M0: Scaffold
- Create the private repo `icosahedron10/cnh-parts-agent`, `uv init --package`, add the layout from §4 and a CI workflow.
- Copy the assets from §3: benchmark, fixtures, index, discovery cases.
- Port `evals/benchmark/verify.py` and a `test_benchmark_integrity.py`.
- **Accept:** CI is green, `verify` passes, and the fixture hashes match the old manifest.

### M1: Catalog client (casece)
- `storefront.py`, `transport.py`, `cache.py`, `parse.py`, `operations.py`, `client.py`
- Parser tests for each fixture. Transport tests with `respx`: rate limit, Retry-After, circuit breaker, host allowlist, size cap. Cache tests: TTL, parser-version invalidation, coalescing.
- A live smoke script fetches the CX350B radiator assembly.
- **Accept:** offline tests pass. In the live smoke, the first run makes at most 3 site requests and the second run makes 0.

### M2: Machine discovery
- `discovery/index.py`, `discovery/match.py`, `scripts/census.py`
- **Accept:** every case in the discovery fixtures that the old system passed also passes here. Record the decision on dropping Qdrant in the README.

### M3: Specialist
- `agent/tools.py`, `specialist.py`, `handoff.py`, `prompts.py`, `model.py`, `lookup_tool.py`, `cli.py`
- Port the scripted-model tests:
  - catalog-choice clarification with no model call
  - out-of-scope calls rejected
  - budget exhaustion
  - URL handle round trip
  - a fabricated URL raises an error
- **Accept:** offline tests pass, and `uv run python -m cnh_parts_agent.cli --machine CX350B --part radiator` returns a correct live handoff.

### M4: Orchestrator integration and full eval
- `evals/harness.py`, `evals/run.py`, `evals/smoke.jsonl`
- **Accept:** smoke passes with at least 18/20, the full exam meets the §10.3 targets, and the results are committed under `reports/` with the date and model.

### M5: Second brand
- Confirm the brand's slug on the site and capture fixtures with `scripts/capture_fixture.py`.
- Diff its page structure against `casece`. If the parsers need changes, keep them as storefront options, not separate parsers.
- Run the census, add the storefront entry, and write about 25 eval cases by hand.
- **Accept:** parser tests pass on the new brand's fixtures, and its eval scores at least 80%.

### M6: Remaining brands
- Repeat M5 for each one. It should be mostly config, fixtures and a census run.

---

## 12. Scraping etiquette checklist

- [ ] One request at a time, 1 per second at most, with jitter
- [ ] Honest User-Agent with a contact address
- [ ] Honor `Retry-After`. The circuit breaker stops all traffic after repeated 403s or 429s.
- [ ] Cache everything that's cacheable. Discovery never touches the site.
- [ ] Census runs by hand, off-peak, and resumes where it stopped instead of starting over
- [ ] No live calls in CI
- [ ] Log every site request (URL, status, latency, cache hit or miss) so we can show our traffic if asked

---

## 13. Open decisions

| # | Question | Default if nobody decides |
|---|---|---|
| D1 | Which brand is second? | Case IH (an ag line, the most different from CE) |
| D2 | Brand slugs and page structure for the other brands | Confirm on the site in M5. Only `casece` is verified. |
| D3 | Option A tool vs Option B `CompiledSubAgent` | A |
| D4 | Final cache TTLs | §6.3 values |
| D5 | Where the cache file lives when the orchestrator is deployed | A path from `PARTS_CACHE_PATH`, one per process |
| D6 | Drop Qdrant and FastEmbed? | Decided by the M2 gate |
| D7 | Does the orchestrator pass a brand hint? | Optional `brand` argument, added in M5 |

---

## 14. References

- Old explainer: `old:docs/PARTS-SUBAGENT-EXPLAINER.md`. It walks through the specialist flow in detail.
- Old onboarding notebook: `old:docs/intro/how-it-works.md`
- Old exam guide: `old:benchmark/README.md`
- Deep Agents `create_deep_agent` reference: https://reference.langchain.com/python/deepagents/graph/create_deep_agent
- Deep Agents subagents (Python): https://docs.langchain.com/oss/python/deepagents/subagents
- LangChain with Microsoft Foundry models (for the later model swap): https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-models
