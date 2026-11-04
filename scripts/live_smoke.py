"""Explicit live CASE CE catalog smoke: cold <= 3 requests, warm zero."""

import argparse
import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from cnh_parts_agent import open_catalog
from cnh_parts_agent.settings import Settings


async def run(output):
    with tempfile.TemporaryDirectory(prefix="cnh-catalog-smoke-") as directory:
        settings = Settings(cache_path=Path(directory) / "catalog.sqlite")
        async with open_catalog(settings) as catalog:

            async def lookup():
                discovery = await catalog.execute("search_machines", {"query": "CX350B", "brand": "casece"})
                machine = discovery["data"]["machines"][0]["machine_ref"]
                search = await catalog.execute(
                    "search_catalog", {"machine_ref": machine, "query": "radiator", "mode": "assembly_title"}
                )
                matches = [
                    r
                    for r in search["data"]["matches"]
                    if r["assembly_label"].split(" - ")[-1].upper() == "RADIATOR"
                ]
                if len(matches) != 1:
                    raise ValueError("Live smoke expected exactly one radiator service assembly")
                return await catalog.execute(
                    "get_assembly", {"machine_ref": machine, "assembly_ref": matches[0]["assembly_ref"]}
                )

            result = {"created_at": datetime.now(UTC).isoformat()}
            try:
                assembly = await lookup()
                cold = catalog.transport.request_count
                await lookup()
                warm = catalog.transport.request_count - cold
                numbers = {r["part_number"] for r in assembly["data"]["occurrences"]}
                assert "KSH1028" in numbers
                assert cold <= 3 and warm == 0
                result.update(status="passed", cold_requests=cold, warm_requests=warm, evidence=assembly)
            except Exception as exc:
                result.update(
                    status="failed",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    requests=catalog.transport.request_count,
                )
                raise
            finally:
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("x", encoding="utf-8") as stream:
                    json.dump(result, stream, indent=2)
                print(json.dumps({k: v for k, v in result.items() if k != "evidence"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    asyncio.run(run(args.out))


if __name__ == "__main__":
    main()
