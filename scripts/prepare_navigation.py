"""Prepare queued storefronts serially; repeat bounded batches to resume observed queues."""

import argparse
import asyncio
import base64
import hashlib
import json
import random
import zlib
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from cnh_parts_agent.catalog import navigation, parse
from cnh_parts_agent.catalog.client import Catalog
from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.catalog.storefront import get_storefront
from cnh_parts_agent.catalog.transport import SourceResponse
from cnh_parts_agent.discovery.index import validate_index
from cnh_parts_agent.settings import Settings

from .census import atomic_json, series_page, sitemap_series

# Preserve the existing CASE discovery stage; New Holland cannot start until CASE is complete.
PREPARATION_STAGES = (("casece", "caseih"), ("newhollandag",), ("newhollandce",))


class BatchComplete(Exception):
    """The bounded preparation batch has used its request allowance."""


def evidence(response):
    return {
        "source_url": response.url,
        "observed_at": response.observed_at.isoformat(),
        "content_hash": response.content_hash,
        "parser_version": parse.PARSER_VERSION,
    }


async def checkpoint_fetch(catalog, url, limit):
    connection = catalog.cache.connection
    row = connection.execute("SELECT payload FROM preparation WHERE url=?", (url,)).fetchone()
    if row:
        catalog.preparation_reused = getattr(catalog, "preparation_reused", 0) + 1
        item = json.loads(row[0])
        body = base64.b64decode(item["body"])
        if item.get("body_codec") == "zlib":
            body = zlib.decompress(body)
        if "sha256:" + hashlib.sha256(body).hexdigest() != item["content_hash"]:
            raise ValueError("Preparation checkpoint content hash mismatch")
        return SourceResponse(
            item["url"],
            body,
            datetime.fromisoformat(item["observed_at"]),
            item["content_hash"],
        )
    if catalog.transport.request_count >= limit:
        raise BatchComplete
    try:
        response = await catalog.transport.fetch(url)
    except PartsError as exc:
        if exc.code == "preparation_batch_complete":
            raise BatchComplete from exc
        raise
    item = {
        "url": response.url,
        "body": base64.b64encode(zlib.compress(response.body)).decode(),
        "body_codec": "zlib",
        "observed_at": response.observed_at.isoformat(),
        "content_hash": response.content_hash,
    }
    with connection:
        connection.execute("INSERT INTO preparation VALUES (?, ?)", (url, json.dumps(item)))
    print(f"Preparation HTTP attempts: {catalog.transport.request_count}; checkpointed {url}", flush=True)
    return response


def catalog_groups(body, machine):
    doc = parse.document(body)
    groups = doc.xpath("//*[@data-functionalgroupcode]/@data-functionalgroupcode")
    series = doc.xpath('//*[@id="bread-in-model-page"]/@data-serie-code')
    if len(series) != 1 or len(groups) != len(set(groups)):
        raise ValueError("Expected one series and unique catalog groups")
    # Parse the catalog heading and group ownership even when there are no groups.
    parsed = parse.groups(body, machine["id"])
    if {g["group_ref"]["$ref"]["id"] for g in parsed["groups"]} != set(groups):
        raise ValueError("Catalog group observations disagree")
    return groups, series[0], parsed["groups"]


async def prepare_catalog(catalog, machine, limit):
    storefront = get_storefront(machine["source_id"].removesuffix("-us-en"))
    connection = catalog.cache.connection
    directory = navigation.read(connection, machine)
    identity = (machine["source_id"], machine["id"])
    if (
        directory
        and any(e["stale"] for e in directory["entries"])
        and not connection.execute(
            "SELECT 1 FROM navigation_refresh WHERE source=? AND catalog=?", identity
        ).fetchone()
    ):
        # Invalidate once, not on every resume. Preserve the last published directory
        # until all refreshed groups can replace it atomically.
        with connection:
            for item in directory["evidence"]:
                url = item["source_url"]
                if "/findAssembly/" in url or url == parse.ORIGIN + machine["path"]:
                    connection.execute("DELETE FROM preparation WHERE url=?", (url,))
            connection.execute("INSERT INTO navigation_refresh VALUES (?, ?)", identity)
    response = await checkpoint_fetch(catalog, parse.ORIGIN + machine["path"], limit)
    groups, series, labels = catalog_groups(response.body, machine)
    entries, provenance = [], [evidence(response)]
    for group in groups:
        url = storefront.home + "/cn/findAssembly/functionalGroupCode/" + quote(group, safe="")
        url += "/" + quote(series, safe="")
        response = await checkpoint_fetch(catalog, url, limit)
        entries.extend(navigation.from_rows(machine, json.loads(response.body), group, storefront))
        provenance.append(evidence(response))
    navigation.publish(
        catalog.cache.connection,
        machine,
        entries,
        expand_references(labels, storefront.source_id),
        provenance,
        complete=True,
    )


def import_snapshots(catalog, root):
    """Validate saved source provenance and reconstruct routes; never rewrite historical files."""
    imported = 0
    for folder in sorted(root.glob("*/contents.json")):
        snapshot = json.loads(folder.read_text(encoding="utf-8"))
        machine = snapshot["machine"]
        storefront = get_storefront(machine["source_id"].removesuffix("-us-en"))
        page = folder.parent / "machine.html"
        source = json.loads((folder.parent / "machine-source.json").read_text(encoding="utf-8"))
        if source["url"] != parse.ORIGIN + machine["path"]:
            raise ValueError("Snapshot machine provenance mismatch")
        if source["content_hash"] != "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest():
            raise ValueError("Snapshot machine hash mismatch")
        groups, series, labels = catalog_groups(page.read_bytes(), machine)
        entries, provenance = (
            [],
            [
                {
                    "source_url": source["url"],
                    **{k: source[k] for k in ("observed_at", "content_hash")},
                    "parser_version": parse.PARSER_VERSION,
                }
            ],
        )
        for i, group in enumerate(groups):
            item = json.loads((folder.parent / f"group-{i:02d}.json").read_text(encoding="utf-8"))
            url = storefront.home + "/cn/findAssembly/functionalGroupCode/" + quote(group, safe="")
            url += "/" + quote(series, safe="")
            if item["url"] != url:
                raise ValueError("Snapshot group provenance mismatch")
            entries.extend(navigation.from_rows(machine, item["rows"], group, storefront))
            # Captures retained parsed JSON, not raw bytes: retain original hash without
            # claiming it was reverified against a reserialized representation.
            provenance.append(
                {
                    "source_url": item["url"],
                    "observed_at": item["observed_at"],
                    "content_hash": item["content_hash"],
                    "parser_version": parse.PARSER_VERSION,
                }
            )
        old = {(json.dumps(e["ref"], sort_keys=True), e["title"], e["group"]) for e in snapshot["entries"]}
        if old != {(json.dumps(e["assembly_ref"], sort_keys=True), e["title"], e["group"]) for e in entries}:
            raise ValueError("Snapshot contents do not match captured group rows")
        navigation.publish(
            catalog.cache.connection,
            machine,
            entries,
            expand_references(labels, storefront.source_id),
            provenance,
            complete=True,
        )
        imported += 1
    return imported


async def run(args):
    interval_min = getattr(args, "interval_min", 1)
    interval_max = getattr(args, "interval_max", 1.25)
    if not 1 <= interval_min <= interval_max < 60:
        raise ValueError("Preparation intervals must satisfy 1 <= minimum <= maximum < 60")
    settings = Settings()
    catalog = Catalog(settings)
    catalog.transport.jitter = lambda: random.uniform(interval_min, interval_max) - 1
    catalog.transport.request_limit = args.max_requests
    report = {"http_attempts": 0, "brands": {}, "complete": False}
    report["request_interval_seconds"] = [interval_min, interval_max]
    report["queued_brands"] = [brand for stage in PREPARATION_STAGES for brand in stage]

    try:
        if args.snapshots:
            report["imported_snapshots"] = import_snapshots(catalog, args.snapshots)
        for stage in PREPARATION_STAGES:
            for brand in stage:
                storefront = get_storefront(brand)
                records = {
                    r["machine"]["machine_ref"]["$ref"]["id"]: r
                    for r in catalog.indexes.get(brand, {}).get("records", [])
                }
                status = report["brands"][brand] = {
                    "discovered_catalogs": 0,
                    "prepared_catalogs": 0,
                    "incomplete_catalogs": 0,
                    "sitemap_exhausted": False,
                }
                try:
                    response = await checkpoint_fetch(
                        catalog, storefront.home + "/sitemap-equipment", args.max_requests
                    )
                    pending, completed = sitemap_series(response.body, storefront), set()
                    series_counts, series_totals = {}, {}
                    while pending:
                        url = pending.pop(0)
                        response = await checkpoint_fetch(catalog, url, args.max_requests)
                        total, machines, pages = series_page(response.body, url, storefront)
                        base = url.split("?", 1)[0]
                        series_totals[base] = total
                        series_counts.setdefault(base, set()).update(
                            m["machine_ref"]["$ref"]["id"] for m in machines
                        )
                        for machine in machines:
                            records[machine["machine_ref"]["$ref"]["id"]] = {
                                "machine": machine,
                                "evidence": evidence(response),
                                "discovery_query": "sitemap:" + url,
                            }
                        completed.add(url)
                        pending.extend(p for p in pages if p not in completed and p not in pending)
                    status["sitemap_exhausted"] = True
                    status["series_total_mismatches"] = [
                        {"url": url, "displayed_total": total, "unique_catalogs": len(series_counts[url])}
                        for url, total in series_totals.items()
                        if total != len(series_counts[url])
                    ]
                finally:
                    status["discovered_catalogs"] = len(records)
                    status["prepared_catalogs"] = sum(
                        bool(
                            (
                                navigation.read(
                                    catalog.cache.connection, {"source_id": storefront.source_id, "id": key}
                                )
                                or {}
                            ).get("complete")
                        )
                        for key in records
                    )
                    status["incomplete_catalogs"] = len(records) - status["prepared_catalogs"]
                    if records:
                        index = {
                            "schema_version": 1,
                            "source_id": storefront.source_id,
                            "coverage": "partial: successfully traversed sitemap routes; see preparation report",
                            "updated_at": max(r["evidence"]["observed_at"] for r in records.values()),
                            "records": sorted(records.values(), key=lambda r: r["machine"]["label"]),
                        }
                        validate_index(index)
                        atomic_json(args.index_dir / f"{brand}.json", index)
            # Finish this stage before discovering or preparing the next storefront.
            for brand in stage:
                storefront = get_storefront(brand)
                index = json.loads((args.index_dir / f"{brand}.json").read_text(encoding="utf-8"))
                status = report["brands"][brand]
                for record in index["records"]:
                    machine = expand_references(record["machine"], storefront.source_id)["machine_ref"]
                    directory = navigation.read(catalog.cache.connection, machine)
                    if not directory or not directory["complete"]:
                        await prepare_catalog(catalog, machine, args.max_requests)
                        status["prepared_catalogs"] += 1
                        status["incomplete_catalogs"] -= 1
        report["complete"] = True
    except BatchComplete:
        report["stopped"] = "batch request allowance reached; rerun to resume"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["http_attempts"] = catalog.transport.request_count
        report["reused_checkpoint_responses"] = getattr(catalog, "preparation_reused", 0)
        report["scope"] = (
            "Observed sitemap routes and retained historical catalogs; not CNH-wide completeness"
        )
        atomic_json(args.report, report)
        print(json.dumps(report, indent=2), flush=True)
        await catalog.aclose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-requests", type=int, default=250)
    parser.add_argument("--index-dir", type=Path, default=Path(".cache/navigation-indexes"))
    parser.add_argument("--report", type=Path, default=Path(".cache/navigation-preparation.json"))
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--interval-min", type=float, default=1)
    parser.add_argument("--interval-max", type=float, default=1.25)
    args = parser.parse_args()
    if args.max_requests < 1:
        parser.error("--max-requests must be positive")
    load_dotenv()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
