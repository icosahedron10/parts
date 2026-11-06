"""Mechanical evidence checks; these do not establish semantic correctness."""

import re
from typing import Any

from cnh_parts_agent.agent.handoff import _URL_TOKEN, _url_without_prose_suffix

PART_TOKEN = re.compile(
    "(?<![\\w./-])(?:[A-Z]{1,4}\\d{4,}[A-Z]?|\\d{6,}[A-Z]?|\\d{3,4}-\\d{1,5})(?![\\w./-])", re.IGNORECASE
)


def source_urls(value: Any) -> set[str]:
    """Collect URL values only, never URLs invented inside narrative source text."""
    urls: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_url", "assembly_url", "product_url", "store_url", "sources", "diagram_urls"}:
                candidates = item if isinstance(item, list) else [item]
                urls.update(
                    (
                        url
                        for url in candidates
                        if isinstance(url, str) and url.startswith(("https://", "http://"))
                    )
                )
            elif isinstance(item, (dict, list)):
                urls.update(source_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.update(source_urls(item))
    return urls


def answer_urls(text: str, observed: set[str] | None = None) -> set[str]:
    """Extract answer links while removing prose punctuation without truncating observed source URLs."""
    return {
        _url_without_prose_suffix(match[0], observed or set(), text[: match.start()])
        for match in re.finditer(_URL_TOKEN, text)
    }


def mentions(text: str, number: str) -> bool:
    """Detect a complete part-number token in prose, excluding numbers that occur only inside URLs."""
    prose = re.sub(_URL_TOKEN, "", text)
    return bool(re.search("(?<![\\w-])" + re.escape(number) + "(?![\\w-])", prose, re.IGNORECASE))


def qualifier_numbers(value: Any) -> set[str]:
    """Observed catalog/configuration codes must not be mistaken for new OEM parts."""
    numbers: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"label", "assembly_label", "qualifications"}:
                strings = item if isinstance(item, list) else [item]
                for text in strings:
                    if isinstance(text, str):
                        numbers.update((match[0].upper() for match in PART_TOKEN.finditer(text)))
            elif isinstance(item, (dict, list)):
                numbers.update(qualifier_numbers(item))
    elif isinstance(value, list):
        for item in value:
            numbers.update(qualifier_numbers(item))
    return numbers


def assembly_occurrences(observations: list[dict]) -> list[dict]:
    """Collect occurrence rows only from successful assembly tool observations."""
    return [
        row
        for observation in observations
        if observation.get("operation") == "get_assembly"
        and observation.get("response", {}).get("outcome") in {"ok", "partial"}
        for row in observation["response"].get("data", {}).get("occurrences", [])
    ]


def evidence_gate(answer: str, observations: list[dict], prior_handoffs: list[dict] | None = None) -> dict:
    """Fail closed on known evidence violations; no pass claims semantic accuracy."""
    successful = [
        item["response"]
        for item in observations
        if isinstance(item.get("response"), dict) and "error" not in item["response"]
    ]
    observed_urls = source_urls(successful) | source_urls(prior_handoffs or [])
    citations = answer_urls(answer, observed_urls)
    occurrences = assembly_occurrences(observations)
    vocabulary = {row["part_number"].upper() for row in occurrences if row.get("part_number")}
    for response in successful:
        vocabulary.update(
            (
                row["part_number"].upper()
                for row in response.get("data", {}).get("matches", [])
                if row.get("part_number")
            )
        )
    proposed = {number for number in vocabulary if mentions(answer, number)}
    inferred = {match[0].upper() for match in PART_TOKEN.finditer(re.sub(_URL_TOKEN, "", answer))}
    proposed.update(inferred - qualifier_numbers(successful))
    prior_text = "\n".join((fact for item in prior_handoffs or [] for fact in item["handoff"]["key_facts"]))
    prior_numbers = {number for number in proposed if mentions(prior_text, number)}
    verified = {row["part_number"].upper() for row in occurrences if row.get("part_number")}
    relationship_numbers: set[str] = set()
    for observation in observations:
        response = observation.get("response", {})
        if observation.get("operation") != "get_part_relationships" or "error" in response:
            continue
        for relationship in response.get("data", {}).get("relationships", []):
            original = relationship.get("from_part_number", "").upper()
            target = relationship.get("to_part_number", "").upper()
            if relationship.get("kind") != "replacement" or original not in verified or (not target):
                continue
            if mentions(answer, target):
                proposed.add(target)
            if source_urls(response) & citations and any(
                (
                    row.get("part_number", "").upper() == original and row.get("assembly_url") in citations
                    for row in occurrences
                )
            ):
                relationship_numbers.add(target)
    violations = []
    unknown = sorted(citations - observed_urls)
    if unknown:
        violations.append({"code": "unobserved_citation", "values": unknown})
    unsupported = sorted(proposed - verified - prior_numbers - relationship_numbers)
    if unsupported:
        violations.append({"code": "part_without_assembly_evidence", "values": unsupported})
    missing_links = sorted(
        (
            number
            for number in proposed & verified
            if not any(
                (
                    row.get("part_number", "").upper() == number and row.get("assembly_url") in citations
                    for row in occurrences
                )
            )
        )
    )
    if missing_links:
        violations.append({"code": "missing_part_assembly_citation", "values": missing_links})
    return {
        "passed": not violations,
        "violations": violations,
        "proposed_part_numbers": sorted(proposed),
        "citations": sorted(citations),
        "semantic_correctness": "not_assessed",
    }
