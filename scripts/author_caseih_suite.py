"""Handwritten Case IH questions; copy answer facts only from captured assembly rows."""

import hashlib
import json
from pathlib import Path

from cnh_parts_agent.catalog import parse
from cnh_parts_agent.catalog.storefront import CASEIH

# These questions and intended row identities are authored, not model-generated or inference inputs.
CASES = [
    ("tractor", "tractor-assembly", "A190663", "I need a radiator for my 1990 Case IH Magnum 7110 tractor."),
    (
        "tractor",
        "tractor-assembly",
        "A170241",
        "Which 15 psi radiator filler cap does my 1990 Case IH 7110 use?",
    ),
    (
        "tractor",
        "tractor-assembly",
        "1334477C1",
        "I need the lower radiator hose for my 1990 Case IH 7110, 58 mm inside diameter and 263 mm long.",
    ),
    (
        "tractor",
        "tractor-assembly",
        "1289072C1",
        "Find the upper radiator hose, 58 mm ID and 344 mm long, for a 1990 Case IH 7110.",
    ),
    (
        "tractor",
        "tractor-assembly",
        "1340707C1",
        "What is the radiator support for my 1990 Case IH 7110 tractor?",
    ),
    (
        "tractor",
        "tractor-assembly",
        "L46821",
        "I need the rubber radiator mount bushing for a 1990 Case IH 7110.",
    ),
    (
        "tractor",
        "tractor-fan",
        "A184979",
        "Find the engine radiator fan for my 1990 Case IH Magnum 7110 with the 6T-830 engine.",
    ),
    (
        "tractor",
        "tractor-fan",
        "A184524",
        "I need the 86 mm thick radiator fan spacer for my 1990 Case IH 7110, 6T-830 engine.",
    ),
    (
        "tractor",
        "tractor-fan",
        "J911567",
        "Find the eight-rib radiator fan belt, about 1701.8 mm long, for my 1990 Case IH 7110.",
    ),
    (
        "combine",
        "combine-assembly",
        "107668C3",
        "I need an engine radiator for my 1982 International Harvester 1420 combine.",
    ),
    (
        "combine",
        "combine-assembly",
        "530874R1",
        "What radiator pressure cap is listed for my 1982 Case IH 1420 combine?",
    ),
    (
        "combine",
        "combine-assembly",
        "86982771",
        "Find the radiator drain cock for my 1982 International Harvester 1420 combine.",
    ),
    (
        "combine",
        "combine-assembly",
        "680514R1",
        "I need a radiator outlet hose for a 1982 Case IH 1420 combine.",
    ),
    (
        "combine",
        "combine-assembly",
        "130870C1",
        "Find the radiator outlet tube for my 1982 International Harvester 1420 combine.",
    ),
    (
        "combine",
        "combine-assembly",
        "107689C1",
        "I need the water-pump inlet radiator hose, 2 inch ID and 4.5 inches long, for a 1982 Case IH 1420 combine.",
    ),
    (
        "combine",
        "combine-assembly",
        "194912C1",
        "What is the left-hand radiator support for my 1982 Case IH 1420 combine?",
    ),
    (
        "combine",
        "combine-assembly",
        "146450C1",
        "I need the right-hand radiator support for my 1982 International Harvester 1420 combine.",
    ),
    (
        "baler",
        "baler-cylinder",
        "87027465",
        "Find the hydraulic pickup-lift cylinder with a 28.57 mm rod and 76.2 mm stroke for my 2002 Case IH RBX451 round baler.",
    ),
    (
        "baler",
        "baler-cylinder",
        "798979",
        "My 2002 Case IH RBX451 has pickup-lift hydraulic cylinder 849372. Which seal kit is listed for that cylinder?",
    ),
    (
        "baler",
        "baler-cylinder",
        "87030873",
        "I need a seal kit for pickup-lift hydraulic cylinder 87027465 on my 2002 Case IH RBX451 baler.",
    ),
    (
        "baler",
        "baler-cylinder",
        "771598",
        "Find the piston rod for pickup-lift cylinder 849372 on my 2002 Case IH RBX451 round baler.",
    ),
    (
        "baler",
        "baler-cylinder",
        "87030037",
        "Find the piston rod for pickup-lift cylinder 87027465 on my 2002 Case IH RBX451 round baler.",
    ),
    (
        "baler",
        "baler-reel",
        "87394242",
        "I need the standard premium curved pickup tine, 186.5 mm long, for the wide double-cam pickup reel on my 2002 Case IH RBX451.",
    ),
    (
        "baler",
        "baler-reel",
        "87607074",
        "Find the optional economy curved pickup tine, 186.5 mm long, for the wide double-cam pickup reel of my 2002 Case IH RBX451.",
    ),
    (
        "baler",
        "baler-reel",
        "84350126",
        "I need the ball cam follower, 38.1 mm OD and 19 mm wide, for the wide double-cam pickup reel on a 2002 Case IH RBX451.",
    ),
]


def main():
    fixtures = Path("tests/fixtures/caseih")
    manifest = json.loads((fixtures / "manifest.json").read_bytes())
    target = Path("evals/caseih")
    target.mkdir(parents=True, exist_ok=True)
    files = {name: target / f"{name}.jsonl" for name in ("queries", "answers", "lineage")}
    if any(path.exists() for path in files.values()):
        raise FileExistsError("Authored suite already exists")
    rows = {name: [] for name in files}
    for number, (family, fixture, part, query) in enumerate(CASES, 1):
        metadata = manifest[fixture + ".html"]
        url = metadata["source_url"]
        assembly_id = url.rsplit("/", 2)[-2]
        assembly = parse.assembly(
            (fixtures / (fixture + ".html")).read_bytes(), assembly_id, url, storefront=CASEIH
        )
        found = [row for row in assembly["occurrences"] if row["part_number"] == part]
        if not found:
            raise ValueError(f"Missing authored evidence for {part}")
        row = found[0]
        case_id = f"IH{number:03}"
        rows["queries"].append({"id": case_id, "query": query})
        rows["answers"].append(
            {
                "id": case_id,
                "answer": {
                    "parts": [
                        {
                            "part_number": part,
                            "catalog_description": row["description"],
                            "product_url": row["product_url"],
                            "assembly_urls": [url],
                        }
                    ],
                    "conditions": [
                        f"Equipment family: {family}",
                        f"Observed {metadata['observed_at']}",
                        f"Callout {row['callout']}; catalog quantity {row['catalog_quantity']}",
                        *row["notes"],
                        "Catalog evidence does not establish serial-specific fitment, availability, or interchangeability.",
                    ],
                },
                "evaluation": {"requires_manual_semantic_review": True},
            }
        )
        rows["lineage"].append(
            {
                "id": case_id,
                "equipment_family": family,
                "fixture": fixture + ".html",
                "fixture_sha256": metadata["sha256"],
                "source_url": url,
                "observed_at": metadata["observed_at"],
                "occurrence_ids": [r["occurrence_id"] for r in found],
            }
        )
    for name, path in files.items():
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows[name]))
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "cases": 25,
                "families": ["tractor", "combine", "baler"],
                "files": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files.values()
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
