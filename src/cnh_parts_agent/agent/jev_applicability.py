"""Separate source candidates inconsistent with the supplied year, preserving uncertainty."""

import re
from datetime import date, datetime, timedelta

PRODUCTION_DATE = re.compile(
    r"\((?:Prod|Production) Date\)\s*(Between|From|After|Before|Up to|To)\s+"
    r"(\d{1,2}-[A-Za-z]{3}-\d{4})(?:\s+(?:and|to)\s+(\d{1,2}-[A-Za-z]{3}-\d{4}))?",
    re.I,
)


def excludes_year(description, year):
    """Only recognize one explicit production-date clause; leave unfamiliar formats unresolved."""
    if year is None:
        return False
    clauses = list(PRODUCTION_DATE.finditer(description))
    if len(clauses) != 1:
        return False
    match = clauses[0]
    operation = match[1].casefold()
    try:
        first = datetime.strptime(match[2], "%d-%b-%Y").date()
        second = datetime.strptime(match[3], "%d-%b-%Y").date() if match[3] else None
    except ValueError:
        return False
    start, end = date.min, date.max
    if operation == "between":
        if second is None:
            return False
        start, end = first, second
    elif operation == "from":
        start = first
    elif operation == "after":
        start = first + timedelta(days=1)
    elif operation == "before":
        end = first - timedelta(days=1)
    elif operation in {"to", "up to"}:
        end = first
    if start > end:
        return False
    return date(year, 12, 31) < start or date(year, 1, 1) > end


def scope_to_year(answer, year):
    """Keep later/earlier build alternatives separately instead of presenting them as matches."""
    if year is None:
        return answer
    scoped, conditional = {"findings": [], "possible_candidates": []}, []
    for role in scoped:
        for finding in answer.get(role, []):
            if excludes_year(finding["catalog_description"], year):
                conditional.append({**finding, "candidate_role": role,
                                    "reason": f"Explicit production-date scope does not overlap the supplied year {year}."})
            else:
                scoped[role].append(finding)
    if not conditional:
        return answer
    questions = list(answer.get("unresolved_questions", []))
    questions.append(f"Confirm whether {year} is the production/build year; a marketing or registration year may require different applicability research.")
    status = "ambiguous" if scoped["findings"] else "needs_confirmation" if scoped["possible_candidates"] else "not_found"
    return {**answer, "status": status, **scoped,
            "conditional_alternatives": conditional, "unresolved_questions": questions,
            "year_scope": {"provided_year": year, "basis": "User-supplied machine year; production basis remains to be confirmed"}}
