"""Small title-only routing experiment using cached, incomplete catalog contents.

Run: uv run python -m scripts.test_toc_selection --out reports/toc-selection.json
No CNH requests. Uses the configured model once per request. This is a diagnostic,
not a clean holdout: the cache and target assemblies come from previous lookups.
"""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from cnh_parts_agent.agent.model import create_parts_model
from cnh_parts_agent.settings import Settings


class Selection(BaseModel):
    assembly_ids: list[str] = Field(max_length=3)
    reason: str


# Frozen before inference. These are routing targets, not fitment judgments.
TARGETS = {
    "H003": {"368E796F-8FBF-E111-9FCE-005056875BD6", "6F27767B-8FBF-E111-9FCE-005056875BD6"},
    "H004": {"EA678B80-8FBF-E111-9FCE-005056875BD6"},
    "H010": {"9745315A-8FBF-E111-9FCE-005056875BD6"},
    "H018": {"7D297A75-8FBF-E111-9FCE-005056875BD6", "C047767B-8FBF-E111-9FCE-005056875BD6"},
    "H020": {"4D9B6E81-8FBF-E111-9FCE-005056875BD6"},
    "H027": {"7EF50978-8FBF-E111-9FCE-005056875BD6"},
}
PROMPT = (
    "Choose up to three assemblies to inspect for the user's requested part, in priority order. "
    "You have the machine name and a list of its catalog assembly titles. "
    "Use your understanding of the part's function and where it belongs. "
    "Return only IDs from the supplied list and a brief reason. "
    "An assembly choice is a place to look, not proof that the part is present."
)


async def run(output):
    if output.exists():
        raise FileExistsError(output)
    settings = Settings()
    titles = {}
    with sqlite3.connect(f"{settings.cache_path.resolve().as_uri()}?mode=ro", uri=True) as db:
        for (value,) in db.execute("SELECT value FROM results ORDER BY key"):
            for row in json.loads(value)["data"].get("matches", []):
                ref = row.get("assembly_ref")
                if ref:
                    ref = ref.get("$ref", ref)
                    label = row.get("assembly_label") or row.get("label")
                    if label:
                        titles.setdefault(ref["context"], {})[ref["id"]] = label
    cases = [
        row for line in Path("reports/holdout-2026-11-03-v2.jsonl").read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line)).get("id") in TARGETS
    ]
    report = {
        "scope": "Incomplete cached title lists, query-biased development cases; not a full TOC benchmark",
        "model": settings.model,
        "prompt": PROMPT,
        "cnh_requests": 0,
        "cases": [],
    }
    model = create_parts_model().with_structured_output(Selection, method="json_schema", include_raw=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        machine = next(
            e["arguments"]["machine_ref"] for e in case["evidence"] if "machine_ref" in e["arguments"]
        )
        catalog = titles[machine["id"]]
        if not TARGETS[case["id"]] <= catalog.keys():
            raise ValueError(f"Missing target in cached titles for {case['id']}")
        entries = sorted(catalog.items(), key=lambda item: (item[1], item[0]))
        # Opaque short IDs avoid spending tokens on source UUIDs.
        handles = {str(i): assembly for i, (assembly, _) in enumerate(entries, 1)}
        request = {
            "machine": case["lookup"]["machine_query"],
            "part": case["lookup"]["part_query"],
            "assemblies": [{"id": str(i), "title": label} for i, (_, label) in enumerate(entries, 1)],
        }
        response = await model.ainvoke([("system", PROMPT), ("human", json.dumps(request))])
        if response["parsing_error"]:
            raise response["parsing_error"]
        selected = response["parsed"]
        if len(set(selected.assembly_ids)) != len(selected.assembly_ids):
            raise ValueError("Duplicate assembly selection")
        chosen = [handles[handle] for handle in selected.assembly_ids]
        result = {
            "id": case["id"], "input": request, "title_count": len(catalog),
            "selected": [{"id": a, "title": catalog[a]} for a in chosen],
            "reason": selected.reason,
            "targets": [{"id": a, "title": catalog[a]} for a in sorted(TARGETS[case["id"]])],
            "top1": bool(chosen and chosen[0] in TARGETS[case["id"]]),
            "top3": bool(set(chosen) & TARGETS[case["id"]]),
            "usage": response["raw"].usage_metadata,
        }
        report["cases"].append(result)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: result[k] for k in ("id", "title_count", "selected", "top1", "top3")}), flush=True)
    report["summary"] = {
        "cases": len(cases),
        "top1": sum(c["top1"] for c in report["cases"]),
        "top3": sum(c["top3"] for c in report["cases"]),
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    asyncio.run(run(args.out))
