"""Offline scoring and HTML report for a completed Jev pilot; never calls a model."""

import argparse
import html
import json
import re
import statistics
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def reader_cost(usage):
    details = usage["input_tokens_details"]
    cached = details.get("cached_tokens", 0)
    written = details.get("cache_write_tokens", 0)
    ordinary = usage["input_tokens"] - cached - written
    if ordinary < 0:
        raise ValueError("Overlapping input usage categories")
    return (ordinary + cached * 0.05 + written * 1.25 + usage["output_tokens"] * 5) / 1_000_000


def score(folder):
    manifest = load(folder / "manifest.json")
    gold = {row["id"]: row for line in Path("evals/benchmark/answers.jsonl").read_text(encoding="utf-8").splitlines()
            if (row := json.loads(line))}
    reviews = load(folder / "semantic-review.json") if (folder / "semantic-review.json").exists() else {}
    rows = []
    for case in manifest["cases"]:
        case_folder = folder / "cases" / case["id"]
        path = case_folder / "result.json"
        if not path.exists():
            continue
        result = load(path)
        reference = gold[case["id"]]["answer"]
        expected = {part["part_number"] for part in reference["parts"]}
        expected_urls = {url for part in reference["parts"] for url in part["assembly_urls"]}
        expected_groups = {match[1] for condition in reference["conditions"]
                           if (match := re.match(r"^Assembly scope: (\d{2})[.-]", condition))}
        if not expected_groups:
            raise ValueError(f"No reference functional group for {case['id']}")
        choices = result.get("choices", [])
        if not choices:
            choices = [load(p) for phase in ("functional_group", "guide")
                       if (p := case_folder / f"{phase}-decision.json").exists()]
        guide = next((c["selected"] for c in choices if c["phase"] == "guide"), {})
        group = next((c["selected"] for c in choices if c["phase"] == "functional_group"), {})
        answer = result.get("answer", {})
        proposed = {part["part_number"] for part in answer.get("findings", [])}
        observed = {part["part_number"] for part in result.get("evidence", {}).get("occurrences", [])}
        jev_cost = 0
        jev_tokens = 0
        jev_calls = 0
        for phase in ("functional_group", "guide"):
            p = case_folder / f"{phase}-response.json"
            if p.exists():
                usage = load(p)["response"]["usage"]
                jev_tokens += usage["input_tokens"]
                jev_cost += usage["input_tokens"] * 0.042 / 1_000_000
                jev_calls += 1
        reader_tokens, reader_outputs, cost = 0, 0, 0
        reader_path = case_folder / "reader-response.json"
        if reader_path.exists():
            usage = load(reader_path)["response"]["usage"]
            reader_tokens, reader_outputs = usage["input_tokens"], usage["output_tokens"]
            cost = reader_cost(usage)
        row = {
            "id": case["id"], "query": case["query"], "machine": case["machine_query"],
            "status": result["status"], "choices": choices, "guide_url": guide.get("url"),
            "reference_group_selected": group.get("label", "").split(" - ")[0] in expected_groups,
            "reference_guide_selected": guide.get("url") in expected_urls,
            "reference_part_in_selected_guide": bool(observed & expected),
            "any_reference_recovered": bool(proposed & expected),
            "all_reference_recovered": bool(expected) and expected <= proposed,
            "exact_reference_set": bool(expected) and expected == proposed,
            "expected_parts": sorted(expected), "returned_parts": sorted(proposed),
            "extra_parts": sorted(proposed - expected), "missing_parts": sorted(expected - proposed),
            "answer": answer, "reference_answer": reference,
            "evidence_violations": result.get("evidence_violations", []),
            "review": reviews.get(case["id"], {"verdict": "pending"}),
            "jev_calls": jev_calls, "jev_input_tokens": jev_tokens,
            "reader_calls": int(reader_path.exists()), "reader_input_tokens": reader_tokens,
            "reader_output_tokens": reader_outputs, "jev_cost_usd": jev_cost,
            "reader_cost_usd": cost, "total_cost_usd": jev_cost + cost,
            "source_requests": result.get("source_requests", 0),
            "latency_seconds": result.get("latency_seconds"), "cleanup": result.get("cleanup"),
            "error": result.get("error"),
        }
        rows.append(row)
    summary = {
        "scheduled": len(manifest["cases"]), "completed_records": len(rows),
        "completed_inference": sum(r["status"] == "completed" for r in rows),
        "reference_group_selected": sum(r["reference_group_selected"] for r in rows),
        "reference_guide_selected": sum(r["reference_guide_selected"] for r in rows),
        "reference_part_in_selected_guide": sum(r["reference_part_in_selected_guide"] for r in rows),
        "any_reference_recovered": sum(r["any_reference_recovered"] for r in rows),
        "all_reference_recovered": sum(r["all_reference_recovered"] for r in rows),
        "exact_reference_set": sum(r["exact_reference_set"] for r in rows),
        "semantic_pass": sum(r["review"]["verdict"] == "pass" for r in rows),
        "semantic_partial": sum(r["review"]["verdict"] == "partial" for r in rows),
        "semantic_miss": sum(r["review"]["verdict"] == "miss" for r in rows),
        "semantic_pending": sum(r["review"]["verdict"] == "pending" for r in rows),
        "source_requests": sum(r["source_requests"] for r in rows)
        + manifest.get("setup_attempt", {}).get("reused_source_requests", 0),
        "jev_calls": sum(r["jev_calls"] for r in rows),
        "reader_calls": sum(r["reader_calls"] for r in rows),
        "jev_input_tokens": sum(r["jev_input_tokens"] for r in rows),
        "reader_input_tokens": sum(r["reader_input_tokens"] for r in rows),
        "reader_output_tokens": sum(r["reader_output_tokens"] for r in rows),
        "jev_cost_usd": sum(r["jev_cost_usd"] for r in rows),
        "reader_cost_usd": sum(r["reader_cost_usd"] for r in rows),
        "total_cost_usd": sum(r["total_cost_usd"] for r in rows),
        "latency_median_seconds": statistics.median([r["latency_seconds"] for r in rows]) if rows else None,
        "latency_excludes_inter_case_pauses": True,
        "evidence_violation_count": sum(len(r["evidence_violations"]) for r in rows),
        "reader_not_found": sum(r["answer"].get("status") == "not_found" for r in rows),
        "reader_input_median_tokens": statistics.median(
            [r["reader_input_tokens"] for r in rows if r["reader_calls"]]
        ) if any(r["reader_calls"] for r in rows) else None,
        "source_rows_preserved": sum((r["cleanup"] or {}).get("rows_preserved", 0) for r in rows),
        "clean_html_median_chars": statistics.median(
            [r["cleanup"]["clean_chars"] for r in rows if r["cleanup"]]
        ) if any(r["cleanup"] for r in rows) else None,
        "cost_basis": manifest["pricing"],
        "scope": manifest["scope"],
    }
    if len(rows) == len(manifest["cases"]) and rows:
        summary["mean_api_cost_usd"] = summary["total_cost_usd"] / len(rows)
        summary["projected_400_api_cost_usd"] = summary["mean_api_cost_usd"] * 400
        summary["projection_basis"] = "Linear extrapolation, same models and one-guide budget; excludes machine resolution"
    report = {"summary": summary, "cases": rows, "setup_attempt": manifest.get("setup_attempt")}
    (folder / "score.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render_html(folder, report)
    return summary


def render_html(folder, report):
    def e(value):
        return html.escape(str(value))

    summary = report["summary"]
    table = []
    details = []
    for row in report["cases"]:
        labels = [choice["selected"]["label"] for choice in row["choices"]]
        probabilities = [f'{choice.get("probability", 1):.0%}' for choice in row["choices"]]
        route = " → ".join(f"{label} ({prob})" for label, prob in zip(labels, probabilities, strict=True))
        review = row["review"]
        table.append(
            f'<tr><td><a href="#{e(row["id"])}">{e(row["id"])}</a><small>{e(row["machine"])}</small></td>'
            f'<td>{e(row["query"])}</td><td>{e(route)}</td>'
            f'<td>{e(", ".join(row["returned_parts"]) or "No match")}</td>'
            f'<td class="{e(review["verdict"])}">{e(review["verdict"])}</td>'
            f'<td>${row["total_cost_usd"]:.5f}</td></tr>'
        )
        ranks = "".join(
            f'<h3>{e(choice["phase"])} · {choice.get("option_count", 1)} options</h3><ol>'
            + "".join(f'<li>{e(rank["label"])} — {rank["probability"]:.0%}</li>'
                      for rank in choice.get("ranking", [])[:8]) + "</ol>"
            for choice in row["choices"]
        )
        details.append(
            f'<details id="{e(row["id"])}"><summary>{e(row["id"])} — {e(row["query"])}</summary>'
            f'<p><strong>Review:</strong> {e(review.get("note", "Pending"))}</p>{ranks}'
            f'<p><a href="{e(row["guide_url"] or "")}">Selected CNH guide</a> · '
            f'<a href="cases/{e(row["id"])}/reader.html">HTML supplied to reader</a></p>'
            f'<h3>Reader answer</h3><pre>{e(json.dumps(row["answer"], indent=2, ensure_ascii=False))}</pre>'
            f'<p>Automated evidence flags: {e(json.dumps(row["evidence_violations"]))}</p>'
            f'<h3>Reference comparison</h3><p>Expected: {e(", ".join(row["expected_parts"]))}; '
            f'missing: {e(", ".join(row["missing_parts"]) or "none")}; '
            f'extra: {e(", ".join(row["extra_parts"]) or "none")}</p>'
            f'<pre>{e(row["reference_answer"]["text"])}</pre></details>'
        )
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Jev · 20-case pilot</title>
<style>body{{font:15px/1.5 system-ui;background:#f7f8fa;color:#172330;margin:32px}}main{{max-width:1500px;margin:auto}}
h1{{font-size:32px}}h2{{margin-top:30px}}.cards{{display:flex;gap:14px;flex-wrap:wrap}}.card{{background:white;padding:18px;border:1px solid #d6dde4;border-radius:8px;min-width:160px}}
.card strong{{display:block;font-size:28px}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #dde3e9;vertical-align:top}}th{{background:#e9eef3}}small{{display:block;color:#536578}}
.pass{{color:#137040}}.partial{{color:#966000}}.miss,.error{{color:#ac2634}}details{{background:white;border:1px solid #d6dde4;padding:18px;margin:12px 0;border-radius:8px}}summary{{cursor:pointer;font-weight:600}}pre{{white-space:pre-wrap;font-size:13px;overflow-wrap:anywhere;background:#f2f5f7;padding:16px}}a{{color:#145c9e}}.note{{max-width:1100px;color:#415267}}</style>
<main><h1>Jev navigation → HTML reader</h1><p>20 distinct benchmark cases · seed 20261105 · Jev 1.14.0 + GPT-6.1 Terra / high</p>
<div class="cards"><div class="card"><strong>{summary['completed_records']}/20</strong>Cases recorded</div>
<div class="card"><strong>{summary['reference_part_in_selected_guide']}/20</strong>Guide contains reference part</div>
<div class="card"><strong>{summary['semantic_pass']}/20</strong>Reviewed passes</div>
<div class="card"><strong>${summary['total_cost_usd']:.4f}</strong>Usage-based API cost estimate</div></div>
<p class="note">After machine resolution. One functional-group choice, one guide choice, and one reader call per case. No rescue routes. Original questions and source rows are preserved. HTML only; diagram images are not supplied. These 20 cases come from the historical CASE Construction benchmark; this is a development sample, not a new holdout. Reference recovery and semantic correctness are scored separately. A different guide can still support the correct part. Review verdicts are an assistant assessment against reference answers and source rows, not an independent human evaluation. All scheduled cases remain in the denominator.</p>
<h2>Navigation and results</h2><table><thead><tr><th>Case / machine</th><th>Request</th><th>Jev route</th><th>Reader parts</th><th>Review</th><th>Cost</th></tr></thead><tbody>{''.join(table)}</tbody></table>
<h2>Review details</h2>{''.join(details)}
<h2>Usage and methodology</h2><pre>{e(json.dumps(summary, indent=2))}</pre>
<p class="note">Cost is calculated from recorded API token usage at published Standard rates, not an invoice. Jev input: $0.042/M tokens; output free. Terra: $1/M uncached input, $0.05/M cached input, $1.25/M default cache writes, $5/M output (including reasoning). No paid grader is used. One initial reader request failed validation; its two Jev decisions were reused. See manifest.json for the preserved setup attempt.</p>
<p><a href="https://docs.typesafe.ai/models">Jev pricing</a> · <a href="https://developers.openai.com/api/docs/pricing">OpenAI pricing</a> · <a href="score.json">Full JSON report</a> · <a href="manifest.json">Frozen sample</a></p></main></html>'''
    (folder / "report.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(score(args.run), indent=2))
