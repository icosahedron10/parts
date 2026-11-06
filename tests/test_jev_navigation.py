import json

import pytest

from cnh_parts_agent.agent import jev_navigation as nav
from cnh_parts_agent.catalog.errors import PartsError


def rows():
    return {
        "r001": {"part_number": "P100", "description": "CYLINDER; Lift", "callout": None,
                 "catalog_quantity": "1", "notes": []},
        "r002": {"part_number": "P100C", "description": "CORE-HYD CYLINDER; Lift", "callout": "1",
                 "catalog_quantity": "1", "notes": []},
    }


def reading():
    return {"candidates": [{"row_id": key, "evidence_quote": row["description"],
                            "match_basis": "Lift cylinder", "notes": []} for key, row in rows().items()],
            "explanation": "Catalog candidates", "unresolved_questions": [], "missing_evidence": [],
            "next_assembly_concepts": []}


def test_filter_copies_identity_and_blank_reference_and_excludes_core():
    answer = nav.filter_candidates(reading(), rows(), {"url": "https://example.test", "label": "Lift"},
                                   "I need a lift cylinder")
    assert [f["part_number"] for f in answer["findings"]] == ["P100"]
    assert answer["findings"][0]["diagram_reference"] is None
    assert answer["rejected_candidates"][0]["part_number"] == "P100C"


def test_explicitly_requested_core_stays_while_unrequested_core_goes():
    answer = nav.filter_candidates(reading(), rows(), {"url": "u", "label": "Lift"}, "what is the core return number?")
    assert [f["part_number"] for f in answer["findings"]] == ["P100", "P100C"]


@pytest.mark.parametrize("code", ["NSS", "NS", "PL", "0C"])
def test_placeholder_part_numbers_are_never_findings(code):
    data = {"r001": {"part_number": code, "description": "PROCURE LOCALLY; 10 x 16.5 Tire", "callout": "*",
                     "catalog_quantity": "4", "notes": []}}
    candidate = {"row_id": "r001", "evidence_quote": "PROCURE LOCALLY", "match_basis": "Tire", "notes": []}
    answer = nav.filter_candidates({"candidates": [candidate], "explanation": "", "unresolved_questions": [],
                                    "missing_evidence": [], "next_assembly_concepts": []},
                                   data, {"url": "u", "label": "Wheels"}, "tire")
    assert answer["status"] == "not_found" and answer["rejected_candidates"][0]["part_number"] == code


def test_unconfirmed_lead_is_never_promoted_to_a_supported_finding():
    data = reading()
    data["possible_candidates"] = data.pop("candidates")
    data["candidates"] = []
    answer = nav.filter_candidates(data, rows(), {"url": "u", "label": "Lift"}, "cylinder")
    assert answer["status"] == "needs_confirmation"
    assert answer["findings"] == []
    assert [f["part_number"] for f in answer["possible_candidates"]] == ["P100"]
    assert answer["possible_candidates"][0]["confirmation_status"] == "unconfirmed_function"


def test_row_cannot_be_both_confirmed_and_unconfirmed():
    data = reading()
    data["possible_candidates"] = [data["candidates"][0]]
    with pytest.raises(ValueError, match="both confirmed and unconfirmed"):
        nav.filter_candidates(data, rows(), {"url": "u", "label": "Lift"}, "cylinder")


@pytest.mark.parametrize("change", ["unknown_row", "invented_quote", "duplicate_row"])
def test_invalid_candidate_evidence_fails_explicitly(change):
    data = reading()
    if change == "unknown_row":
        data["candidates"][0]["row_id"] = "r999"
    elif change == "invented_quote":
        data["candidates"][0]["evidence_quote"] = "Guaranteed serial fitment"
    else:
        data["candidates"].append(data["candidates"][0])
    with pytest.raises(ValueError):
        nav.filter_candidates(data, rows(), {"url": "u", "label": "g"}, "cylinder")


def test_references_have_source_context_and_only_match_observed_figure_codes():
    data = rows()
    data["r001"]["description"] = "RADIATOR; See Figure 02-05"
    refs = nav.cross_references(data)
    assert refs == [{"figure_code": "02-05", "source_part": "P100",
                     "source_text": "RADIATOR; See Figure 02-05", "callout": None}]
    assert nav.cross_reference_matches({"label": "02-05 - RADIATOR"}, refs)
    assert not nav.cross_reference_matches({"label": "02-050 - OTHER"}, refs)


def test_reference_range_includes_the_figures_between_its_endpoints():
    data = rows()
    data["r001"]["description"] = "HYDRAULIC PUMP; Include(s) 1 - 111 And See Figure(s) 08-17 - 08-20"
    assert [r["figure_code"] for r in nav.cross_references(data)] == ["08-17", "08-20", "08-18", "08-19"]


def test_row_labeling_rejects_shifted_identities():
    page = '<article><li data-hotspot-partnumber="P100C">Core</li><li data-hotspot-partnumber="P100">Lift</li></article>'
    with pytest.raises(ValueError, match="order disagree"):
        nav.label_rows(page, rows())


async def run_navigation(monkeypatch, max_guides=8, native_search=False, lead_first=False, empty_index=False,
                         broken_page=None, reference_followup=None):
    machine = b'<html><div id="bread-in-model-page" data-serie-code="series"></div><a data-functionalgroupcode="G1">01 - FIRST</a><a data-functionalgroupcode="G2">02 - SECOND</a></html>'
    page = b'<html><title>Guide</title><div class="canvas-system"><ul data-env="desktop"><li class="js-assembly-part-list" data-hotspot-ref="1" data-hotspot-partnumber="P100">1 1 P100 CYLINDER; Lift</li></ul></div></html>'
    case = {"brand": "casece", "query": "lift cylinder", "machine": {"label": "Machine",
            "machine_ref": {"id": "model", "path": "/machine"}}}
    monkeypatch.setattr(nav, "validate_reference", lambda *a, **kw: None)
    source = rows()
    if reference_followup:
        source["r001"]["description"] = "RADIATOR; See Figure 02-05"
    monkeypatch.setattr(nav, "source_rows", lambda *a: ({"occurrences": list(source.values())}, source))
    inspected = []
    states = []
    menus = []

    async def fetch(url):
        if url.endswith("/machine"):
            return machine, {"url": url}
        if "findAssembly" in url:
            group = "G1" if "/G1/" in url else "G2"
            label = group + " guide"
            if empty_index and group == "G1":
                label = "Hidden nothing here"
            if reference_followup and group == "G1":
                return json.dumps([{"modelCode": "model", "functionalGroupCode": group, "assemblyCode": code,
                                    "assemblyUrl": "/guide/" + code, "assemblyFullName": code + " - PAGE"}
                                   for code in ("02-04", "02-05")]).encode(), {"url": url}
            return json.dumps([{"modelCode": "model", "functionalGroupCode": group,
                                "assemblyCode": group + "A", "assemblyUrl": "/guide/" + group,
                                "assemblyFullName": label}]).encode(), {"url": url}
        if empty_index and "/guide/G1/" in url:
            return b"<html><title>Anything</title></html>", {"url": url}
        if broken_page and "/guide/G1/" in url:
            raise broken_page
        return page, {"url": url}

    async def choose(state, options):
        states.append(state)
        menus.append(options)
        if reference_followup and options[-1]["kind"] == "answer":
            choice = options[0] if reference_followup == "follow" else options[-1]
            return {"selected": choice}
        for option in options:
            if option["kind"] == "guide":
                assert option["ref"]["id"] not in inspected
                return {"selected": option}
        return {"selected": options[0]}

    async def read(case, guide, group, cleaned, source, ordinal):
        inspected.append(guide["ref"]["id"])
        if reference_followup:
            part = "P100" if guide["ref"]["id"] == "02-04" else "P200"
            found = [{"part_number": part, "source_url": guide["url"], "source_row_id": "r001"},
                     {"part_number": part + "B", "source_url": guide["url"], "source_row_id": "r002"}]
            return {"status": "ambiguous", "findings": found, "explanation": "Hoses here",
                    "unresolved_questions": ["Which position?"], "possible_candidates": []}
        found = not lead_first and guide["ref"]["id"] == ("SEARCHED" if native_search else "G2A")
        return {"status": "found" if found else "not_found", "findings": [{"part_number": "P100"}] if found else [],
                "explanation": "Found exact cylinder" if found else "Only pins; cylinder absent",
                "unresolved_questions": ["Confirm functional role"] if lead_first and not found else [],
                "possible_candidates": [{"part_number": "P100", "source_url": guide["url"],
                                         "source_row_id": "r001"}] if lead_first and not found else []}

    async def search(state):
        assert len(state["failed_guides"]) == 2
        return {"queries": ["cylinder"], "coverage": [{"status": "complete_for_scope"}], "sources": [],
                "guides": [{"label": "Found assembly", "ref": {"id": "SEARCHED"},
                            "url": "https://www.mycnhstore.com/searched", "group": "Native catalog search"}]}

    result = await nav.navigate(case, config={"timeout_seconds": 5, "max_groups": 2, "max_guides": max_guides,
                                             "max_actions": 8},
                                fetch=fetch, choose=choose, read=read, search=search if native_search else None)
    return result, states, inspected, menus


async def test_failed_guide_backtracks_with_evidence_and_never_revisits(monkeypatch):
    result, states, inspected, _ = await run_navigation(monkeypatch)
    assert inspected == ["G1A", "G2A"]
    assert result["answer"]["findings"] == [{"part_number": "P100"}]
    assert states[-1]["inspections"][0]["explanation"] == "Only pins; cylinder absent"


async def test_budget_exhaustion_is_not_catalog_wide_absence(monkeypatch):
    result, states, inspected, _ = await run_navigation(monkeypatch, max_guides=1)
    assert inspected == ["G1A"]
    assert result["stop_reason"] == "search_budget_exhausted"
    assert result["answer"]["findings"] == []
    assert "catalog-wide absence is not established" in result["answer"]["explanation"]


async def test_native_search_only_nominates_a_guide_that_must_be_read(monkeypatch):
    result, states, inspected, _ = await run_navigation(monkeypatch, native_search=True)
    assert inspected == ["G1A", "G2A", "SEARCHED"]
    assert result["inspections"][-1]["group"] == "Native catalog search"
    assert result["answer"]["findings"] == [{"part_number": "P100"}]
    assert states[-1]["native_catalog_search"][0]["queries"] == ["cylinder"]


async def test_unconfirmed_lead_survives_budget_exhaustion_without_becoming_an_answer(monkeypatch):
    result, states, inspected, _ = await run_navigation(monkeypatch, max_guides=1, lead_first=True)
    assert result["stop_reason"] == "search_budget_exhausted"
    assert result["answer"]["status"] == "needs_confirmation"
    assert not result["answer"]["findings"]
    assert result["answer"]["possible_candidates"][0]["part_number"] == "P100"
    assert result["answer"]["unresolved_questions"] == ["Confirm functional role"]


async def test_any_page_without_rows_does_not_abort_research_whatever_its_title(monkeypatch):
    result, states, inspected, _ = await run_navigation(monkeypatch, empty_index=True)
    assert inspected == ["G2A"]
    assert len(result["inspections"]) == 2
    assert "no HTML parts rows" in result["inspections"][0]["answer"]["explanation"]
    assert result["answer"]["findings"] == [{"part_number": "P100"}]
    assert result["answer"]["source_limitations"] == []


@pytest.mark.parametrize("failure", [PartsError("parser_schema_changed", "Bad product link", 502), ValueError("Bad page")])
async def test_one_unusable_page_is_reported_and_research_continues(monkeypatch, failure):
    result, states, inspected, _ = await run_navigation(monkeypatch, broken_page=failure)
    assert inspected == ["G2A"]
    assert result["answer"]["findings"] == [{"part_number": "P100"}]
    assert result["answer"]["source_limitations"][0]["operation"] == "read_guide"
    assert str(failure) in result["answer"]["source_limitations"][0]["message"]


async def test_leads_keep_searching_and_stay_separate_from_findings(monkeypatch):
    result, states, inspected, _ = await run_navigation(monkeypatch, lead_first=True)
    assert inspected == ["G1A", "G2A"]
    assert result["stop_reason"] == "search_budget_exhausted"
    assert result["answer"]["possible_candidates"] and not result["answer"]["findings"]
    assert "unconfirmed leads" in result["answer"]["explanation"]


async def test_opened_guides_stay_selectable_without_returning_to_their_group(monkeypatch):
    result, states, inspected, menus = await run_navigation(monkeypatch, max_guides=1)
    assert [o["kind"] for o in menus[1]] == ["guide", "group"]
    assert "current_group" not in states[1]


def test_section_index_guides_stay_off_the_menu_unless_search_nominates_them():
    group = {"kind": "group", "label": "G", "code": "G"}
    index = {"label": "10.000.00 - PICTORIAL INDEX - ENGINE", "ref": {"id": "I"}, "url": "u"}
    kit = {"label": "88.100.33 - DIA KIT, SECTION INDEX - BRAKES", "ref": {"id": "K"}, "url": "u"}
    accessories = {"label": "88.000.00 - PICTORIAL INDEX, ACCESSORIES", "ref": {"id": "A"}, "url": "u"}
    menu = nav.menu([group], {"G": [index, kit, accessories]}, set(), [], [], may_open=False)
    assert [o["ref"]["id"] for o in menu] == ["K", "A"]
    nominated = {**index, "group": "Native catalog search", "search_matches": ["COVER; Grille Remote"]}
    menu = nav.menu([group], {"G": [index, kit, accessories]}, set(), [nominated], [], may_open=False)
    assert menu[0]["search_matches"] == ["COVER; Grille Remote"] and len(menu) == 3


async def test_several_confirmed_rows_with_an_open_question_may_follow_a_literal_reference(monkeypatch):
    result, states, inspected, menus = await run_navigation(monkeypatch, reference_followup="follow")
    assert inspected == ["02-04", "02-05"]
    assert {f["part_number"] for f in result["answer"]["findings"]} == {"P100", "P100B", "P200", "P200B"}
    assert menus[-1][-1]["kind"] == "answer"
    assert menus[-1][0]["cross_references"][0]["figure_code"] == "02-05"


async def test_reference_followup_can_answer_immediately(monkeypatch):
    result, states, inspected, menus = await run_navigation(monkeypatch, reference_followup="stop")
    assert inspected == ["02-04"]
    assert result["stop_reason"] == "supported_candidates"
    assert result["decisions"][-1]["selected"]["kind"] == "answer"


def test_failed_search_coverage_is_exposed_as_a_source_limitation():
    failed = {"status": "failed", "query": "spring", "error": {"code": "parser_schema_changed"}}
    assert nav.source_limitations([{"coverage": [{"status": "complete_for_scope"}, failed]}], []) == [
        {"operation": "native_catalog_search", **failed}]
