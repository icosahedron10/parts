"""Freeze new benchmark families and run the unchanged Jev lookup implementation."""

import argparse
import asyncio
import json
import random
import shutil
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.discovery.index import load_indexes
from cnh_parts_agent.discovery.match import match
from scripts.jev_iterate import CODE_PATHS, DEFAULT_ROOT, digest, dump, load, run


def draw_queries(queries, excluded, count, seed):
    families = {}
    for query in queries:
        family = query["id"].split(".")[0]
        if family not in excluded:
            families.setdefault(family, []).append(query)
    if not 0 < count <= len(families):
        raise ValueError(f"Requested {count} distinct cases but only {len(families)} unused families remain")
    rng = random.Random(seed)
    selected = rng.sample(sorted(families), count)
    return [rng.choice(families[family]) for family in selected]


def prepare(root, stage, count, seed):
    folder = root / stage
    if folder.exists():
        raise FileExistsError("Sample already frozen; use run to resume, or choose a new stage")
    excluded = {c["id"].split(".")[0] for p in root.glob("*/manifest.json")
                for c in load(p)["cases"] if c["id"].startswith("Q")}
    query_path = Path("evals/benchmark/queries.jsonl")
    lineage_path = Path("evals/benchmark/lineage.jsonl")
    queries = [json.loads(line) for line in query_path.read_text(encoding="utf-8").splitlines()]
    lineage = {r["id"]: r["machine"] for line in lineage_path.read_text(encoding="utf-8").splitlines()
               if (r := json.loads(line))}
    selected = draw_queries(queries, excluded, count, seed)
    indexes = load_indexes()
    cases = []
    for query in selected:
        machine = lineage[query["id"]]
        machine_query = f"{machine['make']} {machine['model']} {machine['model_year']}"
        candidates = [(brand, row) for brand, index in indexes.items()
                      for row in match(index["prepared"], machine_query)]
        if len(candidates) != 1:
            raise ValueError(f"Case {query['id']} did not resolve to exactly one machine")
        brand, row = candidates[0]
        cases.append({**query, "brand": brand, "machine_query": machine_query,
                      "machine": expand_references(row["machine"], indexes[brand]["source_id"])})
    previous = load(root / "13-final-regression" / "manifest.json")
    current_hashes = {str(p): digest(p.read_bytes()) for p in CODE_PATHS}
    if current_hashes != previous["code_sha256"]:
        raise ValueError("Lookup implementation changed since the final regression")
    config = {**previous["config"], "fresh_inference": True}
    manifest = {
        "stage": stage, "created_at": datetime.now(UTC).isoformat(), "config": config,
        "cases": cases, "models": previous["models"], "pricing": previous["pricing"],
        "code_sha256": current_hashes, "sampler_sha256": digest(Path(__file__).read_bytes()),
        "input_sha256": {str(p): digest(p.read_bytes()) for p in (query_path, lineage_path)},
        "sampling": {"seed": seed, "count": count, "excluded_base_cases": sorted(excluded),
                     "scope": "New base cases for this Jev experiment; historical benchmark with overlapping machines and guides"},
        "source_snapshot_policy": previous["source_snapshot_policy"],
        "schedule": previous["schedule"],
        "evaluation_policy": "One frozen run, all cases retained in denominator, no answer keys in inference, no tuning during test",
    }
    dump(folder / "manifest.json", manifest)
    for path in [*CODE_PATHS, Path(__file__)]:
        target = folder / "code" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    print(json.dumps({"stage": stage, "cases": count, "excluded_families": len(excluded),
                      "seed": seed, "fresh_inference": True, "lookup_code_unchanged": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "run"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--stage", default="14-fresh40-20261106")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20261106)
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.root, args.stage, args.count, args.seed)
    else:
        load_dotenv(".env")
        asyncio.run(run(args.root, args.stage))
