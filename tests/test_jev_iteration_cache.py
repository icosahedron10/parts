import json
from types import SimpleNamespace

import pytest

from cnh_parts_agent.catalog.errors import PartsError
from scripts.jev_iterate import model_call
from scripts.jev_pilot import Answer


async def test_paid_response_replay_requires_identical_inputs_and_fresh_mode_calls_again(tmp_path):
    invoked = []

    async def parse(**request):
        invoked.append(request)
        result = {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 5},
                  "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                      "status": "not_found", "findings": [], "explanation": "No evidence",
                      "unresolved_questions": []})}]}]}
        return SimpleNamespace(model_dump=lambda **kwargs: result)

    reader = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    for index, (question, fresh) in enumerate([("first", False), ("first", False),
                                              ("changed", False), ("first", True)]):
        calls = []
        result = await model_call("reader", {"input": question}, Answer,
                                  case_folder=tmp_path / f"case-{index}", cache_folder=tmp_path / "cache",
                                  fresh=fresh, jev=None, reader=reader, calls=calls)
        assert result["status"] == "not_found"
        assert calls[0]["reused"] == (index == 1)
    assert len(invoked) == 3


async def test_unresolved_request_is_not_silently_recharged(tmp_path):
    pending = tmp_path / "case" / "calls" / "001-reader.request.json"
    pending.parent.mkdir(parents=True)
    pending.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Unresolved paid request"):
        await model_call("reader", {"input": "question"}, Answer, case_folder=tmp_path / "case",
                         cache_folder=tmp_path / "cache", fresh=False, jev=None, reader=None, calls=[])


def test_oversized_menu_splits_by_label_size_and_keeps_every_option(monkeypatch):
    from scripts import jev_iterate

    monkeypatch.setattr(jev_iterate, "MENU_LABEL_LIMIT", 100)
    options = [{"kind": "guide", "label": f"guide {i:02d} " + "x" * 30} for i in range(7)]
    partitions = jev_iterate.menu_partitions(options)
    assert len(partitions) > 1
    assert [o for part in partitions for o in part] == options
    assert all(sum(len(jev_iterate.option_label(o)) for o in part) <= 100 for part in partitions)


def test_search_match_label_carries_the_usage_note_that_identifies_the_part():
    from scripts import jev_iterate

    label = jev_iterate.option_label({"kind": "guide", "label": "CAB COAT HOOK", "search_matches": ["COVER; Grille Remote"]})
    assert "COVER; Grille Remote" in label


async def test_invalid_optional_search_is_a_visible_failure_not_catalog_absence(monkeypatch):
    from scripts import jev_iterate

    async def invalid(*args, **kwargs):
        raise PartsError("parser_schema_changed", "Source link escaped selected storefront", 502)

    monkeypatch.setattr(jev_iterate, "execute", invalid)
    found, error = await jev_iterate.checked_catalog_search({}, None, None)
    assert found is None
    assert error["code"] == "parser_schema_changed"
    assert "storefront" in error["message"]


async def test_search_does_not_swallow_other_failures(monkeypatch):
    from scripts import jev_iterate

    async def invalid(*args, **kwargs):
        raise PartsError("invalid_reference", "Bad caller reference")

    monkeypatch.setattr(jev_iterate, "execute", invalid)
    with pytest.raises(PartsError, match="Bad caller reference"):
        await jev_iterate.checked_catalog_search({}, None, None)
