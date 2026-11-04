"""Validate captured catalog identities without pretending old observations are fresh."""

import hashlib
import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from ..catalog import parse
from ..catalog.models import Evidence, Machine, expand_references
from ..catalog.operations import validate_reference
from ..catalog.storefront import get_storefront
from .match import prepare


def validate_index(index: dict):
    if index.get("schema_version") != 1 or not isinstance(index.get("records"), list):
        raise ValueError("Unsupported machine index")
    storefront = get_storefront(index["source_id"].removesuffix("-us-en"))
    if index["source_id"] != storefront.source_id or not 1 <= len(index["records"]) <= 20000:
        raise ValueError("Invalid source or record count")
    seen = set()
    for record in index["records"]:
        machine = Machine.model_validate(expand_references(record["machine"], storefront.source_id))
        ref = machine.machine_ref.model_dump()
        validate_reference(ref, storefront=storefront)
        if ref["id"] in seen:
            raise ValueError("Duplicate catalog identity")
        seen.add(ref["id"])
        if (
            parse.source_link(machine.source_url, storefront=storefront).split("?", 1)[0]
            != parse.ORIGIN + ref["path"]
        ):
            raise ValueError("Machine URL and identity disagree")
        evidence = Evidence.model_validate(record["evidence"])
        if evidence.observed_at.tzinfo is None or evidence.observed_at > datetime.now(UTC):
            raise ValueError("Machine observation must be timezone-aware and not in the future")
        # Historical parser versions describe provenance, not the runtime cache version.
        parse.source_link(evidence.source_url, storefront=storefront)
    return storefront


def load_indexes(directory: Path | None = None):
    if directory is None:
        directory = files("cnh_parts_agent").joinpath("data/indexes")
        if not directory.is_dir():
            # Editable checkout: the authoritative assets stay outside the source package.
            directory = Path(__file__).resolve().parents[3] / "data/indexes"
    indexes = {}
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        if not path.name.endswith(".json"):
            continue
        raw = path.read_bytes()
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("Machine index exceeds 32 MiB")
        index = json.loads(raw)
        storefront = validate_index(index)
        if storefront.brand in indexes:
            raise ValueError("Duplicate storefront index")
        indexes[storefront.brand] = {
            **index,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "prepared": prepare(index["records"]),
        }
    if not indexes:
        raise ValueError("No machine indexes are installed")
    return indexes
