"""Offline stage comparison. This module is never imported by the inference runner."""

import argparse
import html
import json
import math
import statistics
from pathlib import Path

from scripts.jev_pilot import load
from scripts.score_jev_pilot import reader_cost


def write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cost(call):
    return (call["usage"]["input_tokens"] * 0.042 / 1_000_000 if call["kind"] == "jev"
            else reader_cost(call["usage"]))


def summarize_stage(folder, gold):
    manifest = load(folder / "manifest.json")
    reviews = load(folder / "reviews.json") if (folder / "reviews.json").exists() else {}
    supplemental = load(folder / "reference.json") if (folder / "reference.json").exists() else {}
    cases = []
    for case in manifest["cases"]:
        path = folder / "cases" / case["id"] / "result.json"
        if not path.exists():
            continue
        result = load(path)
        reference = (gold[case["id"]] if case["id"] in gold else supplemental[case["id"]])["answer"]
        expected = {p["part_number"] for p in reference["parts"]}
        answer = result.get("answer", {})
        returned = {p["part_number"] for p in answer.get("findings", [])}
        support_errors, field_errors = [], []
        for finding in answer.get("findings", []) + answer.get("possible_candidates", []) + answer.get("conditional_alternatives", []):
            source_pages = [page for page in result.get("inspections", [])
                            if finding.get("source_url", page["guide"]["url"]) == page["guide"]["url"]]
            if not any(row["part_number"] == finding["part_number"]
                       for page in source_pages for row in page["evidence"]["occurrences"]):
                support_errors.append(finding["part_number"])
            if finding.get("source_row_id") and not any(
                row["part_number"] == finding["part_number"]
                and row["description"] == finding["catalog_description"]
                and row["callout"] == finding["diagram_reference"]
                and row["catalog_quantity"] == finding["quantity"]
                for page in source_pages for row in page["evidence"]["occurrences"]
            ):
                field_errors.append(finding["part_number"])
        calls = result.get("calls", [])
        cases.append({
            "id": case["id"], "query": case["query"], "machine": case["machine_query"],
            "status": result["status"], "stop_reason": result.get("stop_reason"),
            "expected": sorted(expected), "returned": sorted(returned),
            "all_recovered": expected <= returned, "exact_reference_set": expected == returned,
            "extra": sorted(returned - expected), "missing": sorted(expected - returned),
            "unsupported_numbers": support_errors, "source_field_errors": field_errors, "answer": answer,
            "reference": reference, "review": reviews.get(case["id"], {"verdict": "pending"}),
            "decisions": result.get("decisions", []),
            "inspected_guides": [{"label": p["guide"]["label"], "url": p["guide"]["url"],
                                   "answer": p["answer"]} for p in result.get("inspections", [])],
            "logical_cost_usd": sum(cost(c) for c in calls),
            "new_cost_usd": sum(cost(c) for c in calls if not c["reused"]),
            "calls": len(calls), "reused_calls": sum(c["reused"] for c in calls),
            "source_requests": sum(e["http_attempts"] for e in result.get("source_events", [])),
            "latency_seconds": result.get("latency_seconds"), "error": result.get("error"),
        })
    summary = {
        "stage": folder.name, "scheduled": len(manifest["cases"]), "recorded": len(cases),
        "attempt_type": "interrupted" if ("interrupted" in folder.name or "incomplete" in folder.name) else "complete_stage",
        "execution_errors": sum(c["status"] != "completed" for c in cases),
        "all_references_recovered": sum(c["all_recovered"] for c in cases),
        "exact_reference_set": sum(c["exact_reference_set"] for c in cases),
        **{verdict: sum(c["review"]["verdict"] == verdict for c in cases)
           for verdict in ("pass", "partial", "miss", "pending")},
        "unsupported_number_count": sum(len(c["unsupported_numbers"]) for c in cases),
        "source_field_error_count": sum(len(c["source_field_errors"]) for c in cases),
        "logical_cost_usd": sum(c["logical_cost_usd"] for c in cases),
        "new_cost_usd": sum(c["new_cost_usd"] for c in cases),
        "calls": sum(c["calls"] for c in cases), "reused_calls": sum(c["reused_calls"] for c in cases),
        "source_requests": sum(c["source_requests"] for c in cases),
        "guides_median": statistics.median([len(c["inspected_guides"]) for c in cases]) if cases else None,
        "guides_max": max([len(c["inspected_guides"]) for c in cases], default=0),
        "observed_latency_median_seconds": statistics.median(
            [c["latency_seconds"] for c in cases if c["latency_seconds"] is not None]
        ) if any(c["latency_seconds"] is not None for c in cases) else None,
        "observed_latency_p95_seconds": sorted(c["latency_seconds"] for c in cases if c["latency_seconds"] is not None)[
            math.ceil(sum(c["latency_seconds"] is not None for c in cases) * .95) - 1
        ] if any(c["latency_seconds"] is not None for c in cases) else None,
        "fresh_inference": manifest["config"]["fresh_inference"],
    }
    report = {"summary": summary, "cases": cases}
    write(folder / "score.json", report)
    return report


def recorded_spend(root):
    total, fresh_calls, pending = 0.0, 0, []
    for path in root.glob("*/cases/*/calls/*.response.json"):
        record = load(path)
        if record["reused"]:
            continue
        request = load(path.with_name(path.name.replace(".response.json", ".request.json")))
        total += cost({"kind": request["kind"], "usage": record["response"]["usage"]})
        fresh_calls += 1
    for path in root.glob("*/cases/*/calls/*.request.json"):
        if not path.with_name(path.name.replace(".request.json", ".response.json")).exists():
            pending.append(str(path.relative_to(root)))
    return {"recorded_new_api_cost_usd": total, "fresh_calls_with_usage": fresh_calls,
            "requests_without_response_usage": pending,
            "basis": "All retained response records, including interrupted attempts; missing response usage is unknown, not assumed free"}


def report(root, baseline):
    gold = {r["id"]: r for line in Path("evals/benchmark/answers.jsonl").read_text(encoding="utf-8").splitlines()
            if (r := json.loads(line))}
    stages = [summarize_stage(p, gold) for p in sorted(root.iterdir())
              if p.is_dir() and (p / "manifest.json").exists()]
    original = load(baseline / "score.json")
    accounting = recorded_spend(root)
    readiness = load(root / "readiness.json") if (root / "readiness.json").exists() else None
    stage_notes = load(root / "stage-notes.json") if (root / "stage-notes.json").exists() else []
    data = {"baseline": original["summary"], "stages": stages, "experiment_accounting": accounting,
            "readiness": readiness, "stage_notes": stage_notes}
    write(root / "comparison.json", data)

    def e(value):
        return html.escape(str(value))

    baseline_summary = original["summary"]
    notes_html = ('<h2>What changed, why, and what happened</h2><table><tr><th>Stage</th>'
                  '<th>Change</th><th>Reason</th><th>Observed effect</th></tr>' + ''.join(
                      f'<tr><td>{e(n["stage"])}</td><td>{e(n["change"])}</td>'
                      f'<td>{e(n["why"])}</td><td>{e(n["effect"])}</td></tr>' for n in stage_notes)
                  + '</table>') if stage_notes else ''
    readiness_html = (f'<p><strong>{e(readiness["recommendation"])}</strong> {e(readiness["scope"])}</p>'
                      f'<p><a href="readiness.json">Readiness assessment</a> · '
                      f'<a href="source-search-validation.json">Rejected-source recovery check</a> · '
                      f'<a href="pricing-verification.json">Pricing assumptions</a></p>') if readiness else ''
    rows = [f'<tr><td>00 · Single guide baseline</td><td>20/20</td><td>{baseline_summary["semantic_pass"]}</td>'
            f'<td>{baseline_summary["semantic_partial"]}</td><td>{baseline_summary["semantic_miss"]}</td>'
            f'<td>{baseline_summary["all_reference_recovered"]}</td><td>${baseline_summary["total_cost_usd"]:.4f}</td><td>—</td></tr>']
    details = []
    for stage in stages:
        s = stage["summary"]
        if s["attempt_type"] == "interrupted":
            details.append(f'<details><summary>{e(s["stage"])} — retained interrupted attempt</summary>'
                           f'<pre>{e(json.dumps(s, indent=2))}</pre></details>')
            continue
        rows.append(f'<tr><td><a href="#{e(s["stage"])}">{e(s["stage"])}</a></td><td>{s["recorded"]}/{s["scheduled"]}</td>'
                    f'<td>{s["pass"]}</td><td>{s["partial"]}</td><td>{s["miss"]}</td>'
                    f'<td>{s["all_references_recovered"]}</td><td>${s["logical_cost_usd"]:.4f}</td>'
                    f'<td>${s["new_cost_usd"]:.4f}</td></tr>')
        stage_rows, case_details = [], []
        for case in stage["cases"]:
            route = " → ".join(f'{c["selected"]["label"]} ({c.get("probability",1):.0%})'
                               for c in case["decisions"])
            possible = ", ".join(p["part_number"] for p in case["answer"].get("possible_candidates", []))
            lead_note = f'<small>Unconfirmed lead(s): {e(possible)}</small>' if possible else ''
            stage_rows.append(f'<tr><td>{e(case["id"])}<small>{e(case["query"])}</small></td>'
                              f'<td>{e(", ".join(case["returned"]) or "No confirmed candidate")}{lead_note}</td>'
                              f'<td>{e(case["review"]["verdict"])}</td><td>{e(route)}</td></tr>')
            page_links = " · ".join(f'<a href="{e(s["stage"])}/cases/{e(case["id"])}/pages/{i:02}.html">'
                                    f'HTML {i}</a>' for i in range(1, len(case["inspected_guides"]) + 1)
                                    if (root / s["stage"] / "cases" / case["id"] / "pages" / f"{i:02}.html").exists())
            case_details.append(f'<details><summary>{e(case["id"])} — {e(case["query"])}</summary>'
                           f'<p>{e(case["review"].get("note", "Review pending"))}</p><p>{page_links}</p>'
                           f'<pre>{e(json.dumps(case, indent=2, ensure_ascii=False))}</pre></details>')
        details.append(f'<section id="{e(s["stage"])}"><h2>{e(s["stage"])}</h2>'
                       f'<details><summary>Navigation choices and evidence for {s["recorded"]} cases</summary>'
                       f'<table><tr><th>Case</th><th>Returned numbers</th>'
                       f'<th>Review</th><th>Navigation choices</th></tr>{"".join(stage_rows)}</table>'
                       f'{"".join(case_details)}</details></section>')
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Jev staged experiment</title>
<style>body{{font:15px/1.5 system-ui;color:#192e38;background:#f5f7f9;margin:32px}}main{{max-width:1550px;margin:auto}}
table{{border-collapse:collapse;width:100%;background:white;margin:24px 0}}th,td{{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #d6dfe5}}th{{background:#e7eef2}}small{{display:block;color:#526673}}details{{background:white;margin:12px 0;padding:16px;border:1px solid #d6dfe5;border-radius:8px}}summary{{cursor:pointer;font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}a{{color:#075aa3}}</style>
<main><h1>Persistent Jev navigation: staged results</h1>
{readiness_html}
<p>Stages 01–07, 10 and 13 use the same 20 distinct development cases. Stage 08 uses 20 separately selected base cases excluded from development (seed 20261107); stage 11 repeats those cases after error review, so it is no longer untouched validation. Both sets come from the historical benchmark, not a random production sample; they share machines and some assembly pages. The Farmall question is rechecked separately. Original questions are preserved. Machine resolution is outside this test. The inference process never reads answer keys. Raw baseline, interrupted attempts and stage artifacts are retained.</p>
<p>Stage 14 draws 40 further base cases with seed 20261106, excluding all 40 development/validation families. It uses the unchanged stage-13 lookup code and fresh model calls. Existing source snapshots remain eligible for reuse; missing catalog pages are fetched on demand. It tests new questions within the same historical benchmark, with overlapping machines and guides.</p>
<p>The navigator retains the full question, component/parent hypotheses, observed menus, failed-page evidence, rejected candidates and remaining budget. It can backtrack, follow literal source figure references, replan, and use at most two native catalog searches to nominate guides. Every accepted part comes from a read guide. Limits: four opened menu groups, eight inspected guides, twenty decisions and 240 seconds. Unconfirmed functional leads remain separate from affirmative findings.</p>
<table><tr><th>Stage</th><th>Recorded</th><th>Pass</th><th>Partial</th><th>Miss</th><th>All reference numbers recovered</th><th>Logical API cost</th><th>New API cost</th></tr>{''.join(rows)}</table>
<p>Pass/partial/miss are implementation-agent reviews against source evidence and reference answers, not independent human fitment certification. Number recovery alone does not establish semantic correctness. Pending reviews are not passes. Source-present numbers can still be irrelevant to the request. All scheduled cases stay in the denominator. Route percentages are Jev choice probabilities, not fitment confidence.</p>
<p>Logical cost includes identical calls replayed from the experiment cache; new cost counts only fresh API responses. Usage-based estimates at the recorded pricing, not invoices. Reused source snapshots reduce drift. New catalog requests share the existing pacing and validation. Repeat runs disable inference replay.</p>
<p>Recorded new usage across this experiment, including interrupted attempts: <strong>${accounting['recorded_new_api_cost_usd']:.4f}</strong>. Requests without returned usage: {len(accounting['requests_without_response_usage'])}; their billing is unknown. The earlier baseline is separate.</p>
<p><a href="comparison.json">Full comparison JSON</a> · <a href="https://docs.typesafe.ai/models">Jev rates</a> · <a href="https://developers.openai.com/api/docs/pricing">OpenAI rates</a></p>
{notes_html}
{''.join(details)}</main></html>'''
    (root / "report.html").write_text(page, encoding="utf-8")
    print(json.dumps([s["summary"] for s in stages], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("reports/jev-iterations-20261105"))
    parser.add_argument("--baseline", type=Path, default=Path("reports/jev-pilot-20-20261105-v2"))
    args = parser.parse_args()
    report(args.root, args.baseline)
