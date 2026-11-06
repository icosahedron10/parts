"""Freeze the last unused benchmark families as a holdout; never reads the answer file."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.discovery.index import load_indexes
from cnh_parts_agent.discovery.match import match
from scripts.jev_fresh_sample import draw_queries
from scripts.jev_iterate import CODE_PATHS, digest, dump, load

PRIOR_ROOTS = [Path("reports/jev-iterations-20261105"), Path("reports/jev-pilot-20-20261105"),
               Path("reports/jev-pilot-20-20261105-v2")]
ROOT = Path("reports/jev-adversarial-20261106")
SEED = 20261108


def main():
    folder = ROOT / "holdout"
    if (folder / "manifest.json").exists():
        raise FileExistsError("Holdout already frozen")
    excluded = {c["id"].split(".")[0] for root in PRIOR_ROOTS for p in root.rglob("manifest.json")
                for c in load(p).get("cases", []) if c["id"].startswith("Q")}
    query_path = Path("evals/benchmark/queries.jsonl")
    lineage_path = Path("evals/benchmark/lineage.jsonl")
    queries = [json.loads(line) for line in query_path.read_text(encoding="utf-8").splitlines()]
    lineage = {r["id"]: r["machine"] for line in lineage_path.read_text(encoding="utf-8").splitlines()
               if (r := json.loads(line))}
    families = {q["id"].split(".")[0] for q in queries}
    remaining = families - excluded
    if len(remaining) != 20:
        raise ValueError(f"Expected 20 unused families, found {len(remaining)}")
    indexes = load_indexes()
    cases, unresolved = [], []
    for query in draw_queries(queries, excluded, 20, SEED):
        machine = lineage[query["id"]]
        machine_query = f"{machine['make']} {machine['model']} {machine['model_year']}"
        candidates = [(brand, row) for brand, index in indexes.items()
                      for row in match(index["prepared"], machine_query)]
        if len(candidates) != 1:
            unresolved.append({"id": query["id"], "resolved_count": len(candidates)})
            continue
        brand, row = candidates[0]
        cases.append({**query, "brand": brand, "machine_query": machine_query,
                      "machine": expand_references(row["machine"], indexes[brand]["source_id"])})
    manifest = {
        "created_at": datetime.now(UTC).isoformat(), "seed": SEED, "cases": cases, "unresolved": unresolved,
        "excluded_base_cases": sorted(excluded), "holdout_families": sorted(remaining),
        "input_sha256": {str(p): digest(p.read_bytes()) for p in (query_path, lineage_path)},
        "code_sha256_at_freeze": {str(p): digest(p.read_bytes()) for p in CODE_PATHS},
        "policy": "Inputs frozen before any tuning. Answer keys are excluded from inference and results stay "
                  "unread until the candidate implementation is frozen. Once a result is inspected or used "
                  "for a fix, later runs on these cases are regression tests, not fresh validation.",
    }
    dump(folder / "manifest.json", manifest)
    code = ROOT / "baseline-code"
    code.mkdir(parents=True, exist_ok=True)
    for path in [*CODE_PATHS, Path(__file__), Path("scripts/jev_fresh_sample.py")]:
        shutil.copyfile(path, code / path.name)
    print(json.dumps({"frozen": len(cases), "unresolved": unresolved,
                      "families": sorted(remaining), "excluded": len(excluded)}))


if __name__ == "__main__":
    main()
