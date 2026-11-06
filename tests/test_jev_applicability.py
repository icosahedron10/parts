import pytest

from cnh_parts_agent.agent.jev_applicability import excludes_year, scope_to_year


@pytest.mark.parametrize("wording,year,excluded", [
    ("(Prod Date) Between 20-Nov-2014 and 01-Apr-2018", 2016, False),
    ("(Prod Date) From 02-Apr-2018", 2016, True),
    ("(Prod Date) From 02-Apr-2018", 2018, False),
    ("(Prod Date) To 01-Apr-2018", 2018, False),
    ("(Prod Date) Up to 02-Oct-2016", 2018, True),
    ("(Prod Date) Up to 02-Oct-2016", 2016, False),
    ("(Prod Date) Between 01-Apr-2018 and 20-Nov-2014", 2016, False),
    ("(Prod Date) From 31-Foo-2018", 2016, False),
    ("(Prod Date) From 04-Oct-2013 (Prod Date) Up to 01-Jan-2015", 2020, False),
    ("(Prod Date) Before 01-Jan-2016", 2016, True),
    ("(Prod Date) After 31-Dec-2016", 2016, True),
    ("(Prod Date) From 02-Apr-2018", None, False),
    ("Serial number 2018; shaft length 2016mm", 2016, False),
    ("Unrecognized production-date wording", 2016, False),
])
def test_only_explicit_date_contradictions_are_separated(wording, year, excluded):
    assert excludes_year(wording, year) is excluded


def test_date_inconsistent_part_remains_conditional_without_claiming_build_year():
    answer = {"status": "found", "findings": [
        {"part_number": "EARLY", "catalog_description": "CABLE; (Prod Date) To 01-Apr-2018"},
        {"part_number": "LATE", "catalog_description": "CABLE; (Prod Date) From 02-Apr-2018"}],
        "unresolved_questions": []}
    result = scope_to_year(answer, 2016)
    assert [f["part_number"] for f in result["findings"]] == ["EARLY"]
    assert result["conditional_alternatives"][0]["part_number"] == "LATE"
    assert "production basis remains to be confirmed" in result["year_scope"]["basis"]
    assert len(answer["findings"]) == 2  # original inference artifact is not rewritten


def test_two_revisions_in_same_year_remain_unresolved():
    answer = {"status": "ambiguous", "findings": [
        {"part_number": "EARLY", "catalog_description": "CABLE; (Prod Date) To 01-Apr-2018"},
        {"part_number": "LATE", "catalog_description": "CABLE; (Prod Date) From 02-Apr-2018"}],
        "unresolved_questions": ["What is the build date?"]}
    assert scope_to_year(answer, 2018) == answer


def test_unconfirmed_leads_still_respect_explicit_date_exclusions():
    answer = {"status": "needs_confirmation", "findings": [], "possible_candidates": [
        {"part_number": "EARLY", "catalog_description": "SPRING; (Prod Date) To 01-Apr-2018"},
        {"part_number": "LATE", "catalog_description": "SPRING; (Prod Date) From 02-Apr-2018"}],
        "unresolved_questions": ["Confirm functional role"]}
    result = scope_to_year(answer, 2016)
    assert result["status"] == "needs_confirmation"
    assert not result["findings"]
    assert [f["part_number"] for f in result["possible_candidates"]] == ["EARLY"]
    assert result["conditional_alternatives"][0]["candidate_role"] == "possible_candidates"
