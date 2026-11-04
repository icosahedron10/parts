"""Strict three-field specialist output and exact observed URL restoration."""

import re

from pydantic import BaseModel, ConfigDict, Field

from ..catalog.models import MachineData, OpResult

_URL_TOKEN = r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+"

_LINK_TOKENS = re.compile(_URL_TOKEN + r"|(?<![A-Za-z0-9_])url_[A-Za-z0-9_]*(?![A-Za-z0-9_])")


class PartsHandoff(BaseModel):
    """Source-qualified lookup facts and links for the parent to explain to the user."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key_facts: list[str] = Field(
        description=(
            "Concise source-qualified facts. Keep distinct part entries and their notes, callouts, "
            "catalog quantities, restrictions and uncertainty together. Include clarification questions, "
            "errors, observation times and material coverage or pagination limits here when relevant."
        )
    )
    sources: list[str] = Field(
        description=(
            "Source URLs actually returned by the parts tools, one per string, including assembly "
            "verification links and available product links. Use an empty list when no sources are available."
        )
    )
    store_url: str = Field(
        description=(
            "The observed store product-page URL for the identified part. Use an empty string when "
            "unavailable, no part was found, or distinct candidate parts remain unresolved. Never invent "
            "a URL or substitute an assembly, catalog, or store homepage."
        )
    )


def catalog_choices_handoff(payload: dict) -> PartsHandoff:
    response = OpResult[MachineData].model_validate(payload)
    facts = [
        f"Catalog candidate ({row.machine_ref.source_id}): {row.label}; "
        f"production dates: {row.production_dates or 'not returned'}; "
        f"qualifications: {'; '.join(row.qualifications) or 'none returned'}."
        for row in response.data.machines
    ]
    facts.append(
        f"Dated local discovery: partial coverage; showing {len(response.data.machines)} "
        f"of {response.total} candidates."
    )
    facts.extend(
        f"Source observed: {date}." for date in sorted({e.observed_at.isoformat() for e in response.evidence})
    )
    facts.append(
        "Which exact catalog label and brand matches your machine?"
        if response.total
        else "No matching catalog in the local snapshot. Please confirm the year, make, model and suffix."
    )
    if response.truncated:
        facts.append("More candidates remain; provide a distinguishing model variant or year.")
    facts.append("Catalog candidates do not establish serial-specific fitment or source-wide absence.")
    sources = list(
        dict.fromkeys(
            [m.source_url for m in response.data.machines] + [e.source_url for e in response.evidence]
        )
    )
    return PartsHandoff(key_facts=facts, sources=sources, store_url="")


def validate_url_handles(url_handles: dict[str, str]) -> None:
    """Reject malformed lookup state rather than resolve an ambiguous or invented mapping."""
    for handle, url in url_handles.items():
        if not re.fullmatch(r"url_[1-9][0-9]*", handle):
            raise ValueError("Invalid lookup URL handle")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or re.search(r"\s", url):
            raise ValueError("Lookup URL handles must map to absolute HTTP(S) URLs")
    if len(set(url_handles.values())) != len(url_handles):
        raise ValueError("Lookup URL handles must not duplicate the same observed URL")


def _url_without_prose_suffix(token: str, observed: set[str]) -> str:
    if token in observed:
        return token
    url = token.rstrip(".,;:!?")
    while url and (url[-1] == "]" or (url[-1] == ")" and url.count(")") > url.count("("))):
        url = url[:-1].rstrip(".,;:!?")
    return url


def resolve_handoff_urls(handoff: PartsHandoff, url_handles: dict[str, str]) -> PartsHandoff:
    """Restore exact observed URLs; never repair, normalize, or guess a model-generated URL."""
    validate_url_handles(url_handles)
    observed = set(url_handles.values())

    def resolve_field(value: str, *, empty_allowed: bool = False) -> str:
        """Expand one known URL handle and reject unobserved or malformed structured source links."""
        if empty_allowed and value == "":
            return value
        if value in url_handles:
            return url_handles[value]
        if value in observed:
            return value
        raise ValueError("Handoff contains an unknown URL handle or unobserved URL")

    # Whole URL tokens take precedence so an observed URL path containing url_1 is not rewritten.
    # Reserve standalone url_* aliases, including malformed ones, but not ordinary identifier substrings.

    def resolve_token(match: re.Match[str]) -> str:
        """Replace a prose URL token only when it matches a known link handle or observed URL."""
        token = match.group()
        if "://" not in token:
            return resolve_field(token)
        # Markdown/prose punctuation is outside the URL; retain balanced URL parentheses.
        url = _url_without_prose_suffix(token, observed)
        resolve_field(url)
        return token

    def resolve_fact(fact: str) -> str:
        """Expand fact links and verify that resulting prose preserves exact observed source URLs."""
        resolved = _LINK_TOKENS.sub(resolve_token, fact)
        # Alias suffixes must not manufacture a new URL after a valid token expands.
        for match in re.finditer(_URL_TOKEN, resolved):
            resolve_field(_url_without_prose_suffix(match.group(), observed))
        return resolved

    return PartsHandoff(
        key_facts=[resolve_fact(fact) for fact in handoff.key_facts],
        sources=[resolve_field(value) for value in handoff.sources],
        store_url=resolve_field(handoff.store_url, empty_allowed=True),
    )


def encode_handoff_urls(handoff: PartsHandoff, url_handles: dict[str, str]) -> PartsHandoff:
    """Serialize URLs from an already validated public handoff for a consuming model."""
    reverse = {url: handle for handle, url in url_handles.items()}
    explicit = [*handoff.sources, *([handoff.store_url] if handoff.store_url else [])]
    prose = [
        _url_without_prose_suffix(match.group(), set(explicit))
        for fact in handoff.key_facts
        for match in re.finditer(_URL_TOKEN, fact)
    ]
    for url in [*explicit, *prose]:
        if url not in reverse:
            handle = f"url_{len(reverse) + 1}"
            reverse[url] = handle
            url_handles[handle] = url
    validate_url_handles(url_handles)

    def replace(match):
        token = match.group()
        url = _url_without_prose_suffix(token, set(reverse))
        return reverse[url] + token[len(url) :]

    return PartsHandoff(
        key_facts=[re.sub(_URL_TOKEN, replace, fact) for fact in handoff.key_facts],
        sources=[reverse[url] for url in handoff.sources],
        store_url=reverse[handoff.store_url] if handoff.store_url else "",
    )
