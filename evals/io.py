"""Strict JSONL input and raw-file fingerprints."""

import hashlib
import json
from pathlib import Path


def read_jsonl(path: Path) -> tuple[list[dict], str]:
    """Read nonempty JSON objects with unique string IDs and return rows plus the raw-file SHA-256 digest."""
    raw = path.read_bytes()
    rows = []
    seen = set()
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number}: invalid JSON") from exc
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or (not row["id"].strip()):
            raise ValueError(f"{path.name}:{line_number}: expected an object with a nonempty string id")
        if row["id"] in seen:
            raise ValueError(f"{path.name}:{line_number}: duplicate id")
        seen.add(row["id"])
        rows.append(row)
    if not rows:
        raise ValueError(f"{path.name}: no cases")
    return (rows, hashlib.sha256(raw).hexdigest())
