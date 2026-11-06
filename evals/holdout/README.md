# General specialist holdout

Fifty new requests frozen on 2026-11-03 (America/Chicago), before the first live
inference. The requests cover CASE CE and Case IH machines, component qualifiers,
catalog ambiguity, missing/unknown models, unsupported brands, model spelling,
explicit identifiers, symptoms, multiple parts, commerce claims, fabricated-answer
instructions, and explicit catalog selection.

`queries.jsonl` contains only IDs, human-readable questions, and manually supplied
`lookup_parts` arguments. The direct runner passes only those arguments to a fresh
specialist; it does not invoke the orchestrator or send review criteria to the model.
`rubric.jsonl` contains the pre-run review criteria. `manifest.json` freezes both
files by SHA-256. Existing benchmark queries, answer keys and system prompts were
not changed. Authoring used general user intents and local machine labels, without
reading answer keys or prior answer records. A subsequent comparison found no exact
question overlap with the 400-case exam or the 25-case Case IH suite.

This is a new-query holdout, not a random production sample or a guarantee of
unseen models/catalogs. Existing catalog cache entries are retained. Explicit part
numbers are user inputs, not known-answer targets. Once these cases are used to
tune the system, treat them as regression cases and create another untouched set
for the next generalization check.

## Run and review

```powershell
uv run python -m evals.run --direct --live --cases evals/holdout/queries.jsonl --out reports/holdout-YYYY-MM-DD.jsonl
uv run python -m evals.holdout.score --report reports/holdout-YYYY-MM-DD.jsonl --review reports/holdout-review-YYYY-MM-DD.jsonl --out reports/holdout-score-YYYY-MM-DD.json
```

The score command refuses incomplete or mismatched schedules and refuses output
overwrites. It can also run without `--review` to report mechanical checks only.
Keep every original failure in the denominator; reruns are separate reports.

Review each response against its saved discovery/search/assembly observations and
the frozen criterion. Reviews contain `id`, `verdict`, `reason`, and zero-based
`observation_indices` into the corresponding case's `evidence` list:

- `pass`: relevant supported findings, or appropriate handling of actual ambiguity,
  missing information, scope limits, or unsupported action requests.
- `partial`: useful supported evidence or honest bounded uncertainty, but an
  ordinary retrieval request remains incomplete or a supplied qualifier unverified.
- `fail`: material unsupported inference, ignored supplied constraint, wrong
  component/system, avoidable discovery failure, or stopping with an unrelated part
  when a relevant route remains available.
- `blocked`: execution or source failure prevents a meaningful answer.

These verdicts are evidence-based implementation-agent review, not independent
human assessment, verified fitment, or gold-reference recovery. Mechanical checks
detect unsupported URLs and part references; they do not establish that an answer
matches the request. They can flag a number legitimately quoted from an inspected
row's replacement or packaging note because that number has no separate occurrence.
Keep those flags and explain them in the review rather than changing the checker.

The first run and its case-by-case review are saved under `reports/holdout-*2026-11-03*`.
The offline discovery probe is diagnostic only and is not included in the 50-case score.

## First-run findings

Direct specialist, `gpt-5.6-luna`, existing settings and shared cache; one original
attempt per query. The application implementation and prompts remained unchanged.

| Measurement | Result |
|---|---:|
| Completed handoffs | 50/50 |
| Agent-reviewed passes | 35/50 |
| Partial answers | 4/50 |
| Material failures | 11/50 |
| Tool errors / unobserved citation URLs | 0 / 0 |
| Mechanical evidence passes | 44/50 |
| Requests reaching the catalog transport | 112 |
| Median / 95th-percentile lookup time | 7.76 / 13.89 seconds |
| Sum of lookup durations | 366 seconds |
| Maximum specialist tool calls in one case | 7 (limit 8) |

The 35 passes include correct clarification and boundary handling, not only part
retrieval. This is a small deliberately varied diagnostic sample, not a calibrated
70% estimate of production accuracy. Review is by the implementation agent against
saved evidence; no independent human fitment assessment is claimed.

Main failure groups:

- **Component and qualifier matching:** H004 pursued engine filters despite a cab
  filter request and an observed cab air-conditioning assembly; H017 called an
  engine-system cooler a hydraulic oil cooler. H009 and H013 promoted generic rows
  to upper-hose/rod-seal candidates without supporting position/function evidence.
- **Date reasoning:** H012 ranked 2013-and-newer glass for a supplied 2012 machine
  and said the supplied year could not distinguish the configurations.
- **Discovery aliases:** H025/H026 failed to resolve `IH 1420` and `IH 1470` even
  though those catalogs are installed. An offline probe reproduces the failure and
  succeeds with `1420`/`1470` or `Case IH` spelling. The normalizer treats bare `IH`
  as part of a model prefix.
- **Unsupported extra details:** H028 included an uninspected stocking-list part;
  H030 supplied a callout for an assembly that was never inspected and whose search
  result supplied no callout.
- **Weak stopping and negative reasoning:** H020 stopped at a related brake valve
  with a service-reference route still available; H043 overstated catalog-wide
  absence and then returned irrelevant injector candidates.

The four partial answers were H001 (unresolved belt), H008 (bottom-roller position
unresolved), H019 (24V unverified and an open-ended date paraphrased as onward), and
H036 (unsupported brand handled with an unhelpful generic no-match message).

The six mechanical flags are preserved. H028 is a real uninspected-part violation;
H021, H022 and H050 quote replacement numbers from inspected row notes; H041/H042
echo the requested identifiers while explicitly reporting no match. The latter
five are checker limitations, not proposed unsupported part findings. Conversely,
the checker misses most semantic failures, including H012 and H017. Valid URLs and
an inspected part number are insufficient to establish a correct answer.

Ambiguous catalog choices, explicit catalog selection, model spacing, omitted
brand, UK-versus-US scope, fitment-guarantee refusal, unsupported price/stock claims,
fabricated-answer instructions, and dealer/order boundaries were handled usefully.
The injection request's fake part was echoed only in a denial; its fake URL was not
returned. No dealer message or order was sent.

Artifacts:

- [Frozen queries](queries.jsonl) and [pre-run criteria](rubric.jsonl)
- [Raw responses, observations and diagnostics](../../reports/holdout-2026-11-03.jsonl)
- [Every case's review and evidence indices](../../reports/holdout-review-2026-11-03.jsonl)
- [Machine-readable summary](../../reports/holdout-score-2026-11-03.json)
- [Offline alias diagnosis](../../reports/holdout-discovery-probe-2026-11-03.json)

Evaluation infrastructure checks: Ruff passed, all 248 offline tests passed, and
the original 400-question benchmark's frozen integrity verification passed.
