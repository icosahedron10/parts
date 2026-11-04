"""Catalog lifecycle, offline discovery, and cached live operations."""

import asyncio
import re
from contextlib import asynccontextmanager

from ..diagnostics import current
from ..discovery.index import load_indexes
from ..discovery.match import match
from ..settings import Settings
from . import operations, parse
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
        self.transport = transport or SourceTransport(settings.user_agent, settings=settings)

    def candidates(self, query, brand=None):
        inferred = (
            "caseih"
            if re.search(r"\b(?:case\s*ih|international\s+harvester)\b", query, re.I)
            else "casece"
            if re.search(r"\b(?:case\s+construction|casece)\b", query, re.I)
            else None
        )
        if brand is not None:
            get_storefront(brand)
        if brand and inferred and brand != inferred:
            raise PartsError("invalid_input", "Brand hint conflicts with the machine wording.")
        brand = brand or inferred
        if brand and brand not in self.indexes:
            raise PartsError("machine_index_missing", "The requested brand has no installed index.")
        found = []
        for name, index in self.indexes.items():
            if brand is None or brand == name:
                found.extend((name, record) for record in match(index["prepared"], query))
        return found

    async def execute(self, operation: str, arguments: dict) -> dict:
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
            result = self.cache.get(key)
            status = "hit"
            if result is None:
                async with self.cache.filling(key):
                    result = self.cache.get(key)
                    status = "coalesced"
                    if result is None:
                        parsed = await operations.execute(
                            operation, args, self.transport.fetch, storefront=storefront
                        )
                        data = DATA_MODELS[operation].model_validate(
                            expand_references(parsed.data, source_id)
                        )
                        result = {
                            "data": data.model_dump(mode="json"),
                            "outcome": parsed.outcome,
                            "coverage": parsed.coverage,
                            "evidence": parsed.evidence,
                        }
                        ttl = 604800 if operation in {"list_groups", "get_assembly"} else 86400
                        if not result["data"][ROWS[operation]]:
                            ttl = 3600
                        self.cache.put(key, result, ttl)
                        status = "miss"
            if (diagnostics := current.get()) is not None:
                diagnostics.cache.append(status)
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
