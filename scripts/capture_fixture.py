"""Capture explicitly requested anonymous catalog observations as sanitized fixtures."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from dotenv import load_dotenv
from lxml import html

from cnh_parts_agent.catalog import parse
from cnh_parts_agent.catalog.storefront import get_storefront
from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.settings import Settings


def sanitize(body: bytes) -> bytes:
    if body.lstrip().startswith(b"{"):
        data = json.loads(body)
        if "results" not in data or "pagination" not in data:
            raise ValueError("Expected a catalog-search JSON response")
        fields = {
            "modelCode",
            "assemblyCode",
            "assemblyFullName",
            "functionalGroupCode",
            "sectionCode",
            "assemblyUrl",
            "urlAssemblyImage",
            "technicalTypes",
            "modelFullName",
            "partNumber",
            "partDescription",
            "usage",
            "showNote",
            "product",
            "quantity",
        }
        rows = []
        for row in data["results"]:
            clean = {k: v for k, v in row.items() if k in fields}
            if isinstance(clean.get("product"), dict):
                clean["product"] = {"url": clean["product"].get("url")}
            rows.append(clean)
        return json.dumps({"results": rows, "pagination": data["pagination"]}, indent=2).encode()
    doc = parse.document(body)
    for node in doc.xpath("//script|//style|//noscript|//iframe|//input|//meta|//link|//comment()"):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for node in doc.iter():
        for name in list(node.attrib):
            if name.lower().startswith("on") or any(t in name.lower() for t in ("token", "csrf", "session")):
                del node.attrib[name]
    # Metadata removal also removes charset declarations. ASCII character
    # references preserve Unicode when the sanitized HTML is parsed again.
    return html.tostring(doc, encoding="ascii")


def save_fixture(directory: Path, name: str, response):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if path.exists():
        raise FileExistsError(path)
    body = sanitize(response.body)
    path.write_bytes(body)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest[name] = {
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "source_url": response.url,
        "observed_at": response.observed_at.isoformat(),
        "source_content_hash": response.content_hash,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


async def run(args):
    storefront = get_storefront(args.brand)
    url = parse.source_link(args.url, storefront=storefront)
    transport = SourceTransport(Settings().user_agent)
    try:
        response = await transport.fetch(url)
        print(save_fixture(args.directory, args.name, response))
    finally:
        await transport.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", required=True, choices=["casece", "caseih"])
    parser.add_argument("--url", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name == "manifest.json":
        parser.error("--name must be a fixture filename, not a path or manifest.json")
    load_dotenv()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
