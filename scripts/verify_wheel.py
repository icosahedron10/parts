"""Verify bundled indexes and public imports outside the checkout."""

import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cnh-wheel-check-") as directory:
        with zipfile.ZipFile(args.wheel) as archive:
            if any(".env" in Path(n).parts or n.startswith("tests/") for n in archive.namelist()):
                raise ValueError("Wheel contains private configuration or development tests")
            archive.extractall(directory)
        code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cnh_parts_agent
from cnh_parts_agent.discovery.index import load_indexes
assert Path(cnh_parts_agent.__file__).is_relative_to(Path(sys.argv[1]))
indexes = load_indexes()
assert len(indexes['casece']['records']) == 1339
assert len(indexes['caseih']['records']) >= 3
assert callable(cnh_parts_agent.open_catalog)
assert callable(cnh_parts_agent.make_lookup_parts_tool)
print({brand: len(index['records']) for brand, index in indexes.items()})
"""
        subprocess.run([sys.executable, "-I", "-c", code, directory], cwd=directory, check=True)


if __name__ == "__main__":
    main()
