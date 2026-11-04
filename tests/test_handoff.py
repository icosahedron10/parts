"""Ported exact-link restoration regressions."""

import time

import pytest

from cnh_parts_agent.agent.handoff import PartsHandoff, encode_handoff_urls, resolve_handoff_urls


def test_parent_handoff_serialization_preserves_exact_urls_in_prose():
    url = "https://www.mycnhstore.com/us/en/caseih/category/radiator/p/A190663"
    handoff = PartsHandoff(key_facts=[f"Observed product ({url})."], sources=[url], store_url=url)
    handles = {}
    encoded = encode_handoff_urls(handoff, handles)
    assert encoded.key_facts == ["Observed product (url_1)."]
    assert resolve_handoff_urls(encoded, handles) == handoff
    with pytest.raises(ValueError):
        resolve_handoff_urls(PartsHandoff(key_facts=["url_1/changed"], sources=[], store_url=""), handles)


URL = "https://www.mycnhstore.com/us/en/casece/na/catalog"


def test_resolver_restores_urls_and_fact_tokens_without_mutation_or_substring_replacement():
    """Verify resolver restores urls and fact tokens without mutation or substring replacement."""
    mapping = {"url_1": URL, "url_2": URL + "/p/KSH1028"}
    handoff = PartsHandoff(
        key_facts=["[Assembly](url_1), product url_2; part_url_9_suffix and curl_3 are identifiers."],
        sources=["url_1", URL + "/p/KSH1028"],
        store_url="url_2",
    )
    original = handoff.model_dump()
    resolved = resolve_handoff_urls(handoff, mapping)
    assert resolved.sources == [URL, URL + "/p/KSH1028"]
    assert resolved.store_url == URL + "/p/KSH1028"
    assert resolved.key_facts == [
        f"[Assembly]({URL}), product {URL}/p/KSH1028; part_url_9_suffix and curl_3 are identifiers."
    ]
    assert handoff.model_dump() == original
    assert set(resolved.model_dump()) == {"key_facts", "sources", "store_url"}


def test_resolver_preserves_observed_literal_url_paths_and_prose_punctuation():
    """Verify resolver preserves observed literal url paths and prose punctuation."""
    observed = URL + "/part/url_1/(NA)?q=one+two"
    handoff = PartsHandoff(key_facts=[f"[Evidence]({observed})."], sources=[observed], store_url="")
    assert resolve_handoff_urls(handoff, {"url_1": observed}) == handoff


@pytest.mark.parametrize(
    "field,value",
    [
        ("sources", ["url_99"]),
        ("sources", ["url_fake"]),
        ("sources", [URL + "/invented"]),
        ("sources", [""]),
        ("store_url", "url_0"),
        ("store_url", "url_01"),
        ("store_url", " url_1 "),
        ("key_facts", ["See url_99."]),
        ("key_facts", ["See url_0."]),
        ("key_facts", ["See [assembly](url_fake)."]),
        ("key_facts", ["See url_."]),
        ("key_facts", [f"[Invented]({URL}/invented)."]),
        ("key_facts", [f"[URL with handle path]({URL}/url_1)."]),
    ],
)
def test_resolver_rejects_unknown_handles_and_unobserved_literals(field, value):
    """Verify resolver rejects unknown handles and unobserved literals."""
    values = {"key_facts": [], "sources": [], "store_url": "", field: value}
    with pytest.raises(ValueError, match="unknown URL handle or unobserved URL"):
        resolve_handoff_urls(PartsHandoff(**values), {"url_1": URL})


def test_resolver_does_not_guess_equivalent_url_encoding():
    """Verify resolver does not guess equivalent url encoding."""
    with pytest.raises(ValueError, match="unobserved URL"):
        resolve_handoff_urls(
            PartsHandoff(key_facts=[], sources=[URL + "?q=one%20two"], store_url=""),
            {"url_1": URL + "?q=one+two"},
        )


@pytest.mark.parametrize(
    "mapping", [{"url_0": URL}, {"url_1": "/relative"}, {"url_1": URL + " bad"}, {"url_1": URL, "url_2": URL}]
)
def test_resolver_rejects_malformed_mapping(mapping):
    """Verify resolver rejects malformed mapping."""
    with pytest.raises(ValueError):
        resolve_handoff_urls(PartsHandoff(key_facts=[], sources=[], store_url=""), mapping)


@pytest.mark.parametrize("value", ["url_fake", "url_", "url_999", "url_1_suffix"])
def test_handoff_fact_unknown_or_malformed_standalone_alias_fails(value):
    """Verify handoff fact unknown or malformed standalone alias fails."""
    with pytest.raises(ValueError, match="unknown URL handle"):
        resolve_handoff_urls(
            PartsHandoff(key_facts=[f"[Source]({value})"], sources=[], store_url=""), {"url_1": URL}
        )


@pytest.mark.parametrize("suffix", ["/invented", "?invented=yes", "#invented", "-invented", "%2finvented"])
def test_handoff_fact_cannot_append_unobserved_url_suffix_to_known_alias(suffix):
    """Verify handoff fact cannot append unobserved url suffix to known alias."""
    with pytest.raises(ValueError, match="unobserved URL"):
        resolve_handoff_urls(
            PartsHandoff(key_facts=[f"[Source](url_1{suffix})"], sources=[], store_url=""), {"url_1": URL}
        )


def test_post_expansion_validation_preserves_exact_url_and_prose_delimiters():
    """Verify post expansion validation preserves exact url and prose delimiters."""
    observed = URL + "/assembly/(NA)?query=one+two"
    answer = PartsHandoff(key_facts=["Sources: [assembly](url_1), <url_1>."], sources=[], store_url="")
    assert resolve_handoff_urls(answer, {"url_1": observed}).key_facts == [
        f"Sources: [assembly]({observed}), <{observed}>."
    ]


def test_long_unbroken_qualification_scans_quickly_and_preserves_fact_and_link():
    """Verify long unbroken qualification scans quickly and preserves fact and link."""
    qualifier = "Source qualification: " + "x" * 90000
    handoff = PartsHandoff(key_facts=[qualifier, "[Assembly](url_1)."], sources=["url_1"], store_url="")
    started = time.perf_counter()
    restored = resolve_handoff_urls(handoff, {"url_1": URL})
    elapsed = time.perf_counter() - started
    assert restored == PartsHandoff(key_facts=[qualifier, f"[Assembly]({URL})."], sources=[URL], store_url="")
    assert elapsed < 2, f"Large qualification URL processing took {elapsed:.3f}s"
