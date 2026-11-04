"""Capture and parse an explicitly selected operation using the shared catalog code."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from cnh_parts_agent.catalog import operations, parse
from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.catalog.storefront import get_storefront
from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.discovery.index import load_indexes
from cnh_parts_agent.discovery.match import match
from cnh_parts_agent.settings import Settings

from .capture_fixture import save_fixture


async def run(args):
    storefront = get_storefront(args.brand)
    index = load_indexes()[args.brand]
    candidates = match(index["prepared"], args.machine)
    if len(candidates) != 1:
        raise ValueError("Capture requires exactly one catalog; qualify the machine query")
    machine = expand_references(candidates[0]["machine"]["machine_ref"], storefront.source_id)
    request = {"source_id": storefront.source_id, "machine_ref": machine}
    if args.operation == "search_catalog":
        request.update(query=args.query, mode=args.mode)
    elif args.operation == "get_assembly":
        path = urlsplit(parse.source_link(args.assembly_url, storefront=storefront)).path
        request["assembly_ref"] = {
            "source_id": storefront.source_id,
            "kind": "assembly",
            "id": path.rsplit("/", 2)[-2],
            "context": machine["id"],
            "path": path,
        }
    elif args.operation == "get_part_relationships":
        request["part_ref"] = {
            "source_id": storefront.source_id,
            "kind": "part",
            "id": args.part_number,
            "context": None,
            "path": None,
        }
    if args.parsed_output.exists() or (args.directory / args.name).exists():
        raise FileExistsError("Choose new observation paths")
    transport = SourceTransport(Settings().user_agent)

    async def fetch(url):
        response = await transport.fetch(url)
        save_fixture(args.directory, args.name, response)
        return response

    try:
        result = await operations.execute(args.operation, request, fetch, storefront=storefront)
    finally:
        await transport.aclose()
    observation = {"operation": args.operation, "arguments": request, "response": asdict(result)}
    args.parsed_output.parent.mkdir(parents=True, exist_ok=True)
    with args.parsed_output.open("x", encoding="utf-8") as output:
        json.dump(observation, output, indent=2, ensure_ascii=False)
    print(args.parsed_output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", choices=["casece", "caseih"], required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument(
        "--operation",
        choices=["list_groups", "search_catalog", "get_assembly", "get_part_relationships"],
        required=True,
    )
    parser.add_argument("--query")
    parser.add_argument(
        "--mode", default="assembly_title", choices=["assembly_title", "part_description", "part_number"]
    )
    parser.add_argument("--assembly-url")
    parser.add_argument("--part-number")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--parsed-output", type=Path, required=True)
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name == "manifest.json":
        parser.error("--name must be a fixture filename")
    if (
        args.operation == "search_catalog"
        and not args.query
        or args.operation == "get_assembly"
        and not args.assembly_url
        or args.operation == "get_part_relationships"
        and not args.part_number
    ):
        parser.error("The selected operation needs its corresponding query, assembly URL, or part number")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
