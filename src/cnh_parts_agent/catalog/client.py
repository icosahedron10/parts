"""Catalog lifecycle, offline discovery, and cached live operations."""

import asyncio
import re
from contextlib import asynccontextmanager

from ..diagnostics import current
from ..discovery.index import load_indexes
from ..discovery.match import match
from ..settings import Settings
from . import navigation, operations, parse
from .cache import Cache, cache_key
from .errors import PartsError
from .models import DATA_MODELS, ROWS, OpResult, expand_references
from .storefront import get_storefront
from .transport import SourceTransport


class Catalog:
    def __init__(self, settings: Settings, *, transport=None, indexes=None):
        self.settings = settings
        self.indexes = load_indexes(settings.index_dir) if indexes is None else indexes
        self.cache = Cache(settings.cache_path)
        navigation.initialize(self.cache.connection)
        self.transport = transport or SourceTransport(settings.user_agent, settings=settings)

    def candidates(self, query, brand=None):
        inferred = (
            "caseih"
            if re.search(r"\b(?:case\s*ih|ih|international\s+harvester)\b", query, re.I)
            else "casece"
            if re.search(r"\b(?:case\s+construction|casece)\b", query, re.I)
            else None
        )
        new_holland = bool(re.search(r"\b(?:new\s*holland(?:ag|ce)?|nh)\b", query, re.I))
        case_brand = bool(re.search(r"\b(?:case|casece|caseih|ih|international\s+harvester)\b", query, re.I))
        if case_brand and brand and brand.startswith("newholland"):
            raise PartsError("invalid_input", "Brand hint conflicts with the machine wording.")
        if new_holland:
            if case_brand:
                raise PartsError("invalid_input", "Machine wording contains conflicting brands.")
            inferred = (
                "newhollandce"
                if re.search(r"\b(?:newhollandce|construction)\b", query, re.I)
                else "newhollandag"
                if re.search(r"\b(?:newhollandag|agriculture|agricultural)\b", query, re.I)
                else None
            )
            if brand and not brand.startswith("newholland"):
                raise PartsError("invalid_input", "Brand hint conflicts with the machine wording.")
            if not any(name.startswith("newholland") for name in self.indexes):
                raise PartsError("machine_index_missing", "New Holland discovery is not installed yet.")
        if brand is not None:
            get_storefront(brand)
        if brand and inferred and brand != inferred:
            raise PartsError("invalid_input", "Brand hint conflicts with the machine wording.")
        brand = brand or inferred
        if brand and brand not in self.indexes:
            raise PartsError("machine_index_missing", "The requested brand has no installed index.")
        found = []
        for name, index in self.indexes.items():
            if (
                (brand is None or brand == name)
                and (not new_holland or name.startswith("newholland"))
                and (not case_brand or name in {"casece", "caseih"})
            ):
                found.extend((name, record) for record in match(index["prepared"], query))
        return found

    async def execute(self, operation: str, arguments: dict, *, observations=None) -> dict:
        if operation not in DATA_MODELS:
            raise PartsError("unsupported_operation", "Unknown catalog operation.")
        args = dict(arguments)
        offset, limit = args.pop("offset", 0), args.pop("limit", 50)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise PartsError("invalid_input", "Pagination requires offset >= 0 and limit between 1 and 200.")
        if operation == "search_machines":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip() or len(query) > 200:
                raise PartsError(
                    "invalid_input", "Supply a nonempty machine query of at most 200 characters."
                )
            candidates = self.candidates(query, args.get("brand"))
            machines, evidence = [], {}
            for brand, record in candidates:
                machines.append(expand_references(record["machine"], get_storefront(brand).source_id))
                e = record["evidence"]
                evidence[(e["source_url"], e["observed_at"])] = e
            # A miss retains the snapshot's provenance and never claims source-wide absence.
            if not candidates:
                for index in self.indexes.values():
                    e = index["records"][0]["evidence"]
                    evidence[(e["source_url"], e["observed_at"])] = e
            result = {
                "data": {
                    "machines": machines,
                    "metadata": {
                        "source_notes": [
                            "Discovery uses a dated, partial local snapshot; no match does not establish CNH absence.",
                            "Supply a model with any known year, make, and variant. Similarity is not confirmed fitment.",
                        ]
                    },
                },
                "outcome": "ambiguous" if len(machines) > 1 else "partial",
                "coverage": {"status": "partial", "reason": "dated_local_machine_index"},
                "evidence": list(evidence.values()),
            }
        elif operation in {"list_assemblies", "list_groups"}:
            machine = args.get("machine_ref")
            if not isinstance(machine, dict):
                raise PartsError("invalid_reference", "An observed machine reference is required.")
            storefront = get_storefront(machine["source_id"].removesuffix("-us-en"))
            operations.validate_reference(machine, storefront=storefront)
            if machine["kind"] != "machine":
                raise PartsError("invalid_reference", "Expected a machine reference.")
            directory = navigation.read(self.cache.connection, machine)
            entries = directory["entries"] if directory else []
            if args.get("group") is not None:
                entries = [e for e in entries if e["group"] == args["group"]]
            if args.get("query"):
                entries = [e for e in entries if args["query"].casefold() in e["title"].casefold()]
            data = (
                {"assemblies": entries}
                if operation == "list_assemblies"
                else {"groups": directory["groups"] if directory else []}
            )
            result = {
                "data": data,
                "outcome": "ok" if data[ROWS[operation]] else "not_found",
                "coverage": {
                    "status": "complete_for_scope" if directory and directory["complete"] else "partial",
                    "reason": "dated_local_navigation",
                },
                "evidence": directory["evidence"] if directory else [],
            }
        else:
            ref = args.get("machine_ref", args.get("part_ref"))
            if not isinstance(ref, dict):
                raise PartsError("invalid_reference", "An observed catalog reference is required.")
            source_id = ref.get("source_id", "")
            storefront = get_storefront(source_id.removesuffix("-us-en"))
            args["source_id"] = source_id
            operations.validate_request(operation, args, storefront=storefront)
            for name in ("machine_ref", "assembly_ref", "part_ref"):
                if name in args:
                    operations.validate_reference(args[name], storefront=storefront)
            key = cache_key(source_id, operation, args, parse.PARSER_VERSION)
            result = observations.get(key) if observations is not None else None
            if result is None:
                result = self.cache.get(key)
            status = "hit"
            if result is None:
                async with self.cache.filling(key):
                    result = self.cache.get(key)
                    status = "coalesced"
                    if result is None:
                        try:
                            parsed = await operations.execute(
                                operation, args, self.transport.fetch, storefront=storefront
                            )
                        except PartsError as exc:
                            if operation == "get_assembly" and (
                                exc.status in {404, 410}
                                or exc.code
                                in {"source_not_found", "parser_schema_changed", "unexpected_source_content"}
                            ):
                                navigation.mark_stale(
                                    self.cache.connection, args["machine_ref"], args["assembly_ref"]
                                )
                            raise
                        data = DATA_MODELS[operation].model_validate(
                            expand_references(parsed.data, source_id)
                        )
                        result = {
                            "data": data.model_dump(mode="json"),
                            "outcome": parsed.outcome,
                            "coverage": parsed.coverage,
                            "evidence": parsed.evidence,
                        }
                        ttl = self.settings.evidence_ttl_seconds
                        if not result["data"][ROWS[operation]]:
                            ttl = 3600
                        self.cache.put(key, result, ttl)
                        status = "miss"
            if observations is not None:
                observations[key] = result
            if (diagnostics := current.get()) is not None:
                diagnostics.cache.append(status)
        if operation == "get_assembly":
            directory = navigation.read(self.cache.connection, args["machine_ref"])
            title = (
                next(
                    (
                        e["title"]
                        for e in directory["entries"]
                        if e["assembly_ref"]["id"] == args["assembly_ref"]["id"]
                    ),
                    None,
                )
                if directory
                else None
            )
            result = {
                **result,
                "data": {
                    **result["data"],
                    "assembly_ref": args["assembly_ref"],
                    "assembly_label": title,
                    "referenced_assemblies": [
                        e
                        for e in navigation.figure_routes(directory, result["data"]["occurrences"])
                        if e["assembly_ref"]["id"] != args["assembly_ref"]["id"]
                    ],
                },
            }
        if operation == "search_catalog" and result["data"]["matches"]:
            navigation.retain_matches(
                self.cache.connection, args["machine_ref"], result["data"]["matches"], result["evidence"]
            )
        rows = result["data"][ROWS[operation]]
        total = len(rows)
        result = {
            **result,
            "data": {**result["data"], ROWS[operation]: rows[offset : offset + limit]},
            "total": total,
            "truncated": offset + limit < total,
        }
        return OpResult[DATA_MODELS[operation]].model_validate(result).model_dump(mode="json")

    async def aclose(self):
        try:
            await self.transport.aclose()
        finally:
            self.cache.close()


_shared = None
_owners = 0
_loop = None


@asynccontextmanager
async def open_catalog(settings: Settings | None = None):
    """Borrow the process's shared catalog; incompatible simultaneous settings fail."""
    global _shared, _owners, _loop
    settings = settings or Settings()
    loop = asyncio.get_running_loop()
    if _shared is not None and (_loop is not loop or _shared.settings != settings):
        raise RuntimeError("One active catalog configuration and event loop are supported per process")
    if _shared is None:
        _shared = Catalog(settings)
        _loop = loop
    catalog = _shared
    _owners += 1
    try:
        yield catalog
    finally:
        _owners -= 1
        if _owners == 0:
            try:
                await catalog.aclose()
            finally:
                _shared, _loop = None, None
