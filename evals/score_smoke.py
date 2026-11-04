"""Score retrieval references separately from clarification and scoped absence behavior."""

import argparse
import json
from pathlib import Path

from .benchmark.score import assess_case
from .evidence import evidence_gate
from .io import read_jsonl


def score(report):
    records = [json.loads(s) for s in report.read_text(encoding="utf-8").splitlines()]
    runs = [r for r in records if r.get("record_type") == "run"]
    cases = [r for r in records if r.get("record_type") == "case"]
    if len(runs) != 1 or [r["id"] for r in runs[0]["scheduled_cases"]] != [r["id"] for r in cases]:
        raise ValueError("Report cases must exactly match the complete schedule")
    gold = {r["id"]: r for r in read_jsonl(Path(__file__).with_name("smoke-answers.jsonl"))[0]}
    behaviors = json.loads(Path(__file__).with_name("smoke-behaviors.json").read_text())
    results = []
    for case in cases:
        if case["id"] in gold:
            assessment = assess_case(case, gold[case["id"]])
            passed = (
                case["status"] == "received"
                and bool(assessment["mentioned_reference_numbers"])
                and assessment["evidence_gate"]["passed"]
            )
        elif case["id"] in behaviors:
            diagnostics = case.get("diagnostics", {})
            handoffs = diagnostics.get("handoffs", [])
            observations = diagnostics.get("observations", [])
            handoff = handoffs[-1]["handoff"] if handoffs else {}
            discovery = next((o["response"] for o in observations if o["operation"] == "search_machines"), {})
            passed = case["status"] == "received" and bool(handoff) and handoff.get("store_url") == ""
            if behaviors[case["id"]] == "catalog_choice":
                passed = passed and discovery.get("total", 0) > 1 and len(observations) == 1
            else:
                negatives = [
                    o
                    for o in observations
                    if o["operation"] == "search_catalog" and o["response"].get("outcome") == "not_found"
                ]
                passed = (
                    passed
                    and bool(negatives)
                    and evidence_gate(case["response_text"], observations)["passed"]
                )
            assessment = {"behavior": behaviors[case["id"]]}
        else:
            raise ValueError("Unknown smoke case")
        results.append({"id": case["id"], "passed": bool(passed), "assessment": assessment})
    behavior_pass = all(r["passed"] for r in results if r["id"] in behaviors)
    count = sum(r["passed"] for r in results)
    return {
        "passed": count,
        "total": len(results),
        "behavior_checks_passed": behavior_pass,
        "gate_passed": len(results) == 20 and count >= 18 and behavior_pass,
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.report)
    with args.out.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}))
    raise SystemExit(0 if result["gate_passed"] else 1)


if __name__ == "__main__":
    main()
