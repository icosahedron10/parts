"""Durable observed route maps. Navigation dates never imply fresh parts evidence."""

import json
import re
from urllib.parse import urlsplit

from . import parse
from .models import AssemblyRoute, Evidence, Group
from .operations import validate_reference
from .storefront import get_storefront

FIGURE_REFERENCE = re.compile(
    r"\bfig(?:ure)?s?\.?\s+(\d+(?:-\d+)*)(?![\w.\[-])"
    r"(?:\s+(?:through|to)\s+(\d+(?:-\d+)*)(?![\w.\[-]))?",
    re.I,
)


def figure_routes(directory, occurrences):
    """Resolve explicit source figure references against observed titles, without ranking choices."""
    if not directory:
        return []
    ranges = set()
    for row in occurrences:
        text = " ".join([row["description"], *row.get("notes", [])])
        for match in FIGURE_REFERENCE.finditer(text):
            start = tuple(int(n) for n in match[1].split("-"))
            end = tuple(int(n) for n in (match[2] or match[1]).split("-"))
            if start[:-1] == end[:-1] and start <= end:
                ranges.add((start, end))
    found = []
    for entry in directory["entries"]:
        match = re.match(r"^(\d+(?:-\d+)*)(?![\w.\[-])", entry["title"])
        if match:
            figure = tuple(int(n) for n in match[1].split("-"))
            if any(figure[:-1] == start[:-1] and start <= figure <= end for start, end in ranges):
                found.append(entry)
    return found


def initialize(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS navigation (
            source TEXT NOT NULL, catalog TEXT NOT NULL, directory TEXT NOT NULL,
            PRIMARY KEY (source, catalog));
        CREATE TABLE IF NOT EXISTS preparation (
            url TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS navigation_refresh (
            source TEXT NOT NULL, catalog TEXT NOT NULL, PRIMARY KEY (source, catalog));
    """)


def read(connection, machine):
    row = connection.execute(
        "SELECT directory FROM navigation WHERE source=? AND catalog=?",
        (machine["source_id"], machine["id"]),
    ).fetchone()
    if row is None:
        return None
    directory = json.loads(row[0])
    if machine.get("path"):
        prefix = machine["path"].rsplit("/cn/", 1)[0] + "/"
        if any(not e["assembly_ref"]["path"].startswith(prefix) for e in directory["entries"]):
            return None
    return directory


def publish(connection, machine, entries, groups, evidence, *, complete):
    """Validate before replacing a directory in one SQLite transaction."""
    storefront = get_storefront(machine["source_id"].removesuffix("-us-en"))
    validate_reference(machine, storefront=storefront)
    unique = {}
    prefix = machine["path"].rsplit("/cn/", 1)[0] + "/"
    for entry in entries:
        AssemblyRoute.model_validate(entry)
        ref = entry["assembly_ref"]
        validate_reference(ref, storefront=storefront)
        if ref["context"] != machine["id"] or not ref["path"].startswith(prefix):
            raise ValueError("Navigation assembly escaped its catalog")
        if parse.source_link(entry["assembly_url"], storefront=storefront) != parse.ORIGIN + ref["path"]:
            raise ValueError("Navigation URL and reference disagree")
        if ref["id"] in unique and unique[ref["id"]] != entry:
            raise ValueError("Conflicting navigation identity")
        unique[ref["id"]] = entry
    for group in groups:
        Group.model_validate(group)
        validate_reference(group["group_ref"], storefront=storefront)
        if group["group_ref"]["context"] != machine["id"]:
            raise ValueError("Navigation group escaped its catalog")
    for item in evidence:
        Evidence.model_validate(item)
        parse.source_link(item["source_url"], storefront=storefront)
    directory = {
        "entries": list(unique.values()),
        "groups": groups,
        "evidence": evidence,
        "complete": complete,
    }
    with connection:
        connection.execute(
            "INSERT OR REPLACE INTO navigation VALUES (?, ?, ?)",
            (machine["source_id"], machine["id"], json.dumps(directory)),
        )
        if complete:
            connection.execute(
                "DELETE FROM navigation_refresh WHERE source=? AND catalog=?",
                (machine["source_id"], machine["id"]),
            )


def from_rows(machine, rows, group, storefront):
    if not isinstance(rows, list):
        raise ValueError("Expected a complete assembly group array")
    entries = []
    for row in rows:
        if row["modelCode"] != machine["id"] or row["functionalGroupCode"] != group:
            raise ValueError("Assembly escaped its catalog/group")
        url = parse.assembly_url(machine["path"], row, storefront=storefront)
        entries.append(
            {
                "assembly_ref": {
                    "source_id": storefront.source_id,
                    "kind": "assembly",
                    "id": row["assemblyCode"],
                    "context": machine["id"],
                    "path": urlsplit(url).path,
                },
                "title": row["assemblyFullName"],
                "group": group,
                "figure": row["assemblyFullName"].split(" - ", 1)[0],
                "observed_path": row["assemblyUrl"],
                "assembly_url": url,
                "stale": False,
            }
        )
    return entries


def retain_matches(connection, machine, matches, evidence):
    directory = read(connection, machine) or {"entries": [], "groups": [], "evidence": [], "complete": False}
    entries = {e["assembly_ref"]["id"]: e for e in directory["entries"]}
    complete = directory["complete"]
    for row in matches:
        ref = row["assembly_ref"]
        old = entries.get(ref["id"], {})
        same_route = old.get("assembly_ref", {}).get("path") == ref["path"]
        complete = complete and same_route
        entries[ref["id"]] = {
            "assembly_ref": ref,
            "title": row.get("assembly_label") or row["label"],
            "group": old.get("group", "") if same_route else "",
            "figure": old.get("figure") if same_route else None,
            "observed_path": old.get("observed_path") if same_route else None,
            "assembly_url": row["assembly_url"],
            "stale": False,
        }
    provenance = directory["evidence"] + [e for e in evidence if e not in directory["evidence"]]
    publish(
        connection,
        machine,
        list(entries.values()),
        directory["groups"],
        provenance,
        complete=complete,
    )


def mark_stale(connection, machine, ref):
    directory = read(connection, machine)
    if directory:
        for entry in directory["entries"]:
            if entry["assembly_ref"] == ref:
                entry["stale"] = True
        publish(
            connection,
            machine,
            directory["entries"],
            directory["groups"],
            directory["evidence"],
            complete=False,
        )
