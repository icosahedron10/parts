"""Verify the frozen exam's hashes, isolation, and four-way case lineage offline."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from evals.io import read_jsonl

EXAM = Path(__file__).resolve().parent
STYLES = {"year_first", "year_between", "machine_first", "two_sentences"}


# Frozen hashes and structure detect accidental edits before an expensive live exam.
def verify(directory: Path = EXAM) -> dict:
    """Validate all 400 ordered query/answer pairs and their preserved base-case constraints.

    Read only local files. Raise ValueError on integrity or schema drift; never repair
    the frozen key or send answer content to the evaluated agent.
    """
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    names = {"queries.jsonl", "answers.jsonl", "lineage.jsonl"}
    if (
        manifest.get("schema_version") != 1
        or manifest.get("base_cases") != 100
        or manifest.get("variants") != 400
        or set(manifest.get("styles", [])) != STYLES
        or set(manifest.get("files", {})) != names
    ):
        raise ValueError("The manifest must describe the frozen 100-by-four exam")
    rows = {}
    for name in sorted(names):
        rows[name], digest = read_jsonl(directory / name)
        if digest != manifest["files"][name] or len(rows[name]) != 400:
            raise ValueError(f"Frozen exam integrity mismatch: {name}")
    queries, answers, lineage = (rows[name] for name in ("queries.jsonl", "answers.jsonl", "lineage.jsonl"))
    identities = [row["id"] for row in queries]
    if any([row["id"] for row in group] != identities for group in (answers, lineage)):
        raise ValueError("Query, answer, and lineage order must agree exactly")
    families: dict[str, dict] = {}
    for query, answer, origin in zip(queries, answers, lineage, strict=True):
        if set(query) != {"id", "query"} or not isinstance(query["query"], str) or not query["query"].strip():
            raise ValueError("Agent inputs must contain only an ID and a nonempty query")
        if (
            origin.get("style") not in STYLES
            or not isinstance(origin.get("base_id"), str)
            or origin["id"] != origin["base_id"] + "." + origin["style"]
        ):
            raise ValueError("Invalid base-case lineage or syntax style")
        gold = answer.get("answer", {})
        parts = gold.get("parts")
        if (
            not isinstance(parts, list)
            or not parts
            or any(
                not isinstance(part, dict)
                or not isinstance(part.get("part_number"), str)
                or not part["part_number"].strip()
                or not part.get("assembly_urls")
                for part in parts
            )
        ):
            raise ValueError("Every answer needs supported part candidates and assembly references")
        constraints = {"machine": origin.get("machine"), "part_type": origin.get("part_type"), "answer": gold}
        family = families.setdefault(origin["base_id"], {"constraints": constraints, "styles": set()})
        if family["constraints"] != constraints or origin["style"] in family["styles"]:
            raise ValueError("A paraphrase changed its frozen answer or machine/part constraints")
        family["styles"].add(origin["style"])
    if len(families) != 100 or any(family["styles"] != STYLES for family in families.values()):
        raise ValueError("Every base case must have exactly four distinct syntax variants")
    return {
        "cases": 400,
        "base_cases": 100,
        "styles": dict(Counter(row["style"] for row in lineage)),
        "manifest_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest(),
        "integrity": "passed",
        "network_requests": 0,
    }


# A nonzero process exit makes integrity drift visible to release automation.
def main() -> None:
    """Print the offline integrity result; propagate validation failures as a nonzero exit."""
    print(json.dumps(verify(), indent=2))


if __name__ == "__main__":
    main()
