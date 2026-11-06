"""Offline paired scoring of ablation runs. Never imported by inference.

Number recovery, exact reference match and semantic review are reported separately. A semantic verdict is
inherited only for an identical (case, confirmed parts, unconfirmed leads) signature; anything new is pending.
Holdout answer keys are loaded only for sets that explicitly request them.
"""

import argparse
import json
import re
import statistics
from pathlib import Path

from scripts.jev_pilot import load
from scripts.score_jev_iterations import cost

ROOT = Path("reports/jev-adversarial-20261106")
PRIOR = Path("reports/jev-iterations-20261105")
GENERIC_QUESTION = re.compile(r"serial|build date|built on|production date|applicab|\bvariant\b|\bPIN\b", re.I)
DISCRIMINATING = re.compile(r"\bwhich\b|\bor\b.*\b(?:reference|left|right|front|rear|upper|lower)\b|\bleft\b|\bright\b|\bposition\b", re.I)


def signature(case_id, answer):
    return "|".join([case_id, ",".join(sorted({f["part_number"] for f in answer.get("findings", [])})),
                     ",".join(sorted({f["part_number"] for f in answer.get("possible_candidates", [])}))])


def prior_reviews():
    verdicts = {}
    for path in PRIOR.glob("*/reviews.json"):
        reviews = load(path)
        for case_id, review in reviews.items():
            result_path = path.parent / "cases" / case_id / "result.json"
            if result_path.exists():
                verdicts[signature(case_id, load(result_path).get("answer", {}))] = review
    extra = ROOT / "reviews-by-signature.json"
    if extra.exists():
        verdicts.update(load(extra))
    return verdicts


def needless_questions(answer):
    """A question is needless when it is generic fitment boilerplate with no choice among returned rows."""
    questions = answer.get("unresolved_questions", [])
    return [q for q in questions if GENERIC_QUESTION.search(q) and not DISCRIMINATING.search(q)]


def score_run(stage, gold, verdicts, expectations):
    folder = ROOT / "runs" / stage
    manifest = load(folder / "manifest.json")
    cases = []
    for case in manifest["cases"]:
        path = folder / "cases" / case["id"] / "result.json"
        result = load(path) if path.exists() else {"status": "missing"}
        answer = result.get("answer", {})
        confirmed = {f["part_number"] for f in answer.get("findings", [])}
        leads = {f["part_number"] for f in answer.get("possible_candidates", [])}
        expected = gold.get(case["id"])
        calls = result.get("calls", [])
        tokens = {"reader_input": sum(c["usage"]["input_tokens"] for c in calls if c["kind"] != "jev"),
                  "reader_output": sum(c["usage"].get("output_tokens", 0) or 0 for c in calls if c["kind"] != "jev"),
                  "jev_input": sum(c["usage"]["input_tokens"] for c in calls if c["kind"] == "jev")}
        sig = signature(case["id"], answer)
        row = {
            "id": case["id"], "status": result["status"], "stop": result.get("stop_reason"), "signature": sig,
            "confirmed": sorted(confirmed), "leads": sorted(leads),
            "exact": expected == confirmed if expected is not None else None,
            "recovered": expected <= confirmed if expected is not None else None,
            "recovered_with_leads": expected <= (confirmed | leads) if expected is not None else None,
            "extra": sorted(confirmed - expected) if expected is not None else [],
            "missing": sorted(expected - confirmed) if expected is not None else [],
            "questions": answer.get("unresolved_questions", []), "needless_questions": needless_questions(answer),
            "guides": len(result.get("inspections", [])), "calls": len(calls),
            "kinds": {k: sum(c["kind"] == k for c in calls) for k in {c["kind"] for c in calls}},
            "fresh_calls": sum(not c["reused"] for c in calls), "tokens": tokens,
            "logical_cost": sum(cost(c) for c in calls), "new_cost": sum(cost(c) for c in calls if not c["reused"]),
            "model_seconds": sum(c["elapsed_seconds"] for c in calls),
            "latency": result.get("latency_seconds"), "error": result.get("error"),
            "conditional": [f["part_number"] for f in answer.get("conditional_alternatives", [])],
        }
        review = verdicts.get(sig)
        row["verdict"] = review["verdict"] if review else "pending"
        row["review_note"] = review.get("note") if review else None
        if case["id"] in expectations:
            expectation = expectations[case["id"]]
            row["adversarial"] = adversarial_verdict(expectation, confirmed, leads | set(row["conditional"]))
            row["adversarial_kind"] = expectation.get("kind")
            if "expects_question" in expectation:
                row["question_as_expected"] = bool(row["questions"]) == expectation["expects_question"]
        cases.append(row)
    def total(key):
        return sum(c[key] for c in cases if isinstance(c[key], int | float))
    seconds = sorted(c["model_seconds"] for c in cases)
    summary = {
        "stage": stage, "variant": manifest["variant"], "cases": len(cases), "fresh": manifest["fresh_inference"],
        "errors": sum(c["status"] != "completed" for c in cases),
        "pass": sum(c["verdict"] == "pass" for c in cases), "partial": sum(c["verdict"] == "partial" for c in cases),
        "miss": sum(c["verdict"] == "miss" for c in cases), "pending": sum(c["verdict"] == "pending" for c in cases),
        "exact_reference": sum(c["exact"] is True for c in cases),
        "recovered_reference": sum(c["recovered"] is True for c in cases),
        "cases_with_extra": sum(bool(c["extra"]) for c in cases),
        "cases_with_question": sum(bool(c["questions"]) for c in cases),
        "cases_only_needless_questions": sum(bool(c["questions"]) and len(c["needless_questions"]) == len(c["questions"])
                                             for c in cases),
        "guides": total("guides"), "calls": total("calls"), "fresh_calls": total("fresh_calls"),
        "reader_input": sum(c["tokens"]["reader_input"] for c in cases),
        "reader_output": sum(c["tokens"]["reader_output"] for c in cases),
        "jev_input": sum(c["tokens"]["jev_input"] for c in cases),
        "logical_cost": round(total("logical_cost"), 4), "new_cost": round(total("new_cost"), 4),
        "model_seconds_median": round(statistics.median(seconds), 1) if seconds else None,
        "model_seconds_p95": round(seconds[max(0, int(len(seconds) * .95 + .999) - 1)], 1) if seconds else None,
    }
    adversarial = [c["adversarial"] for c in cases if "adversarial" in c]
    if adversarial:
        summary["adversarial"] = {k: adversarial.count(k) for k in sorted(set(adversarial))}
    report = {"summary": summary, "cases": cases}
    (folder / "score.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def load_expectations():
    """Frozen expectations plus visibly separate, post-control amendments that only widen correct answers."""
    expectations = {}
    for path in (ROOT / "sets").glob("*.expected.json"):
        expectations.update(load(path))
    amendments = ROOT / "sets" / "adversarial.amendments.json"
    if amendments.exists():
        for case_id, amendment in load(amendments).items():
            if case_id in expectations:
                expectations[case_id] = {**expectations[case_id], "also_acceptable": amendment["also_acceptable"]}
    return expectations


def adversarial_verdict(expect, confirmed, leads):
    """Compare with behaviour frozen before any variant ran."""
    forbidden = set(expect.get("forbidden_confirmed", []))
    required = set(expect.get("required_confirmed", []))
    any_of = set(expect.get("any_lead_or_confirmed", []))
    if confirmed & set(expect.get("also_acceptable", [])):
        required, any_of = set(), set()
    if confirmed & forbidden:
        return "false_positive"
    if required - confirmed:
        return "false_negative" if not (required - confirmed) & leads else "under_confident"
    if any_of and not any_of & (confirmed | leads):
        return "false_negative"
    if expect.get("expect_none") and (confirmed or leads):
        return "overclaim" if confirmed else "unneeded_lead"
    return "ok"


def load_gold(stages):
    wanted = {c["id"] for s in stages for c in load(ROOT / "runs" / s / "manifest.json")["cases"]}
    gold = {}
    with Path("evals/benchmark/answers.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["id"] in wanted:
                gold[row["id"]] = {p["part_number"] for p in row["answer"]["parts"]}
    for case_id, row in load(ROOT / "sets" / "farmall.reference.json").items():
        if case_id in wanted:
            gold[case_id] = {p["part_number"] for p in row["answer"]["parts"]}
    return gold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stages", nargs="+")
    parser.add_argument("--control")
    parser.add_argument("--show-diff", action="store_true")
    args = parser.parse_args()
    holdout = set(load(ROOT / "holdout" / "manifest.json")["holdout_families"])
    stages = args.stages + ([args.control] if args.control else [])
    if any(c["id"].split(".")[0] in holdout for s in stages for c in load(ROOT / "runs" / s / "manifest.json")["cases"]) \
            and not (ROOT / "holdout" / "candidate-frozen.json").exists():
        raise SystemExit("Holdout scoring is locked until the candidate implementation is frozen")
    gold = load_gold(stages)
    expectations = load_expectations()
    verdicts = prior_reviews()
    control = score_run(args.control, gold, verdicts, expectations) if args.control else None
    base = {c["id"]: c for c in control["cases"]} if control else {}
    for stage in args.stages:
        report = score_run(stage, gold, verdicts, expectations)
        print(json.dumps(report["summary"]))
        if args.show_diff:
            for case in report["cases"]:
                other = base.get(case["id"])
                if other is None or other["signature"] != case["signature"] or case["verdict"] == "pending":
                    print("  DIFF", case["id"], "now", case["confirmed"], "leads", case["leads"], case["verdict"], case.get("adversarial"),
                          "| ctrl", other and other["confirmed"], other and other["leads"], other and other["verdict"],
                          "| guides", case["guides"], other and other["guides"], case["stop"], case["error"] or "")
    if control:
        print(json.dumps(control["summary"]))


if __name__ == "__main__":
    main()
