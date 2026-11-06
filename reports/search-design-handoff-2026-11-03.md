# Handoff: how should the agent learn where parts live?

For a rubber-duck session. Goal: pick a design that's just complex enough, and test it before building.
Written 2026-11-03 after two runs of the 50-case holdout.

## The problem in one paragraph

The specialist finds parts through CNH's live, word-match catalog search with an 8-call budget.
Most hard failures are vocabulary or location mismatches, not missing data:

- **The catalog uses another word:** tooth → "TINE", ball joint → "BEARING, spherical", master cylinder → "BRAKE ACTUATOR".
- **The part lives under an unexpected assembly:** a cab air filter under "CAB - AIR CONDITIONING", a hydraulic cooler under "RADIATOR ASSEMBLY", a drive belt near "WATER PUMP SYSTEM".

The agent spends its calls guessing that wording. We want the first search to be aimed, without giving up the setup's main strength: very few requests to CNH.

## Hard constraints

- **Politeness:** one request at a time, at least 1 second apart, with backoff on denial (`POC-IMPLEMENTATION-GUIDE.md` §6.2). Few requests to CNH is a feature, not an accident.
- **Evidence rules stay:** answers cite only rows from assemblies actually inspected (`src/cnh_parts_agent/agent/prompts.py`). Any new component may only choose where to look; it never verifies a part.
- **POC scope:** one process, SQLite cache, no service, no Redis, no embeddings so far (README). New dependencies need a reason.

## What we already know

| Fact | Value | Source |
|---|---|---|
| Catalogs in the local machine index | 1,339 CASE CE (partial); 23 Case IH (Magnum 7100, 1400 series, RBX) | `data/indexes/*.json` |
| Cache lifetimes | assemblies/groups 7 days; searches 1 day; empty results 1 hour | README |
| Already cached, free | 169 assemblies, 5,412 rows, 381 searches, 1,079 distinct assembly titles, 1,717 part-name/assembly pairs | `.cache/catalog.sqlite` |
| CNH requests per holdout run | 112 (cold cache), 68 (warm), for 50 lookups | `reports/holdout-score-2026-11-03*.json` |
| Cheap full catalog map? | No. The group page lists only groups; assembly pages don't link the rest of the catalog | fixtures `caseih/tractor-groups.html`, `tractor-assembly.html` |
| Assemblies per catalog | **Unknown.** Figure codes suggest a few hundred; never measured | — |

### What's been tried

`src/cnh_parts_agent/agent/search_guidance.py` (uncommitted) adds host-written guidance to each search and assembly result: which of the user's words are missing, searches not yet tried, "see figure" pointers, and remaining budget. Holdout results:

| | Run 1 | Run 2 (patched) |
|---|---:|---:|
| Pass / partial / fail | 35 / 4 / 11 | 35 / 11 / 4 |
| Input tokens | 0.84M | 1.43M |

- **What it fixed:** confident wrong answers became honest "unverified" partials. H020 now passes by following a "see Figures" pointer.
- **What it broke:**
  - H027 became too literal: it said "tooth" was unresolved although the tine rows it found are the answer.
  - H018 now names numbers from search hits it never opened.
  - H002 used its whole budget and then picked one cable provisionally.
- **Lesson:** knowing which words are missing makes the agent search more. Knowing the catalog's wording would make it search less.

Reviews: `reports/holdout-review-2026-11-03*.jsonl`. These 50 cases are now tuning data, not a clean holdout.

## Options on the table

Rough costs at 1 request/second; assemblies per catalog assumed at about 300 until measured.

| Option | Idea | CNH cost | Main risk |
|---|---|---|---|
| **A. Phrasebook from the cache** | Mine already-cached rows and titles into a map: user wording → catalog wording → likely assembly titles. It grows with every lookup | 0 | Thin coverage now (e.g. "actuator" appears in 1 cached row name) |
| **B. Full crawl ahead of queries** | Download every assembly of every catalog; search locally | about 400k requests (about 4.6 days nonstop), plus refreshes | Request load, the source's terms, staleness of supersessions and availability notes |
| **C. Contents-only crawl** | Ahead of time, store each catalog's assembly titles only; fetch rows live | Unknown: depends on whether titles can be listed cheaply (see Q1) | May need about one request per group per catalog, or may not be possible at all |
| **D. Sample crawl to seed the phrasebook** | Fully download a representative sample (one catalog per family or series, about 20–40), offline and once, only to learn wording and where parts live. Answers still come from live, verified lookups. Option A then keeps growing it | about 6–12k requests once (2–3 hours) | Wording may not carry over between families or brands (see Q2) |

**Current lean: D, then A.** It is crawling before the user's query, as the user suggested, but its purpose is to learn the catalog's language, not to answer from the copy. Because nothing is answered from the crawled data, staleness barely matters. It costs one bounded batch of requests rather than ongoing load. Challenge this.

## Questions the rubber duck should settle

1. **Catalog size and contents cost:** how many assemblies does one catalog have, and can all titles be listed cheaply? One possible route is a broad search with mode `assembly_title`. Settle this with a probe of 1–3 requests, approved by the user first. The answer decides whether C is viable and what D actually costs.
2. **Does wording carry over?** Build the phrasebook from catalogs that exclude the holdout machines. Measure how often its first suggested search reaches the right assembly on holdout queries. Compare CASE CE against Case IH.
3. **Where does it plug in?** The options:
   - (a) replace the lexical guidance in `search_guidance.py`;
   - (b) rewrite the part query before the specialist starts;
   - (c) a new tool the model may call.

   Prefer whatever adds the fewest model tokens.
4. **What to keep from the current patch:** the "see Figures" redirect handling clearly helped (H020). The missing-word flags caused the over-literal answers. Decide keep, trim or revert.
5. **Matching method:** plain word statistics with co-occurrence, or a small embedding model? Default to no new dependency unless Q2 shows a gap.
6. **Is demand concentrated?** If a few dozen models dominate real use, a priority crawl of just those changes the math for B. There are no production logs yet, so this is a user/business question.
7. **The source's terms:** is a one-off 10k-request crawl acceptable to the user, pacing aside?

## What done looks like for the session

- One chosen design, with a one-paragraph rationale and its CNH request cost.
- The first offline experiment written down: its data split, its metric ("first suggested search reaches the right assembly") and its pass bar. It needs no model calls and no CNH requests beyond any probe approved under Q1.
- An explicit out-of-scope list. These are unrelated to search:
  - date reasoning (H012);
  - the `IH 1420` discovery alias bug (H025/H026), a small separate fix in `src/cnh_parts_agent/discovery/match.py`;
  - the forced re-inspection of already-inspected assemblies (`pending_assemblies` in `specialist.py`).

## Pointers

- **Specialist loop and evidence rules:** `src/cnh_parts_agent/agent/specialist.py`, `prompts.py`
- **Current guidance patch and its tests:** `src/cnh_parts_agent/agent/search_guidance.py`, `tests/test_search_guidance.py`
- **Transport, cache, census:** `src/cnh_parts_agent/catalog/transport.py`, `catalog/cache.py`, `scripts/census.py`
- **Holdout definition and runs:** `evals/holdout/`, `reports/holdout-*2026-11-03*`
