"""Frozen case sets and immutable run folders for the current Jev lookup.

Every ablation variant, and the flagged code that ran it, is preserved under
reports/jev-adversarial-20261106 (runs/*/code and flagged-code/). This script now runs only the chosen lookup.
Replay is exact-request only, and every reused response is marked. Inference never reads answers.
"""

import argparse
import asyncio
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.settings import Settings
from scripts.jev_iterate import CODE_PATHS, digest, dump, evaluate_case, load

ROOT = Path("reports/jev-adversarial-20261106")
PRICING = {"currency": "USD", "basis": "Published standard rates when measured; estimates, not invoices",
           "per_million_tokens": {"jev_input": 0.042, "jev_output": 0, "reader_input": 1.0, "reader_cached_input": 0.05,
                                  "reader_30m_cache_write": 1.25, "reader_output": 5.0}}
CONFIG = {"max_groups": 4, "max_guides": 6, "max_actions": 20, "timeout_seconds": 240}


def prepare(label, set_name, fresh):
    stage = f"{label}__{set_name}" + ("__fresh" if fresh else "")
    folder = ROOT / "runs" / stage
    if folder.exists():
        raise FileExistsError(f"{stage} is frozen")
    manifest = {"stage": stage, "variant": label, "case_set": set_name, "created_at": datetime.now(UTC).isoformat(),
                "config": {**CONFIG, "fresh_inference": fresh}, "cases": load(ROOT / "sets" / f"{set_name}.json")["cases"],
                "models": {"navigation": "jev-1.14.0", "reader": "gpt-6.1-terra", "reasoning_effort": "high"},
                "pricing": PRICING,
                "code_sha256": {str(p): digest(p.read_bytes()) for p in CODE_PATHS},
                "fresh_inference": fresh,
                "replay_policy": "No replay" if fresh else "Exact request, schema and kind only; reused responses are marked."}
    dump(folder / "manifest.json", manifest)
    for path in CODE_PATHS:
        target = folder / "code" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return stage


async def run(stage, concurrency):
    folder = ROOT / "runs" / stage
    manifest = load(folder / "manifest.json")
    if manifest["code_sha256"] != {str(p): digest(p.read_bytes()) for p in CODE_PATHS}:
        raise ValueError("Code changed since this run was frozen; prepare a new label")
    settings = Settings()
    transport = SourceTransport(settings.user_agent, settings=settings)
    lock, semaphore = asyncio.Lock(), asyncio.Semaphore(concurrency)
    errors = []
    try:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + os.environ["JEV_API_KEY"]},
                                     timeout=45, follow_redirects=False) as jev:
            async with AsyncOpenAI(max_retries=0, timeout=90) as reader:
                async def execute(case):
                    async with semaphore:
                        try:
                            await evaluate_case(case, folder, ROOT, manifest["config"], transport, lock, jev, reader)
                        except Exception as exc:  # recorded in result.json; stays in the denominator
                            errors.append({"id": case["id"], "type": type(exc).__name__})
                await asyncio.gather(*(execute(case) for case in manifest["cases"]))
    finally:
        await transport.aclose()
    print(json.dumps({"stage": stage, "errors": errors}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--set", required=True)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    load_dotenv(".env")
    asyncio.run(run(prepare(args.label, args.set, args.fresh), args.concurrency))
