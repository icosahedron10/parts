import pytest

from scripts.jev_fresh_sample import draw_queries


def test_fresh_sample_excludes_families_not_just_question_wordings():
    queries = [{"id": f"Q{i:03}.{style}", "query": f"question {i} {style}"}
               for i in range(100) for style in range(4)]
    excluded = {f"Q{i:03}" for i in range(40)}
    selected = draw_queries(queries, excluded, 40, 20261106)
    families = {q["id"].split(".")[0] for q in selected}
    assert len(selected) == len(families) == 40
    assert not families & excluded
    assert selected == draw_queries(queries, excluded, 40, 20261106)
    assert all(q in queries for q in selected)


def test_fresh_sample_cannot_silently_reuse_families_when_pool_is_too_small():
    with pytest.raises(ValueError, match="only 1 unused families"):
        draw_queries([{"id": "Q001.a"}, {"id": "Q001.b"}], set(), 2, 123)
