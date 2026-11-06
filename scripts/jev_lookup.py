"""Ask the Jev catalog lookup one question and print its answer as JSON.

Needs JEV_API_KEY and OPENAI_API_KEY. Makes live, paced catalog requests and paid model calls.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.discovery.index import load_indexes
from cnh_parts_agent.discovery.match import match
from cnh_parts_agent.settings import Settings
from scripts.jev_iterate import evaluate_case

CONFIG = {"max_groups": 4, "max_guides": 6, "max_actions": 20, "timeout_seconds": 240, "fresh_inference": True}


def resolve_machine(machine_query, label=None):
    """Return the one catalog this wording matches, or the labels the user has to choose between."""
    indexes = load_indexes()
    found = [(brand, row) for brand, index in indexes.items() for row in match(index["prepared"], machine_query)]
    if label:
        found = [(brand, row) for brand, row in found if row["machine"]["label"] == label]
    if len(found) != 1:
        return None, [row["machine"]["label"] for _, row in found]
    brand, row = found[0]
    return (brand, expand_references(row["machine"], indexes[brand]["source_id"])), []


def report(result):
    answer = result["answer"]
    fields = ("part_number", "catalog_description", "diagram_reference", "quantity", "notes", "match_basis",
              "source_url", "guide_label")
    def rows(key):
        return [{f: row.get(f) for f in fields} for row in answer.get(key, [])]
    return {"status": answer["status"], "confirmed": rows("findings"), "unconfirmed_leads": rows("possible_candidates"),
            "conditional_alternatives": rows("conditional_alternatives"), "explanation": answer["explanation"],
            "questions": answer["unresolved_questions"], "source_limitations": answer.get("source_limitations", []),
            "guides_inspected": [page["guide"]["label"] for page in result["inspections"]],
            "seconds": round(result["latency_seconds"], 1)}


async def lookup(case, work):
    settings = Settings()
    transport = SourceTransport(settings.user_agent, settings=settings)
    try:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + os.environ["JEV_API_KEY"]},
                                     timeout=45, follow_redirects=False) as jev:
            async with AsyncOpenAI(max_retries=0, timeout=90) as reader:
                return await evaluate_case(case, work / "run", work, CONFIG, transport, asyncio.Lock(), jev, reader)
    finally:
        await transport.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True, help='For example "CASE 580N 2011"; a trailing year scopes date rows')
    parser.add_argument("--part", required=True, help="The request in the user's own words")
    parser.add_argument("--catalog-label", help="Exact catalog label, when the machine wording matched several")
    parser.add_argument("--keep-trace", type=Path, help="Keep requests, responses and source pages in this new folder")
    args = parser.parse_args()
    load_dotenv(".env")
    resolved, choices = resolve_machine(args.machine, args.catalog_label)
    if not resolved:
        print(json.dumps({"status": "machine_not_resolved", "matching_catalog_labels": choices}, indent=2, ensure_ascii=False))
        sys.exit(2)
    brand, machine = resolved
    case = {"id": f"lookup-{int(time.time())}", "query": args.part, "brand": brand,
            "machine_query": args.machine, "machine": machine}
    if args.keep_trace:
        args.keep_trace.mkdir(parents=True, exist_ok=False)
        result = asyncio.run(lookup(case, args.keep_trace))
    else:
        with tempfile.TemporaryDirectory() as folder:
            result = asyncio.run(lookup(case, Path(folder)))
    print(json.dumps(report(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
