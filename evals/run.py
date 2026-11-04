"""Durable in-process evaluation. Gold is never loaded by the inference runner."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from cnh_parts_agent import create_parts_model, open_catalog
from cnh_parts_agent.diagnostics import Diagnostics, current
from cnh_parts_agent.settings import Settings

from .harness import run_query
from .io import read_jsonl


def source_fingerprint():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    paths = [
        *root.joinpath("src").rglob("*.py"),
        *root.joinpath("evals").rglob("*.py"),
        root / "uv.lock",
        root / "pyproject.toml",
    ]
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def run(cases, output, *, live=False, model_name=None, limit=None, execute=None):
    queries, query_hash = read_jsonl(cases)
    if any(
        set(q) != {"id", "query"} or not isinstance(q["query"], str) or not q["query"].strip()
        for q in queries
    ):
        raise ValueError("Inference input must contain only id and a nonempty query")
    queries = queries[:limit] if limit else queries
    settings = Settings(model=model_name) if model_name else Settings()
    output.parent.mkdir(parents=True, exist_ok=True)
    errors = 0
    with output.open("x", encoding="utf-8", newline="\n") as stream:

        def write(row):
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

        async with open_catalog(settings) as catalog:
            write(
                {
                    "record_type": "run",
                    "created_at": datetime.now(UTC).isoformat(),
                    "scheduled_cases": queries,
                    "queries_sha256": query_hash,
                    "source_sha256": source_fingerprint(),
                    "model": settings.model,
                    "config": settings.model_dump(mode="json"),
                    "index_sha256": {brand: index["sha256"] for brand, index in catalog.indexes.items()},
                    "live": live,
                    "cache_policy": "shared, not cleared by runner",
                }
            )
            model = None
            stopped = False
            for case in queries:
                diagnostics = Diagnostics()
                token = current.set(diagnostics)
                started = time.monotonic()
                record = {
                    "record_type": "case",
                    "id": case["id"],
                    "query": case["query"],
                    "status": "not_run",
                    "response_status": None,
                    "response_text": "",
                    "evidence": [],
                }
                try:
                    if (live or execute is not None) and not stopped:
                        if execute is None and model is None:
                            # Constructor failures also get a durable case record and
                            # leave every subsequent scheduled case in the denominator.
                            stopped = True
                            model = create_parts_model(settings.model)
                            stopped = False
                        response = (
                            await execute(case["query"])
                            if execute
                            else await run_query(case["query"], catalog=catalog, model=model)
                        )
                        record.update(status="received", response_status="completed", response_text=response)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    stopped = True
                    errors += 1
                    record.update(status="cancelled", response_status="cancelled")
                except Exception as exc:
                    errors += 1
                    # Persist a failed case, then return a nonzero run status; no repaired answer.
                    message = str(exc)
                    secret = os.getenv("OPENAI_API_KEY")
                    if secret:
                        message = message.replace(secret, "[redacted]")
                    record.update(
                        status="error",
                        response_status="error",
                        error={"type": type(exc).__name__, "message": message},
                    )
                    # Configuration/authentication failures must not spend 400 failed calls.
                    if any(
                        name in type(exc).__name__
                        for name in ("AuthenticationError", "NotFoundError", "PermissionDeniedError")
                    ):
                        stopped = True
                finally:
                    current.reset(token)
                    record.update(
                        latency_seconds=round(time.monotonic() - started, 4),
                        evidence=diagnostics.observations,
                        diagnostics=asdict(diagnostics),
                    )
                    hits = sum(s in {"hit", "coalesced"} for s in diagnostics.cache)
                    record["cache_hit_rate"] = hits / len(diagnostics.cache) if diagnostics.cache else None
                    write(record)
                if live:
                    print(f"{case['id']}: {record['status']}", flush=True)
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/benchmark/queries.jsonl"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    load_dotenv()
    raise SystemExit(
        asyncio.run(run(args.cases, args.out, live=args.live, model_name=args.model, limit=args.limit))
    )


if __name__ == "__main__":
    main()
