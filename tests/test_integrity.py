"""Immutable assets and frozen exam remain independent of inference."""

import hashlib
import json
from pathlib import Path

from evals.benchmark.verify import verify


def test_exam_integrity():
    assert verify()["integrity"] == "passed"


def test_parser_fixture_hashes():
    directory = Path(__file__).parent / "fixtures/casece"
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, metadata in manifest.items():
        raw = (directory / name).read_bytes()
        assert len(raw) == metadata["bytes"]
        assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
