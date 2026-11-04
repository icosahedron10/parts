"""Single-pair live CLI; deterministic tests use injected models."""

import argparse
import asyncio

from dotenv import load_dotenv

from . import create_parts_model, make_lookup_parts_tool, open_catalog


async def run(args):
    async with open_catalog() as catalog:
        tool = make_lookup_parts_tool(catalog=catalog, model=create_parts_model(args.model))
        print(
            await tool.ainvoke(
                {
                    "machine_query": args.machine,
                    "part_query": args.part,
                    "selected_machine_label": args.selected_machine_label,
                    "brand": args.brand,
                }
            )
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--part", required=True)
    parser.add_argument("--brand", choices=["casece", "caseih"])
    parser.add_argument("--selected-machine-label")
    parser.add_argument("--model")
    args = parser.parse_args()
    load_dotenv()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
