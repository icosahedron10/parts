"""Summarize a complete direct-specialist run without treating evidence checks as accuracy."""

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from cnh_parts_agent.agent.handoff import PartsHandoff
from evals.evidence import evidence_gate
from evals.io import read_jsonl


def summarize(report, cases, rubric, review=None):
    queries, query_hash = read_jsonl(cases)
    criteria, rubric_hash = read_jsonl(rubric)
    raw = report.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    header, *results = records
    if header.get("record_type") != "run" or header.get("target") != "specialist":
        raise ValueError("Expected a direct-specialist run")
    if header["queries_sha256"] != query_hash or header["scheduled_cases"] != queries:
        raise ValueError("Run does not match the complete frozen schedule")
    ids = [query["id"] for query in queries]
    if [row.get("id") for row in results] != ids:
        raise ValueError("Report must contain every scheduled case exactly once, in order")
    if [row["id"] for row in criteria] != ids:
        raise ValueError("Rubric does not match schedule")
    reviews = None
    review_hash = None
    if review:
        reviews, review_hash = read_jsonl(review)
        if [row["id"] for row in reviews] != ids:
            raise ValueError("Review must cover the entire schedule in order")
        for row in reviews:
            if row.get("verdict") not in {"pass", "partial", "fail", "blocked"} or not row.get("reason"):
                raise ValueError("Each review needs a verdict and an evidence-based reason")
    summaries = []
    for index, (result, criterion) in enumerate(zip(results, criteria, strict=True)):
        observations = result["evidence"]
        gate = None
        handoff = None
        if result["status"] == "received":
            handoff = PartsHandoff.model_validate_json(result["response_text"])
            answer = "\n".join(handoff.key_facts + handoff.sources + [handoff.store_url])
            gate = evidence_gate(answer, observations)
        tool_errors = [
            {
                "observation_index": i,
                "operation": observation["operation"],
                "error": observation["response"]["error"],
            }
            for i, observation in enumerate(observations)
            if "error" in observation.get("response", {})
        ]
        summary = {
            "id": result["id"],
            "category": criterion["category"],
            "status": result["status"],
            "error": result.get("error"),
            "latency_seconds": result["latency_seconds"],
            "tool_calls": sum(not o.get("host_action", False) for o in observations),
            "tool_errors": tool_errors,
            "assembly_inspections": sum(o["operation"] == "get_assembly" for o in observations),
            "source_requests": len(result["diagnostics"]["requests"]),
            "evidence_gate": gate,
            "has_product_selection": bool(handoff and handoff.store_url),
        }
        if reviews:
            summary["review"] = reviews[index]
        summaries.append(summary)
    latencies = sorted(row["latency_seconds"] for row in summaries)
    usage = Counter()
    for row in results:
        for call in row["diagnostics"]["model_usage"]:
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                usage[key] += call.get(key, 0)
    return {
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "queries_sha256": query_hash,
        "rubric_sha256": rubric_hash,
        "review_sha256": review_hash,
        "target": "specialist",
        "model": header["model"],
        "count": len(summaries),
        "statuses": dict(Counter(row["status"] for row in summaries)),
        "mechanical_evidence_passes": sum(
            bool(row["evidence_gate"] and row["evidence_gate"]["passed"]) for row in summaries
        ),
        "cases_with_tool_errors": sum(bool(row["tool_errors"]) for row in summaries),
        "cases_with_assembly_inspection": sum(row["assembly_inspections"] > 0 for row in summaries),
        "source_requests": sum(row["source_requests"] for row in summaries),
        "latency_seconds": {
            "median": statistics.median(latencies),
            "p95": latencies[max(0, (95 * len(latencies) + 99) // 100 - 1)],
            "maximum": max(latencies),
            "total": sum(latencies),
        },
        "model_usage": dict(usage),
        "review_verdicts": dict(Counter(row["verdict"] for row in reviews)) if reviews else None,
        "limitations": [
            "Mechanical evidence passes do not establish semantic correctness or complete retrieval.",
            "Semantic review is implementation-agent review of saved evidence, not independent fitment approval.",
            "No gold part numbers; unsuccessful and interrupted cases remain in the denominator.",
            "New queries are not guaranteed model/catalog-disjoint; shared cache is retained.",
        ],
        "cases": summaries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=Path("evals/holdout/queries.jsonl"))
    parser.add_argument("--rubric", type=Path, default=Path("evals/holdout/rubric.jsonl"))
    parser.add_argument("--review", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(args.report, args.cases, args.rubric, args.review)
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()
