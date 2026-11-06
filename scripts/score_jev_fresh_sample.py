"""Offline, compact report for a frozen Jev sample, including every navigation trace."""

import argparse
import html
import json
from pathlib import Path

from scripts.jev_pilot import load
from scripts.score_jev_iterations import summarize_stage


def report(root, stage):
    folder = root / stage
    manifest = load(folder / "manifest.json")
    gold = {r["id"]: r for line in Path("evals/benchmark/answers.jsonl").read_text(encoding="utf-8").splitlines()
            if (r := json.loads(line))}
    scored = summarize_stage(folder, gold)
    summary = scored["summary"]
    rows, details = [], []

    def e(value):
        return html.escape(str(value))

    for case in scored["cases"]:
        result = load(folder / "cases" / case["id"] / "result.json")
        verdict = case["review"]["verdict"]
        primary = ", ".join(case["returned"]) or "No confirmed candidate"
        leads = ", ".join(p["part_number"] for p in case["answer"].get("possible_candidates", []))
        if leads:
            primary += f"; unconfirmed: {leads}"
        rows.append(f'<tr><td><a href="#{e(case["id"])}">{e(case["id"])}</a><small>{e(case["query"])}</small></td>'
                    f'<td>{e(primary)}</td><td class="{e(verdict)}">{e(verdict)}</td>'
                    f'<td>{len(case["inspected_guides"])}</td><td>${case["new_cost_usd"]:.4f}</td></tr>')
        choices = ''.join(f'<li>{e(d["selected"]["label"])}</li>' for d in case["decisions"])
        searches = [s["queries"] for s in result.get("catalog_searches", [])]
        evidence = " · ".join(f'<a href="cases/{e(case["id"])}/pages/{p.name}">{e(p.stem)} HTML</a>'
                              for p in sorted((folder / "cases" / case["id"] / "pages").glob("*.html")))
        details.append(f'<details id="{e(case["id"])}"><summary>{e(case["id"])} — {e(verdict)} — {e(case["query"])}</summary>'
                       f'<p><strong>Review:</strong> {e(case["review"].get("note", "Pending"))}</p>'
                       f'<p><strong>Reference numbers:</strong> {e(", ".join(case["expected"]))}</p>'
                       f'<p><strong>Navigation:</strong></p><ol>{choices}</ol>'
                       f'<p><strong>Native search queries:</strong> {e(json.dumps(searches))}</p>'
                       f'<p>{evidence} · <a href="cases/{e(case["id"])}/result.json">Complete recorded trace</a></p>'
                       f'<pre>{e(json.dumps(case["answer"], indent=2, ensure_ascii=False))}</pre></details>')
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jev: fresh {summary['scheduled']}-case test</title><style>
body{{font:15px/1.55 system-ui;background:#f4f7f9;color:#20313b;margin:28px}}main{{max-width:1250px;margin:auto}}
table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:12px;border-bottom:1px solid #d6e0e5;text-align:left;vertical-align:top}}th{{background:#e6edf1}}
small{{display:block;color:#50626d}}a{{color:#075ea3}}details{{background:white;border:1px solid #d6e0e5;border-radius:8px;padding:16px;margin:16px 0}}summary{{font-weight:600;cursor:pointer}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}
.pass{{color:#11613d}}.partial{{color:#8a5000}}.miss{{color:#a12626}}.metrics{{font-size:20px;font-weight:600}}.scroll{{overflow-x:auto}}</style><main>
<h1>Jev: fresh {summary['scheduled']}-case test</h1>
<p class="metrics">{summary['pass']}/{summary['scheduled']} full passes · {summary['partial']} partial · {summary['miss']} misses · {summary['pending']} pending reviews</p>
<p>{summary['recorded']}/{summary['scheduled']} recorded; {summary['execution_errors']} execution errors. Estimated API cost: <strong>${summary['new_cost_usd']:.4f}</strong>.
Median lookup: {summary['observed_latency_median_seconds'] or 0:.1f}s; p95: {summary['observed_latency_p95_seconds'] or 0:.1f}s.
{summary['calls']} model calls; {summary['reused_calls']} inference replays; {summary['source_requests']} catalog HTTP attempts.</p>
<p>Seed {manifest['sampling']['seed']}; one question per base case, excluding all {len(manifest['sampling']['excluded_base_cases'])} previously used base cases. Lookup code matches stage 13. Fresh model calls, with normal provider prefix caching. Existing source snapshots may be reused. No tuning during the run.</p>
<p>These are historical benchmark cases with overlapping machines and guides, not new-machine production validation. Full passes are agent reviews against source evidence and frozen references; partials are excluded from the pass rate. Source-field discrepancies: {summary['source_field_error_count']}. Number presence alone does not establish semantic correctness or serial-specific fitment.</p>
<p><a href="score.json">Full score JSON</a> · <a href="manifest.json">Frozen sample and configuration</a> · <a href="../report.html">Earlier stages: changes, reasons and outcomes</a></p>
<div class="scroll"><table><tr><th>Case and question</th><th>Returned numbers</th><th>Review</th><th>Guides</th><th>API estimate</th></tr>{''.join(rows)}</table></div>
<h2>Navigation and evidence</h2><p>Open a case to inspect the choices, source HTML, answer and review.</p>{''.join(details)}</main></html>'''
    (folder / "report.html").write_text(page, encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("reports/jev-iterations-20261105"))
    parser.add_argument("--stage", default="14-fresh40-20261106")
    args = parser.parse_args()
    report(args.root, args.stage)
