"""Compact assembly HTML for a reader while preserving all desktop parts rows."""

import copy
import re

from lxml import etree, html

from .parse import document


def has_class(name):
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def clean_guide_html(body: bytes) -> tuple[str, dict]:
    doc = document(body)
    roots = doc.xpath(f'//*[{has_class("canvas-system")}]')
    if len(roots) != 1:
        raise ValueError("Expected exactly one assembly content region")
    rows = roots[0].xpath(f'.//ul[@data-env="desktop"]/li[{has_class("js-assembly-part-list")}]')
    if not rows:
        raise ValueError("Assembly has no recognized desktop parts rows")
    original_rows = [
        (row.get("data-hotspot-partnumber"), row.get("data-hotspot-ref"),
         " ".join(row.text_content().split()))
        for row in rows
    ]
    root = copy.deepcopy(roots[0])
    remove = root.xpath(
        './/*[@data-env="mobile"]|.//*[@id="canvas-system-toolbox"]|'
        f'.//*[{has_class("modal")}]|'
        './/script|.//style|.//noscript|.//svg|.//iframe|.//form|.//select|'
        './/input|.//button|.//img|.//comment()'
    )
    for node in remove:
        if node.getparent() is not None:
            node.getparent().remove(node)
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        for attr in list(node.attrib):
            if attr not in {"data-hotspot-partnumber", "data-hotspot-ref", "colspan", "rowspan", "href"}:
                del node.attrib[attr]
        if node.get("href") is not None and "/cn/" not in node.get("href"):
            del node.attrib["href"]
        if node.text:
            node.text = re.sub(r"\s+", " ", node.text)
        if node.tail:
            node.tail = re.sub(r"\s+", " ", node.tail)
    for node in list(root.iterdescendants())[::-1]:
        if node.tag in {"div", "span", "i", "a"} and not node.attrib:
            node.drop_tag()
    cleaned_rows = root.xpath('.//li[@data-hotspot-partnumber]')
    if len(cleaned_rows) != len(original_rows):
        raise ValueError("HTML cleanup changed the number of catalog rows")
    for original, cleaned in zip(original_rows, cleaned_rows, strict=True):
        identity = (cleaned.get("data-hotspot-partnumber"), cleaned.get("data-hotspot-ref"))
        if identity != original[:2] or " ".join(cleaned.text_content().split()) != original[2]:
            raise ValueError("HTML cleanup changed a catalog row's identity or text")
    article = html.Element("article")
    heading = etree.SubElement(article, "h1")
    heading.text = " ".join(doc.xpath("string(//title)").split())
    article.append(root)
    result = etree.tostring(article, method="html", encoding="unicode")
    return result, {
        "raw_bytes": len(body), "clean_chars": len(result), "rows_preserved": len(original_rows),
        "cleanup_version": "guide-html-v1", "images_supplied": False,
    }
