"""Observed second-storefront navigation and brand-aware discovery."""

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from cnh_parts_agent.catalog import operations, parse
from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.catalog.storefront import CASECE, CASEIH
from cnh_parts_agent.catalog.transport import SourceResponse
from cnh_parts_agent.discovery.match import match, prepare
from scripts.census import series_page, sitemap_series

FIXTURES = Path(__file__).parent / "fixtures/caseih"


def test_observed_fixture_hashes():
    for name, metadata in json.loads((FIXTURES / "manifest.json").read_bytes()).items():
        body = (FIXTURES / name).read_bytes()
        assert len(body) == metadata["bytes"]
        assert hashlib.sha256(body).hexdigest() == metadata["sha256"]


def test_caseih_navigation_uses_shared_parser():
    series = sitemap_series((FIXTURES / "sitemap.html").read_bytes(), CASEIH)
    assert any("magnum7100" in url for url in series)
    assert any("%26" in url for url in series)
    metadata = json.loads((FIXTURES / "manifest.json").read_bytes())["series.html"]
    total, rows, pages = series_page((FIXTURES / "series.html").read_bytes(), metadata["source_url"], CASEIH)
    assert len(rows) == total == 16
    assert rows[0]["source_url"].startswith(CASEIH.home)
    assert not pages
    with pytest.raises(PartsError):
        parse.source_link(rows[0]["source_url"], storefront=CASECE)


@pytest.mark.parametrize(
    "query,prefix",
    [
        ("my 1990 Case IH Magnum 7110 tractor", "7110 -"),
        ("Case IH RBX 451 round baler", "RBX451 -"),
        ("1982 International Harvester 1420 combine", "1420 -"),
        ("Case IH RBX341 silage pack", "RBX341 - SILAGE"),
    ],
)
def test_agricultural_natural_language(indexes, query, prefix):
    rows = match(indexes["caseih"]["prepared"], query)
    assert len(rows) == 1
    assert rows[0]["machine"]["label"].startswith(prefix)


def test_caseih_ambiguity_and_contradictions(catalog):
    assert len(catalog.candidates("Case IH RBX341")) == 2
    assert catalog.candidates("Case IH 7110 combine") == []
    assert catalog.candidates("Case IH 7110 2005") == []
    assert catalog.candidates("Case IH RBX451Z") == []


def test_cross_brand_identifier_cannot_select_first(catalog):
    duplicate = copy.deepcopy(catalog.indexes["caseih"]["records"][2])
    duplicate["machine"]["label"] = "CX350B - SYNTHETIC TEST CATALOG"
    catalog.indexes = copy.deepcopy(catalog.indexes)
    catalog.indexes["caseih"]["prepared"] = prepare([duplicate])
    assert {brand for brand, _ in catalog.candidates("CX350B")} == {"casece", "caseih"}
    assert {brand for brand, _ in catalog.candidates("CX350B", "casece")} == {"casece"}


def test_multiword_model_is_one_identity(indexes):
    record = copy.deepcopy(indexes["caseih"]["records"][2])
    record["machine"]["label"] = "MAGNUM 250 - SYNTHETIC TEST TRACTOR"
    prepared = prepare([record])
    assert match(prepared, "Case IH Magnum 250 tractor") == [record]
    assert match(prepared, "Case IH Magnum 251 tractor") == []


@pytest.mark.parametrize(
    "name,operation",
    [
        ("tractor-groups.html", "list_groups"),
        ("tractor-search.json", "search_catalog"),
        ("tractor-number.json", "search_catalog"),
        ("tractor-description.json", "search_catalog"),
        ("tractor-negative.json", "search_catalog"),
        ("combine-search.json", "search_catalog"),
        ("baler-search.json", "search_catalog"),
        ("tractor-assembly.html", "get_assembly"),
        ("tractor-fan.html", "get_assembly"),
        ("combine-assembly.html", "get_assembly"),
        ("baler-cylinder.html", "get_assembly"),
        ("baler-reel.html", "get_assembly"),
        ("tractor-relationships.html", "get_part_relationships"),
    ],
)
async def test_shared_operations_against_observed_caseih_pages(indexes, name, operation):
    metadata = json.loads((FIXTURES / "manifest.json").read_bytes())[name]
    url = metadata["source_url"]
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    machine_id = query.get("modelCode", [parsed.path.rsplit("/", 1)[-1]])[0]
    request = {"source_id": CASEIH.source_id}
    if operation != "get_part_relationships":
        row = next(
            r for r in indexes["caseih"]["records"] if r["machine"]["machine_ref"]["$ref"]["id"] == machine_id
        )
        request["machine_ref"] = expand_references(row["machine"]["machine_ref"], CASEIH.source_id)
    if operation == "search_catalog":
        mode = {
            "figure": "assembly_title",
            "part-description": "part_description",
            "part-number": "part_number",
        }
        request.update(query=query["paramSearch"][0], mode=mode[parsed.path.rsplit("/", 1)[-1]])
    elif operation == "get_assembly":
        request["assembly_ref"] = {
            "source_id": CASEIH.source_id,
            "kind": "assembly",
            "id": parsed.path.rsplit("/", 2)[-2],
            "context": machine_id,
            "path": parsed.path,
        }
    elif operation == "get_part_relationships":
        request["part_ref"] = {
            "source_id": CASEIH.source_id,
            "kind": "part",
            "id": query["productCode"][0],
            "context": None,
            "path": None,
        }

    async def fetch(actual_url):
        assert actual_url == url
        return SourceResponse(
            url,
            (FIXTURES / name).read_bytes(),
            datetime.fromisoformat(metadata["observed_at"]),
            metadata["source_content_hash"],
        )

    result = await operations.execute(operation, request, fetch, storefront=CASEIH)
    if "negative" in name:
        assert result.outcome == "not_found"
    elif "relationships" in name:
        assert {r["to_part_number"] for r in result.data["relationships"]} == {"A190663"}
        assert {r["from_part_number"] for r in result.data["relationships"]} == {"A189087", "A189091"}
    else:
        assert result.outcome == "ok"
    for row in result.data.get("occurrences", []) + result.data.get("matches", []):
        if row.get("part_number") in {"NSS", "NLS", "REF"}:
            assert row["part_ref"] is None and row["product_url"] is None


def test_authored_caseih_suite_is_query_only_and_frozen():
    directory = Path(__file__).parents[1] / "evals/caseih"
    manifest = json.loads((directory / "manifest.json").read_bytes())
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((directory / filename).read_bytes()).hexdigest() == digest
    queries = [
        json.loads(line) for line in (directory / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(queries) == 25 and all(set(row) == {"id", "query"} for row in queries)
