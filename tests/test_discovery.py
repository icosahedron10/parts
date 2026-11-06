"""Frozen candidate sets and natural-language constraints."""

import copy
import json
from pathlib import Path

import pytest

from cnh_parts_agent.discovery.index import validate_index
from cnh_parts_agent.discovery.match import match

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/discovery/machine_model_alias_cases.json").read_text()
)


def identities(records):
    return [r["machine"]["machine_ref"]["$ref"]["id"] for r in records]


@pytest.mark.parametrize("group", FIXTURE["alias_groups"], ids=lambda g: g["id"])
def test_expected_candidates_and_alias_order(indexes, group):
    prepared = indexes["casece"]["prepared"]
    canonical = identities(match(prepared, group["canonical_query"]))
    assert set(canonical) == {r["catalog_id"] for r in group["expected_relevant"]}
    for alias in group["aliases"]:
        assert identities(match(prepared, alias["query"])) == canonical


@pytest.mark.parametrize(
    "query",
    [
        "my 2010 CASE CX350B",
        "2010 CASE CX350B",
        "CASE 2010 CX350B",
        "CASE CX350B, built in 2010",
        "my Case CX 350 B 2010",
    ],
)
def test_natural_language_year_make_model(indexes, query):
    rows = match(indexes["casece"]["prepared"], query)
    assert len(rows) == 1
    assert rows[0]["machine"]["label"].startswith("CX350B -")


@pytest.mark.parametrize("query", ["CX350B 2025", "CX350 Z", "CX3500", "an excavator with tracks"])
def test_does_not_relax_unknown_models_or_years(indexes, query):
    assert match(indexes["casece"]["prepared"], query) == []


def test_invalid_or_duplicate_index_fails(indexes):
    snapshot = copy.deepcopy({k: v for k, v in indexes["casece"].items() if k not in {"prepared", "sha256"}})
    snapshot["records"].append(snapshot["records"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_index(snapshot)


def test_expanded_named_model_spacing_preserves_all_catalog_choices(indexes):
    from cnh_parts_agent.discovery.match import prepare

    records = [copy.deepcopy(indexes["caseih"]["records"][0]) for _ in range(2)]
    for i, (record, label) in enumerate(zip(records, ("FARMALL 110 U", "FARMALL 110U"), strict=True)):
        record["machine"]["label"] = label + " - TRACTOR"
        record["machine"]["machine_ref"]["$ref"]["id"] = f"fixture-{i}"
    prepared = prepare(records)
    for query in ("Farmall 110U", "Farmall 110 U", "Case IH Farmall 110U"):
        assert set(identities(match(prepared, query))) == {"fixture-0", "fixture-1"}
    assert match(prepared, "Farmall 110V") == []
