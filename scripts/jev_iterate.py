"""Run the Jev lookup over a frozen input manifest. Inference never reads answer keys.

Earlier staged configurations are preserved with their code snapshots under reports/; this file runs only
the current lookup. Rerunning an old stage means using that stage's saved code, not this module.
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from cnh_parts_agent.agent.jev_applicability import scope_to_year
from cnh_parts_agent.agent.jev_navigation import (
    NAVIGATION_PROMPT,
    READER_PROMPT,
    SEARCH_PROMPT,
    CandidateReading,
    SearchPlan,
    filter_candidates,
    label_rows,
    navigate,
)
from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.catalog.operations import execute, validate_reference
from cnh_parts_agent.catalog.storefront import get_storefront
from cnh_parts_agent.catalog.transport import SourceResponse, SourceTransport
from cnh_parts_agent.settings import Settings
from scripts.jev_pilot import JEV_MODEL, READER_MODEL, digest, dump, fetch, load

# Jev rejects oversized menus: 40k label characters returned 400, 23.7k was accepted.
MENU_LABEL_LIMIT = 24_000
MENU_OPTION_LIMIT = 255
DEFAULT_ROOT = Path("reports/jev-iterations-20261105")
CODE_PATHS = [Path(__file__), Path("src/cnh_parts_agent/agent/jev_navigation.py"),
              Path("src/cnh_parts_agent/catalog/reader_html.py"), Path("scripts/jev_pilot.py"),
              Path("src/cnh_parts_agent/agent/jev_applicability.py")]


async def model_call(kind, request, schema, *, case_folder, cache_folder, fresh, jev, reader, calls):
    """Reuse only byte-equivalent inputs/schema/model; distinguish logical from newly incurred usage."""
    payload = {"request": request, "schema": schema.model_json_schema() if schema else None, "kind": kind}
    key = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
    stem = case_folder / "calls" / f"{len(calls) + 1:03d}-{kind}"
    request_path = stem.with_suffix(".request.json")
    response_path = stem.with_suffix(".response.json")
    cache_path = cache_folder / (key + ".json")
    if response_path.exists():
        if load(request_path) != payload:
            raise ValueError("Resume would change a recorded model input")
        record = load(response_path)
    else:
        if request_path.exists():
            raise RuntimeError("Unresolved paid request checkpoint; inspect before repeating")
        dump(request_path, payload)
        if not fresh and cache_path.exists():
            record = {**load(cache_path), "reused": True}
        else:
            started = time.perf_counter()
            if kind == "jev":
                response = await jev.post("https://api.typesafe.ai/v1/systemone", json=request)
                response.raise_for_status()
                result = response.json()
            else:
                response = await reader.responses.parse(**request, text_format=schema)
                result = response.model_dump(mode="json")
            record = {"response": result, "elapsed_seconds": time.perf_counter() - started,
                      "reused": False, "cache_key": key}
            if not fresh and not cache_path.exists():
                dump(cache_path, record)
        dump(response_path, record)
    calls.append({"kind": kind, "record": str(response_path.relative_to(case_folder)),
                  "usage": record["response"]["usage"], "reused": record["reused"],
                  "elapsed_seconds": record["elapsed_seconds"]})
    if kind == "jev":
        return record["response"]
    result = record["response"]
    outputs = [c["text"] for item in result["output"] if item["type"] == "message"
               for c in item["content"] if c["type"] == "output_text"]
    if result["status"] != "completed" or len(outputs) != 1:
        raise ValueError("Reader did not produce one complete structured response")
    return schema.model_validate_json(outputs[0]).model_dump()


def reader_request(prompt, data):
    return {"model": READER_MODEL, "reasoning": {"effort": "high"}, "max_output_tokens": 8192,
            "store": False, "service_tier": "default",
            "input": [{"role": "system", "content": prompt},
                      {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]}


def option_label(item):
    prefix = {"group": "Open group: ", "guide": "Inspect guide: ", "answer": "Stop: "}
    label = prefix[item["kind"]] + item["label"]
    if item.get("cross_references"):
        label += " | Explicit source reference: " + json.dumps(item["cross_references"], ensure_ascii=False)
    if item.get("search_matches"):
        label += " | Observed matching descriptions: " + json.dumps(item["search_matches"], ensure_ascii=False)
    return label


def menu_partitions(options):
    """Split a menu Jev would reject; every observed option stays in play through a tournament."""
    partitions, chunk, size = [], [], 0
    for option in options:
        length = len(option_label(option))
        if chunk and (len(chunk) >= MENU_OPTION_LIMIT or size + length > MENU_LABEL_LIMIT):
            partitions.append(chunk)
            chunk, size = [], 0
        chunk.append(option)
        size += length
    return [*partitions, chunk]


def supplied_year(case):
    supplied = case["machine_query"].rsplit(" ", 1)[-1]
    return int(supplied) if supplied.isdigit() and len(supplied) == 4 else None


async def evaluate_case(case, folder, root, config, transport, fetch_lock, jev, reader):
    case_folder = folder / "cases" / case["id"]
    result_path = case_folder / "result.json"
    if result_path.exists():
        return load(result_path)
    calls, source_events = [], []
    started = time.perf_counter()

    async def source(url):
        async with fetch_lock:
            before = transport.request_count
            body, meta = await fetch(transport, root, url)
            source_events.append({"url": url, "http_attempts": transport.request_count - before})
            return body, meta

    async def invoke(kind, request, schema=None):
        return await model_call(kind, request, schema, case_folder=case_folder,
                                cache_folder=root / "model-cache", fresh=config["fresh_inference"],
                                jev=jev, reader=reader, calls=calls)

    async def choose(state, options):
        partitions = menu_partitions(options)
        if len(partitions) > 1:
            winners = [(await choose({**state, "menu_partition": [i + 1, len(partitions)]}, part))["selected"]
                       for i, part in enumerate(partitions)]
            return await choose({**state, "partition_winners": True}, winners)
        if len(options) == 1:
            return {"selected": options[0], "probability": 1, "automatic": True}
        keyed = {f"option_{i:03d}": item for i, item in enumerate(options, 1)}
        labels = {key: option_label(item) for key, item in keyed.items()}
        request = {"model": JEV_MODEL, "state": state,
                   "questions": {"next_option": {"type": "choice", "instructions": NAVIGATION_PROMPT,
                                                  "criteria": labels}}}
        result = await invoke("jev", request)
        answer = result["answers"]["next_option"]
        if result["model"] != JEV_MODEL or answer["choice"] not in keyed:
            raise ValueError("Unexpected Jev model or choice")
        if set(answer["probabilities"]) != set(keyed):
            raise ValueError("Incomplete Jev option probabilities")
        return {"selected": keyed[answer["choice"]], "probability": answer["probabilities"][answer["choice"]],
                "option_count": len(options), "confidence": answer["confidence"],
                "ranking": [{"label": labels[k], "probability": v}
                            for k, v in sorted(answer["probabilities"].items(), key=lambda x: -x[1])]}

    async def read(case, guide, group, cleaned, rows, ordinal):
        cleaned = label_rows(cleaned, rows)
        html_path = case_folder / "pages" / f"{ordinal:02d}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        if html_path.exists() and html_path.read_text(encoding="utf-8") != cleaned:
            raise ValueError("Page changed during resume")
        html_path.write_text(cleaned, encoding="utf-8")
        data = {"machine": case["machine"]["label"], "original_question": case["query"],
                "navigation_path": [group["label"], guide["label"]], "source_url": guide["url"], "html": cleaned}
        reading = await invoke("reader", reader_request(READER_PROMPT, data), CandidateReading)
        validation_failures = []
        for attempt in range(2):
            try:
                filter_candidates(reading, rows, guide, case["query"])
                break
            except ValueError as exc:
                validation_failures.append({"error": str(exc), "reading": reading})
                if attempt == 1:
                    return {"status": "not_found", "findings": [],
                            "explanation": "Reader candidates failed source-evidence validation after one correction attempt; no candidate accepted.",
                            "unresolved_questions": [], "missing_evidence": [str(exc)],
                            "next_assembly_concepts": reading["next_assembly_concepts"],
                            "validation_failures": validation_failures}
                feedback = {**data, "previous_reading": reading, "validation_error": str(exc),
                            "exact_source_rows": {key: row.get("reader_row_text", row["description"])
                                                  for key, row in rows.items()},
                            "correction_instruction": "Re-read the evidence. Fix invalid row IDs/quotes using exact source text; remove unsupported candidates. Row position is not diagram reference."}
                reading = await invoke("reader-correction", reader_request(READER_PROMPT, feedback),
                                       CandidateReading)
        answer = {**filter_candidates(reading, rows, guide, case["query"]),
                  "validation_failures": validation_failures}
        return scope_to_year(answer, supplied_year(case))

    async def search(state):
        search_plan = await invoke("search-plan", reader_request(SEARCH_PROMPT, state), SearchPlan)
        queries = list(dict.fromkeys(search_plan["queries"]))
        if not 1 <= len(queries) <= 3 or any(not q.strip() or len(q) > 80 for q in queries):
            raise ValueError("Search plan exceeds its bounded query allowance")
        storefront = get_storefront(case["brand"])
        sources, coverage, guides = [], [], {}

        async def operation_fetch(url):
            body, meta = await source(url)
            sources.append(meta)
            return SourceResponse(meta["url"], body, datetime.fromisoformat(meta["observed_at"]),
                                  "sha256:" + meta["sha256"])

        for query in queries:
            found, error = await checked_catalog_search({"source_id": storefront.source_id,
                                   "machine_ref": case["machine"]["machine_ref"],
                                   "mode": "part_description", "query": query},
                                   operation_fetch, storefront)
            if error:
                coverage.append({"status": "failed", "query": query, "error": error})
                continue
            coverage.append(found.coverage)
            for row in expand_references(found.data, storefront.source_id)["matches"]:
                ref = row["assembly_ref"]
                validate_reference(ref, storefront=storefront)
                guide = guides.setdefault(ref["id"], {"label": row["assembly_label"], "ref": ref,
                                          "url": row["assembly_url"], "group": "Native catalog search",
                                          "search_matches": []})
                # The usage note often holds the distinguishing words: COVER; Grille Remote.
                matched = "; ".join([row["description"], *row.get("notes", [])])
                if matched not in guide["search_matches"]:
                    guide["search_matches"].append(matched)
        return {"queries": queries, "rationale": search_plan["rationale"],
                "coverage": coverage, "guides": list(guides.values()), "sources": sources}

    try:
        result = await navigate(case, config=config, fetch=source, choose=choose, read=read, search=search)
    except asyncio.CancelledError:
        dump(result_path, {"id": case["id"], "status": "cancelled", "calls": calls,
                           "source_events": source_events, "error": "Interrupted; in-flight request usage may be unavailable"})
        raise
    except Exception as exc:
        message = str(exc)
        for key in ("OPENAI_API_KEY", "JEV_API_KEY"):
            if os.getenv(key):
                message = message.replace(os.environ[key], "[redacted]")
        dump(result_path, {"id": case["id"], "status": "error", "error": message,
                           "calls": calls, "source_events": source_events})
        raise
    result.update({"id": case["id"], "query": case["query"], "calls": calls,
                   "source_events": source_events, "latency_seconds": time.perf_counter() - started})
    dump(result_path, result)
    print(json.dumps({"stage": folder.name, "id": case["id"], "stop": result["stop_reason"],
                      "guides": len(result["inspections"]), "decisions": len(result["decisions"]),
                      "parts": [p["part_number"] for p in result["answer"]["findings"]]}), flush=True)
    return result


async def checked_catalog_search(arguments, fetch, storefront):
    """An invalid optional search is an explicit coverage failure, never an empty result."""
    try:
        return await execute("search_catalog", arguments, fetch, storefront=storefront), None
    except PartsError as exc:
        if exc.code != "parser_schema_changed":
            raise
        return None, exc.payload()["error"]


async def run(root, stage):
    folder = root / stage
    manifest = load(folder / "manifest.json")
    if manifest["code_sha256"] != {str(p): digest(p.read_bytes()) for p in CODE_PATHS}:
        raise ValueError("Frozen stage code changed; prepare a new stage instead of mixing versions")
    settings = Settings()
    transport = SourceTransport(settings.user_agent, settings=settings)
    lock, semaphore = asyncio.Lock(), asyncio.Semaphore(2)
    try:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + os.environ["JEV_API_KEY"]},
                                     timeout=45, follow_redirects=False) as jev:
            async with AsyncOpenAI(max_retries=0, timeout=90) as reader:
                async def execute_case(case):
                    async with semaphore:
                        return await evaluate_case(case, folder, root, manifest["config"],
                                                   transport, lock, jev, reader)
                async with asyncio.TaskGroup() as tasks:
                    for case in manifest["cases"]:
                        tasks.create_task(execute_case(case))
    finally:
        await transport.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    load_dotenv(".env")
    asyncio.run(run(args.root, args.stage))
