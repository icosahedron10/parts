"""Host-computed lexical bookkeeping that keeps catalog research from stopping early.

The catalog search is lexical. A nonempty page can still be off target (seat belts for an
engine drive belt, engine air cleaners for a cab filter), and an inspected parent assembly can
redirect to another figure. This ledger never guesses part identity. It reports which supplied
request words the observed rows lack, which search routes remain untried, which observed
assemblies share the request but remain uninspected, and the remaining lookup budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

STOPWORDS = {
    "a", "an", "and", "any", "do", "for", "i", "in", "is", "it", "machine", "my", "need", "needs",
    "new", "of", "on", "one", "or", "part", "parts", "replacement", "the", "this", "to", "two",
    "what", "which", "with",
}  # fmt: skip
PREPOSITIONS = {"for", "of", "on", "in", "to"}
GENERIC = {"assembly", "assy", "complete", "kit", "kits", "set"}
UNSUITABLE = re.compile(r"\b(?:PICTORIAL INDEX|DIA KIT|STOCKING)\b", re.IGNORECASE)
REFERENCE = re.compile(r"\b(?:see|refer to|parts list)\b", re.IGNORECASE)
SIDES = (
    (re.compile(r"\b(?:right[\s-]*(?:hand|side)|r\.?\s?h\.?)(?![a-z])", re.IGNORECASE), " rh "),
    (re.compile(r"\b(?:left[\s-]*(?:hand|side)|l\.?\s?h\.?)(?![a-z])", re.IGNORECASE), " lh "),
)
# A symptom description ('oil leaking underneath') is a diagnosis request, not a component noun.
SYMPTOMS = {
    "broke", "broken", "cracked", "grinding", "leak", "leaking", "leaks", "noise", "noisy", "overheating",
    "smoke", "smoking", "squeal", "squealing", "stuck", "underneath", "vibrating", "vibration", "worn",
}  # fmt: skip
IRREGULAR = {"teeth": "tooth", "knives": "knife"}
MAX_ROUTES = 3


def _stem(word: str) -> str:
    if word in IRREGULAR:
        return IRREGULAR[word]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    for pattern, replacement in SIDES:
        text = pattern.sub(replacement, text)
    return [_stem(word) for word in re.findall(r"[a-z0-9]+", text.casefold())]


@dataclass(frozen=True)
class Request:
    """One requested component noun and the supplied words that qualify it."""

    component: str
    qualifiers: tuple[str, ...]
    lead: str | None  # supplied word immediately before the component, e.g. 'air' in 'air filter'
    generic: str | None  # trailing kit/assembly word, e.g. 'kit' in 'seal kit'
    parent: str | None  # phrase after 'for'/'of', e.g. 'swing motor'


def parse_requests(part_query: str) -> list[Request]:
    """Split a focused part query into component nouns and qualifiers without inferring identity."""
    # Appended instructions ('; order two', ': exact price', '. Ignore ...') are not component words.
    focus = re.split(r"[;:?]|\.\s", part_query, maxsplit=1)[0]
    if SYMPTOMS & set(re.findall(r"[a-z]+", focus.casefold())) or re.search(
        r"\bpart\s*(?:number|no\b|#)", focus, re.IGNORECASE
    ):
        return []
    requests = []
    for phrase in re.split(r"\s+and\s+|,", focus):
        words = _tokens(phrase)
        split = next((index for index, word in enumerate(words) if word in PREPOSITIONS), len(words))
        left = [word for word in words[:split] if word not in STOPWORDS]
        right = [word for word in words[split + 1 :] if word not in STOPWORDS]
        if not left:
            left, right = right, []
        nouns = [word for word in left if word not in GENERIC]
        if not nouns:
            continue
        component = nouns[-1]
        if any(character.isdigit() for character in component):
            continue  # an identifier or dimension, not a component noun
        position = len(left) - 1 - left[::-1].index(component)
        generic = left[position + 1] if position + 1 < len(left) else None
        qualifiers = tuple(dict.fromkeys(word for word in left + right if word != component))
        requests.append(
            Request(
                component=component,
                qualifiers=qualifiers,
                lead=left[position - 1] if position else None,
                generic=generic,
                parent=" ".join(right) or None,
            )
        )
    # A component shared by every phrase ('engine air filter and fuel filter') still keeps
    # each phrase's qualifiers separate, so 'fuel' is never demanded from an air-filter row.
    return requests


def _row_texts(operation: str, row: dict[str, Any], assembly_label: str) -> tuple[set[str], set[str]]:
    """Return (component evidence, qualifier evidence) word sets for one returned row."""
    if operation == "get_assembly":
        # An assembly title naming 'master cylinders' does not make any row a master cylinder.
        component_text = " ".join([row.get("description") or "", *row.get("notes", [])])
    elif row.get("kind") == "assembly":
        component_text = " ".join([row.get("assembly_label") or "", row.get("label") or ""])
    else:
        component_text = " ".join(
            [row.get("label") or "", row.get("description") or "", *row.get("notes", [])]
        )
    context = " ".join([component_text, row.get("assembly_label") or "", assembly_label])
    return set(_tokens(component_text)), set(_tokens(context))


def _reference_name(description: str) -> str | None:
    """Name the referenced component: 'INSTRUCTION; BRAKE ACTUATOR - see ...' -> 'brake actuator'."""
    head, _, body = description.partition(";") if ";" in description else ("", "", description)
    body = REFERENCE.split(body, maxsplit=1)[0].strip()
    name = re.split(r"\s+-\s+|,", body.strip(" -,"), maxsplit=1)[0]
    if body.startswith("-") and head and head.strip().upper() not in {"INSTRUCTION", "REF"}:
        # 'VALVE; - double check, parts list ...' modifies its head noun: 'double check valve'.
        name = f"{name} {head}"
    words = re.findall(r"[A-Za-z0-9]+", name)
    return " ".join(words).casefold() if 0 < len(words) <= 4 else None


@dataclass
class SearchLedger:
    """Per-lookup record of attempted routes, inspected assemblies and unmet request words."""

    part_query: str
    requests: list[Request] = field(init=False)
    tried: set[tuple[str, str]] = field(default_factory=set)
    inspected: set[str] = field(default_factory=set)
    missing: list[set[str]] = field(init=False)
    references: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.requests = parse_requests(self.part_query)
        self.missing = [set() for _ in self.requests]

    def reset(self) -> None:
        self.tried.clear()
        self.inspected.clear()
        for unmet in self.missing:
            unmet.clear()
        self.references.clear()

    def attempted(self, operation: str, arguments: dict[str, Any]) -> None:
        if operation == "search_catalog":
            self.tried.add((str(arguments.get("mode")), " ".join(_tokens(str(arguments.get("query", ""))))))
        elif operation == "get_assembly" and arguments.get("assembly_ref"):
            self.inspected.add(arguments["assembly_ref"])

    def guidance(
        self,
        operation: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
        *,
        assembly_labels: dict[str, str],
        remaining_calls: int,
        remaining_seconds: float,
    ) -> dict[str, Any] | None:
        """Summarize what this result does and does not establish, with untried routes."""
        if not self.requests or (operation == "search_catalog" and arguments.get("mode") == "part_number"):
            return None
        data = payload.get("data", {})
        rows = data.get("matches", data.get("occurrences", []))
        label = (
            assembly_labels.get(arguments.get("assembly_ref", ""), "") if operation == "get_assembly" else ""
        )
        texts = [_row_texts(operation, row, label) for row in rows]
        assessed = []
        for request, unmet in zip(self.requests, self.missing, strict=True):
            bearing = [context for words, context in texts if request.component in words]
            missing = [q for q in request.qualifiers if bearing and not any(q in c for c in bearing)]
            status = (
                "no_matches"
                if not rows
                else "component_absent"
                if not bearing
                else "qualifiers_unobserved"
                if missing
                else "candidates"
            )
            # A qualifier stays unmet until some component-bearing row carries it.
            unmet.difference_update(q for q in request.qualifiers if any(q in c for c in bearing))
            unmet.update(missing)
            assessed.append({"component": request.component, "status": status, "missing_qualifiers": missing})
        if operation == "get_assembly":
            self._collect_references(rows)
        guidance: dict[str, Any] = {
            "origin": "host lexical bookkeeping; not source text and not proof of identity or absence",
            "requested": assessed,
            "remaining_tool_calls": remaining_calls,
            "remaining_seconds": round(remaining_seconds),
            "note": self._note(operation, assessed),
        }
        if remaining_calls > 0 and any(item["status"] != "candidates" for item in assessed):
            searches = self._untried_searches()
            assemblies = self._uninspected_assemblies(assembly_labels)
            if searches:
                guidance["untried_searches"] = searches
            if assemblies:
                guidance["uninspected_assemblies"] = assemblies
        if operation == "get_assembly" and self.references:
            guidance["observed_cross_references"] = self.references[:MAX_ROUTES]
        if payload.get("truncated"):
            guidance["more_pages"] = (
                "Returned rows are one page; check further pages before claiming absence."
            )
        return guidance

    def _note(self, operation: str, assessed: list[dict[str, Any]]) -> str:
        statuses = {item["status"] for item in assessed}
        place = "this inspected assembly" if operation == "get_assembly" else "these results"
        if statuses == {"candidates"}:
            return "Rows contain the requested component and every supplied qualifier word. " + (
                "Verify the row's notes and conditions, then finish."
                if operation == "get_assembly"
                else "Inspect the matching assembly to verify the row, then finish."
            )
        parts = []
        if "no_matches" in statuses:
            parts.append(
                "This lexical search returned no rows; that is bounded evidence, not catalog-wide absence."
            )
        if "component_absent" in statuses:
            parts.append(
                f"No row in {place} contains the requested component word. Treat a row as the component only "
                "when its description or notes plainly name the same thing under a catalog synonym; never offer "
                "a different component as a substitute."
            )
        if "qualifiers_unobserved" in statuses:
            parts.append(
                f"Rows in {place} name the component but lack a supplied qualifier word. Establish that qualifier "
                "from an inspected row, its assembly or an equivalent catalog term before presenting a row as the "
                "requested component; otherwise report the qualifier as unverified, not as a likely match."
            )
        parts.append(
            "While calls remain, take an untried route (or a better one) before reporting the component "
            "unresolved or asking the user to repeat a supplied qualifier."
        )
        return " ".join(parts)

    def _untried_searches(self) -> list[dict[str, str]]:
        candidates: list[tuple[str, str]] = [
            ("assembly_title", reference["suggested_assembly_title"])
            for reference in self.references
            if reference.get("suggested_assembly_title")
        ]
        for request in self.requests:
            candidates.append(("part_description", request.component))
            if request.lead:
                candidates.append(("part_description", f"{request.lead} {request.component}"))
            if request.generic:
                candidates.append(("part_description", f"{request.component} {request.generic}"))
            if request.parent:
                candidates.append(("assembly_title", request.parent))
            candidates.append(("assembly_title", request.component))
        routes = []
        for mode, query in dict.fromkeys(candidates):
            if (mode, " ".join(_tokens(query))) not in self.tried:
                routes.append({"tool": "search_catalog", "mode": mode, "query": query})
        return routes[:MAX_ROUTES]

    def _uninspected_assemblies(self, assembly_labels: dict[str, str]) -> list[dict[str, str]]:
        scored = []
        for ref, label in assembly_labels.items():
            if ref in self.inspected or UNSUITABLE.search(label):
                continue
            words = set(_tokens(label))
            score = max(
                self._assembly_score(request, unmet, words)
                for request, unmet in zip(self.requests, self.missing, strict=True)
            )
            if score >= 3:
                scored.append((-score, len(scored), ref, label))
        return [
            {"assembly_ref": ref, "assembly_label": label} for _, _, ref, label in sorted(scored)[:MAX_ROUTES]
        ]

    @staticmethod
    def _assembly_score(request: Request, unmet: set[str], words: set[str]) -> int:
        # An assembly lacking a qualifier already reported missing cannot resolve that gap.
        if not unmet <= words:
            return 0
        # Without the component noun, only a location/system qualifier (the 'cab' of 'cab air
        # filter') makes a title relevant; the compound modifier ('drive' of 'drive belt') does not.
        if request.component not in words and not (unmet - {request.lead}) & words:
            return 0
        return (2 if request.component in words else 0) + sum(
            2 if q in unmet else 1 for q in request.qualifiers if q in words
        )

    def _collect_references(self, rows: list[dict[str, Any]]) -> None:
        request_words = {
            word for request in self.requests for word in (request.component, *request.qualifiers)
        }
        found = []
        for row in rows:
            description = row.get("description") or ""
            # Parsed notes usually repeat the description's tail; keep only new note text.
            text = " ".join(
                [description, *(note for note in row.get("notes", []) if note not in description)]
            )
            instruction = str(row.get("part_number", "")).upper() == "REF" or description.upper().startswith(
                "INSTRUCTION"
            )
            match = REFERENCE.search(text)
            if not (instruction or match):
                continue
            reference = {
                "callout": row.get("callout"),
                "part_number": row.get("part_number"),
                "source_text": (text[match.start() :] if match else text)[:160],
            }
            if name := _reference_name(description):
                reference["suggested_assembly_title"] = name
            overlap = len(request_words & set(_tokens(text)))
            # Explicit redirect rows ('for the correct assembly') outrank a part's own parts-list pointer.
            found.append((not instruction, -overlap, len(found), reference))
        known = {(item["callout"], item["source_text"]) for item in self.references}
        for *_, reference in sorted(found, key=lambda item: item[:3]):
            if (reference["callout"], reference["source_text"]) not in known:
                self.references.append(reference)
