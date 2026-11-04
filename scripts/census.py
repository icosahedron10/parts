"""Resumable per-brand equipment census; every request uses the catalog transport."""

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from dotenv import load_dotenv
from lxml import html

from cnh_parts_agent.catalog import parse
from cnh_parts_agent.catalog.storefront import get_storefront
from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.discovery.index import validate_index
from cnh_parts_agent.settings import Settings

from .capture_fixture import save_fixture


def sitemap_series(body, storefront):
    doc = parse.document(body)
    found = {}
    for href in doc.xpath("//a/@href"):
        path = urlsplit(href).path
        segments = path.strip("/").split("/")
        if (
            path.startswith(storefront.home_path + "/na/")
            and len(segments) == 9
            and segments[-2] == "cn"
            and segments[-1].startswith("NA_")
        ):
            url = parse.source_link(path, storefront=storefront)
            found[url] = url
    if not found:
        raise ValueError("Equipment sitemap contains no recognized series links")
    return sorted(found)


def series_page(body, url, storefront):
    doc = parse.document(body)
    totals = {
        int(m[1].replace(",", ""))
        for node in doc.xpath("//p|//h1|//h2|//h3|//div[not(*)]")
        if (m := re.fullmatch(r"([\d,]+)\s+Results", " ".join(node.text_content().split()), re.I))
    }
    if len(totals) != 1:
        raise ValueError("Series page does not expose one catalog total")
    for node in doc.xpath(
        '//li[contains(@class,"category-page-search-card-container")]//h2[contains(@class,"card-title")]'
    ):
        node.tag = "h5"
    total = totals.pop()
    machines = parse.machines(html.tostring(doc), storefront=storefront) if total else []
    prefix = urlsplit(url).path.rsplit("/cn/", 1)[0] + "/"
    if any(not urlsplit(m["source_url"]).path.startswith(prefix) for m in machines):
        raise ValueError("Catalog escaped its observed series")
    pages = set()
    for href in doc.xpath('//a[contains(@href,"page=")]/@href'):
        target = urljoin(url, href)
        parsed = urlsplit(parse.source_link(target, storefront=storefront))
        if parsed.path != urlsplit(url).path:
            raise ValueError("Pagination changed series scope")
        page = int(parse_qs(parsed.query)["page"][0])
        if page < 0:
            raise ValueError("Invalid page number")
        pages.add(url.split("?", 1)[0] + (f"?page={page}" if page else ""))
    return total, machines, sorted(pages)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


async def run(args):
    storefront = get_storefront(args.brand)
    transport = SourceTransport(Settings().user_agent)
    if args.resume:
        state = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        if state["source_id"] != storefront.source_id:
            raise ValueError("Checkpoint belongs to a different storefront")
        if state.get("initialized") and args.series and state["scope"] != args.series:
            raise ValueError("Resume must preserve the checkpoint's census scope")
    else:
        if args.checkpoint.exists():
            raise FileExistsError("Use --resume or choose a new checkpoint")
        state = {
            "source_id": storefront.source_id,
            "pending": [],
            "completed": [],
            "records": {},
            "series_totals": {},
            "started_at": datetime.now(UTC).isoformat(),
        }
        if args.output.exists():
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            if validate_index(previous) != storefront:
                raise ValueError("Existing index belongs to a different storefront")
            state["records"] = {
                record["machine"]["machine_ref"]["$ref"]["id"]: record for record in previous["records"]
            }
    try:
        if not state.get("initialized"):
            response = await transport.fetch(storefront.home + "/sitemap-equipment")
            state["pending"] = sitemap_series(response.body, storefront)
            if args.series:
                state["pending"] = [
                    url
                    for url in state["pending"]
                    if any(s.casefold() in url.casefold() for s in args.series)
                ]
                if not state["pending"]:
                    raise ValueError("No series matched the requested scope")
            state["scope"] = args.series or ["all observed sitemap series"]
            state["initialized"] = True
            if args.fixture_dir and not (args.fixture_dir / "sitemap.html").exists():
                save_fixture(args.fixture_dir, "sitemap.html", response)
        while state["pending"] and transport.request_count < args.max_requests:
            url = state["pending"][0]
            response = await transport.fetch(url)
            total, machines, pages = series_page(response.body, url, storefront)
            state["series_totals"][url.split("?", 1)[0]] = total
            evidence = {
                "source_url": response.url,
                "observed_at": response.observed_at.isoformat(),
                "content_hash": response.content_hash,
                "parser_version": parse.PARSER_VERSION,
            }
            for machine in machines:
                state["records"][machine["machine_ref"]["$ref"]["id"]] = {
                    "machine": machine,
                    "evidence": evidence,
                    "discovery_query": "sitemap:" + url,
                }
            if args.fixture_dir and not (args.fixture_dir / "series.html").exists():
                save_fixture(args.fixture_dir, "series.html", response)
            state["pending"].pop(0)
            state["completed"].append(url)
            state["pending"].extend(
                p for p in pages if p not in state["completed"] and p not in state["pending"]
            )
            atomic_json(args.checkpoint, state)
            print(f"{len(state['records'])} catalogs; {len(state['pending'])} pages pending", flush=True)
    finally:
        await transport.aclose()
        atomic_json(args.checkpoint, state)
        if state["records"]:
            index = {
                "schema_version": 1,
                "source_id": storefront.source_id,
                "coverage": "partial: observed equipment census pages; scope "
                + ", ".join(state.get("scope", [])),
                "updated_at": max(r["evidence"]["observed_at"] for r in state["records"].values()),
                "records": sorted(state["records"].values(), key=lambda r: r["machine"]["label"]),
            }
            validate_index(index)
            atomic_json(args.output, index)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", required=True, choices=["casece", "caseih"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=250)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--series", action="append", help="Limit census scope to series URLs containing this text"
    )
    parser.add_argument("--fixture-dir", type=Path)
    args = parser.parse_args()
    if args.max_requests < 1:
        parser.error("--max-requests must be positive")
    load_dotenv()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
