"""Shared CNH operations; all source-specific routing comes from Storefront."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from . import parse
from .errors import PartsError
from .storefront import CASECE, Storefront
from .transport import SourceResponse


@dataclass
class ParsedResult:
    data: dict
    outcome: str
    coverage: dict
    evidence: list


PARSER_VERSION = parse.PARSER_VERSION
MODES = {"part_description": "part-description", "part_number": "part-number", "assembly_title": "figure"}
OPERATIONS = ("search_machines", "list_groups", "search_catalog", "get_assembly", "get_part_relationships")


def validate_reference(payload: dict, *, storefront: Storefront = CASECE) -> None:
    """Reject references outside this source, resource kind, catalog context, or approved route."""
    if not isinstance(payload, dict) or payload.get("source_id") != storefront.source_id:
        raise PartsError("invalid_reference", "Reference belongs to another source.")
    kind, item_id, context, path = (payload.get(key) for key in ("kind", "id", "context", "path"))
    if kind not in ("machine", "group", "assembly", "part"):
        raise PartsError("invalid_reference", "Unsupported reference kind.")
    try:
        if kind in ("group", "assembly"):
            context = parse.identifier(context)
        elif context is not None:
            raise PartsError("invalid_reference", "Reference has an unexpected catalog context.")
        if kind == "group":
            assert context is not None
            item_id = parse.group_identifier(item_id, context)
        else:
            item_id = parse.identifier(item_id, part=kind == "part")
        if kind == "part":
            if item_id in parse.UNSUPPLIED_PARTS or path is not None:
                raise PartsError(
                    "invalid_reference", "Part reference does not identify a purchasable source part."
                )
            return
        if kind == "group":
            assert context is not None
            if not item_id.startswith(context + "_") or path is not None:
                raise PartsError("invalid_reference", "Group reference belongs to another catalog.")
            return
        if not isinstance(path, str) or not path.startswith(storefront.home_path + "/na/"):
            raise PartsError("invalid_reference", "Reference is missing an approved catalog path.")
        parsed = urlsplit(parse.source_link(path, storefront=storefront))
        suffix = "/cn/" + item_id
        if kind == "assembly":
            assert context is not None
            suffix += "/" + context
        if parsed.query or parsed.fragment or (not path.endswith(suffix)) or (path.count("/cn/") != 1):
            raise PartsError("invalid_reference", "Reference path does not match its source identity.")
    except PartsError as exc:
        if exc.code == "invalid_reference":
            raise
        raise PartsError(
            "invalid_reference", "Reference contains an unsupported source identifier or path."
        ) from None


def resolved(request: dict, name: str, kind: str, *, storefront: Storefront = CASECE) -> dict:
    """Return a validated reference payload from a resolved request; reject missing or mismatched kinds."""
    payload = request.get(name)
    if not isinstance(payload, dict):
        raise PartsError("invalid_reference", "A resolved resource reference is required.")
    validate_reference(payload, storefront=storefront)
    if payload["kind"] != kind:
        raise PartsError("invalid_reference", "Reference has the wrong resource kind.")
    return payload


def validate_request(operation: str, request: dict, *, storefront: Storefront = CASECE) -> None:
    """Reject unsupported source operations and filters before any upstream request."""
    if request.get("source_id") != storefront.source_id:
        raise PartsError("unsupported_source", "The CNH adapter does not support this source.")
    if operation not in OPERATIONS:
        raise PartsError("unsupported_operation", "This source does not support the operation.")
    if any(
        (
            request.get(name) not in (None, {}, [])
            for name in ("filters", "model", "variant", "date", "serial_number", "pin", "group_ref")
        )
    ):
        raise PartsError("unsupported_filter", "This source has no verified query filters.")
    if operation == "search_catalog" and request.get("mode") not in MODES:
        raise PartsError("unsupported_mode", "The source does not support this search mode.")
    if operation in ("search_machines", "search_catalog"):
        query = request.get("query")
        if not isinstance(query, str) or not 2 <= len(query) <= 120 or (not query.strip()):
            raise PartsError("invalid_input", "Query must contain 2 to 120 characters.")


async def execute(
    operation: str,
    request: dict,
    fetch: Callable[[str], Awaitable[SourceResponse]],
    *,
    storefront: Storefront = CASECE,
) -> ParsedResult:
    """Fetch and parse one supported catalog operation using the caller-owned bounded fetch function."""

    validate_request(operation, request, storefront=storefront)
    machine: dict = {}
    if operation in ("list_groups", "search_catalog", "get_assembly"):
        machine = resolved(request, "machine_ref", "machine", storefront=storefront)
    if operation == "search_machines":
        url = storefront.home + "/search/?" + urlencode({"byModel": "1", "text": request["query"]})
    elif operation == "list_groups":
        url = parse.ORIGIN + machine["path"]
    elif operation == "search_catalog":
        url = (
            storefront.home
            + "/dc/searchInModel/"
            + MODES[request["mode"]]
            + "?"
            + urlencode(
                {
                    "modelCode": machine["id"],
                    "paramSearch": request["query"],
                    "regionCode": storefront.region_code,
                }
            )
        )
    elif operation == "get_assembly":
        assembly_ref = resolved(request, "assembly_ref", "assembly", storefront=storefront)
        prefix = machine["path"].rsplit("/cn/", 1)[0] + "/"
        if assembly_ref["context"] != machine["id"] or not assembly_ref["path"].startswith(prefix):
            raise PartsError("invalid_reference", "Assembly belongs to another machine catalog.")
        url = parse.ORIGIN + assembly_ref["path"]
    else:
        part = resolved(request, "part_ref", "part", storefront=storefront)
        url = storefront.home + "/cn/assemblyRemanAltsSubModal?" + urlencode({"productCode": part["id"]})
    response = await fetch(url)
    source_url = parse.source_link(response.url, storefront=storefront)
    evidence = [
        {
            "source_url": source_url,
            "observed_at": response.observed_at.isoformat(),
            "content_hash": response.content_hash,
            "parser_version": PARSER_VERSION,
        }
    ]
    coverage: dict = {"status": "unknown", "reason": "source_does_not_establish_completeness"}
    outcome = "ok"
    if operation == "search_machines":
        candidates = parse.machines(response.body, storefront=storefront)
        data = {"machines": candidates, "refinement_fields": ["machine_ref"] if len(candidates) > 1 else []}
        outcome = "ambiguous" if len(candidates) > 1 else "ok"
    elif operation == "list_groups":
        data = parse.groups(response.body, machine["id"])
        coverage = {"status": "complete_for_scope", "reason": None}
    elif operation == "search_catalog":
        result = parse.search(
            response.body, machine["id"], machine["path"], MODES[request["mode"]], storefront=storefront
        )
        data = {
            "matches": result["matches"],
            "metadata": {
                "source_pagination": result["source_pagination"],
                "diagram_urls": result["diagram_urls"],
            },
        }
        outcome, coverage = (result["outcome"], result["coverage"])
    elif operation == "get_assembly":
        data = parse.assembly(response.body, assembly_ref["id"], url, storefront=storefront)
    else:
        relationships = parse.substitutions(response.body, part["id"])
        data = {"relationships": relationships}
        coverage = {
            "status": "unknown",
            "reason": "only_displayed_direct_substitutions_observed"
            if relationships
            else "no_displayed_substitutions_not_verified_absence",
        }
    return ParsedResult(data=data, outcome=outcome, coverage=coverage, evidence=evidence)
