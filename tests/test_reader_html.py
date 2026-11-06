"""Exercise cleanup against real HTML, including row notes and repeated references."""

from pathlib import Path

import pytest
from lxml import html

from cnh_parts_agent.catalog.reader_html import clean_guide_html

FIXTURES = [
    path for path in Path("tests/fixtures").rglob("*.html")
    if b'js-assembly-part-list' in path.read_bytes() and b'canvas-system' in path.read_bytes()
]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: str(p))
def test_real_assembly_rows_and_notes_survive(path):
    body = path.read_bytes()
    original = html.fromstring(body)
    rows = original.xpath('//ul[@data-env="desktop"]/li[@data-hotspot-partnumber]')
    cleaned, metrics = clean_guide_html(body)
    actual = html.fromstring(cleaned).xpath('//li[@data-hotspot-partnumber]')
    assert metrics["rows_preserved"] == len(rows) == len(actual)
    assert [" ".join(n.text_content().split()) for n in actual] == [
        " ".join(n.text_content().split()) for n in rows
    ]
    assert len(cleaned) < len(body) * 0.5
    assert not html.fromstring(cleaned).xpath('//script|//input|//button|//img')


def test_missing_assembly_fails_explicitly():
    with pytest.raises(ValueError, match="assembly content region"):
        clean_guide_html(b"<html><body>Store temporarily unavailable</body></html>")
