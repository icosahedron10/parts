"""Recognize bounded CNH responses and retain allowlisted catalog evidence.

The M0 fixtures are source observations, not production dependencies. No parser
failure is interpreted as absence. HTML is data and never returned as markup.
"""

import copy
import hashlib
import json
import re
from typing import Any, NoReturn
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import etree, html

from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.storefront import CASECE, Storefront

ORIGIN = "https://www.mycnhstore.com"
PARSER_VERSION = "cnh-v3"
UNSUPPLIED_PARTS = frozenset({"NSS", "NLS", "REF"})
ID_RE = re.compile("[A-Za-z0-9][A-Za-z0-9_-]{0,255}\\Z")
GROUP_RE = re.compile("[A-Za-z0-9][A-Za-z0-9_&-]{0,255}\\Z")
PART_RE = re.compile("[A-Za-z0-9][A-Za-z0-9._/+() -]{0,119}\\Z")
SEGMENT_RE = re.compile("[A-Za-z0-9](?:[A-Za-z0-9_.+-]|%26)*\\Z")
DATE_RE = re.compile("\\((\\d{2}/\\d{2}\\s*-\\s*(?:\\d{2}/\\d{2})?)\\)")


def fail(message: str, code: str = "parser_schema_changed") -> NoReturn:
    """Raise a typed upstream parsing error; malformed content never establishes catalog absence."""
    raise PartsError(code, message, 502)


def identifier(value: Any, *, part: bool = False) -> str:
    """Validate a source identifier or part number and return its original spelling."""
    pattern = PART_RE if part else ID_RE
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail("The source returned an unsupported identifier.")
    return value


def group_identifier(value: Any, catalog_id: str) -> str:
    """Group names contain observed ampersands; they are never URL path IDs."""
    identifier(catalog_id)
    if (
        not isinstance(value, str)
        or not GROUP_RE.fullmatch(value)
        or (not value.startswith(catalog_id + "_"))
        or (value == catalog_id + "_")
    ):
        fail("The source returned an unsupported or cross-catalog group identifier.")
    return value


def plain(node: html.HtmlElement) -> str:
    """Extract bounded visible text from a copied HTML node, removing scripts and hidden template content."""
    node = copy.deepcopy(node)
    for child in node.xpath(".//script|.//style|.//noscript|.//template|.//comment()"):
        child.drop_tree() if hasattr(child, "drop_tree") else child.getparent().remove(child)
    value = " ".join(node.text_content().split())
    if len(value) > 8192:
        fail("A source text field exceeds the supported size.")
    return value


def text(value: Any, *, required: bool = False) -> str:
    """Normalize a source text field to plain text; reject malformed, oversized, or missing required values."""
    if value is None and (not required):
        return ""
    if not isinstance(value, str) or len(value) > 32768:
        fail("The source changed a text field.")
    try:
        value = plain(html.fragment_fromstring(value, create_parent="div")) if value else ""
    except (etree.LxmlError, ValueError):
        fail("The source returned malformed text markup.")
    if required and (not value):
        fail("The source omitted a required text field.")
    return value


def document(body: bytes) -> html.HtmlElement:
    """Parse nonempty HTML and reject recognized login or access challenges with a typed source error."""
    if not body.strip():
        fail("The source returned an empty response.", "empty_source_response")
    try:
        doc = html.fromstring(body)
    except (etree.LxmlError, ValueError):
        fail("The source returned unrecognized HTML.", "unexpected_source_content")
    title = " ".join(doc.xpath("//title/text()")).lower()
    if (
        any((phrase in title for phrase in ("access denied", "just a moment", "sign in", "log in")))
        or doc.xpath('//*[@id="challenge-form" or @id="cf-challenge-running"]')
        or doc.xpath('//form//input[@type="password"]')
    ):
        fail("The source returned an access or login challenge.", "source_access_denied")
    return doc


def source_link(value: str, *, asset: bool = False, storefront: Storefront = CASECE) -> str:
    """Canonicalize an approved CNH catalog or asset URL, rejecting unsafe paths and foreign origins."""
    if not isinstance(value, str) or not value or len(value) > 8192:
        fail("The source returned an invalid link.")
    if any((ord(char) < 33 or ord(char) == 127 or char in "\\" for char in value)):
        fail("The source returned an unsafe link.")
    try:
        if any((segment in (".", "..") for segment in urlsplit(value).path.split("/"))):
            fail("The source returned a path containing traversal segments.")
        url = urljoin(ORIGIN, value)
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "www.mycnhstore.com"
            and (parsed.port in (None, 443))
            and (not parsed.username)
            and (not parsed.password)
            and (not parsed.fragment)
        )
    except ValueError:
        fail("The source returned an invalid link.")
    prefix = "/medias/" if asset else storefront.home_path + "/"
    if not valid or not parsed.path.startswith(prefix):
        fail("The source link is outside the approved origin and market.")
    segments = parsed.path.strip("/").split("/")
    if any((segment in (".", "..") or not SEGMENT_RE.fullmatch(segment) for segment in segments)):
        fail("The source returned an unsupported link path.")
    return urlunsplit(("https", "www.mycnhstore.com", parsed.path, parsed.query, ""))


def reference(kind: str, item_id: str, context: str | None = None, path: str | None = None) -> dict:
    """Construct an internal unsigned resource marker for validation at the service boundary."""
    return {"$ref": {"kind": kind, "id": item_id, "context": context, "path": path}}


def qualifications(label: str) -> list[str]:
    """Preserve each parenthetical source qualifier, including market and production-date wording."""
    return [text(item) for item in re.findall("\\(([^()]*)\\)", label) if item.strip()]


def machines(body: bytes, *, storefront: Storefront = CASECE) -> list[dict]:
    """Parse bounded, unique machine cards with catalog identities, original labels, and source URLs."""
    doc = document(body)
    cards = doc.xpath(
        '//li[contains(concat(" ",normalize-space(@class)," ")," category-page-search-card-container ")]'
    )
    if not cards:
        fail("The source no longer exposes recognized machine result cards.")
    if len(cards) > 500:
        fail("The source returned too many machine cards.")
    result, seen = ([], set())
    for card in cards:
        labels, links = (card.xpath(".//h5"), card.xpath("./a/@href"))
        if len(labels) != 1 or len(links) != 1:
            fail("The source changed a machine card.")
        url = source_link(links[0], storefront=storefront)
        path = urlsplit(url).path
        parts = path.rsplit("/cn/", 1)
        if len(parts) != 2 or "/" in parts[1] or (not path.startswith(storefront.home_path + "/na/")):
            fail("The source changed the machine catalog path.")
        catalog_id = identifier(parts[1])
        if catalog_id in seen:
            fail("The source repeated a machine identifier.")
        seen.add(catalog_id)
        label = plain(labels[0])
        if not label:
            fail("The source omitted a machine label.")
        dates = DATE_RE.search(label)
        result.append(
            {
                "machine_ref": reference("machine", catalog_id, path=path),
                "label": label,
                "production_dates": dates.group(1) if dates else None,
                "qualifications": qualifications(label),
                "source_url": url,
            }
        )
    return result


def groups(body: bytes, catalog_id: str) -> dict:
    """Parse the selected catalog group list and reject duplicate or cross-catalog group identifiers."""
    doc = document(body)
    headings = doc.xpath('//*[@id="assembly-model-page-content"]//h1')
    nodes = doc.xpath("//*[@data-functionalgroupcode]")
    if len(headings) != 1 or not nodes:
        fail("The source no longer exposes a recognized model group list.")
    result, seen = ([], set())
    for node in nodes:
        group_id = group_identifier(node.get("data-functionalgroupcode"), catalog_id)
        label = plain(node)
        if not group_id.startswith(catalog_id + "_") or not label or group_id in seen:
            fail("The source returned a duplicated or cross-catalog group.")
        seen.add(group_id)
        result.append({"group_ref": reference("group", group_id, context=catalog_id), "label": label})
    return {"model_label": plain(headings[0]), "groups": result}


def assembly_url(model_path: str, row: dict, *, storefront: Storefront = CASECE) -> str:
    """Resolve a search row into the selected model region, rejecting foreign or cross-catalog paths."""
    raw = row.get("assemblyUrl")
    if not isinstance(raw, str):
        fail("The source omitted an assembly URL.")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        fail("The source returned a malformed assembly URL.")
    parts = parsed.path.split("/")
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or (len(parts) != 10)
        or (parts[1] not in ("amea", "na", "eu"))
        or (parts[8] != "cn")
        or (parts[9] != row["assemblyCode"])
    ):
        fail("The source changed the assembly URL structure.")
    if model_path.rsplit("/", 1)[-1] != row["modelCode"]:
        fail("The source returned a cross-catalog assembly.")
    return source_link(
        model_path.rsplit("/cn/", 1)[0] + "/" + "/".join(parts[6:]) + "/" + row["modelCode"],
        storefront=storefront,
    )


def note_values(value: Any) -> list[str]:
    """Normalize source notes to strings while preserving a flag that additional notes require inspection."""
    if value is None or value is False:
        return []
    if value is True:
        return ["Source indicates additional notes; inspect the assembly page."]
    if isinstance(value, str):
        clean = text(value)
        return [clean] if clean else []
    if isinstance(value, list) and all((isinstance(item, str) for item in value)):
        return [clean for item in value if (clean := text(item))]
    fail("The source changed its qualification field structure.")


def quantity(value: Any, *, required: bool = False) -> str | None:
    """Return catalog quantity as text without interpreting units; reject booleans and malformed fields."""
    if value is None and (not required):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        fail("The source changed the catalog quantity field.")
    return text(str(value), required=required)


def product_link(value: Any, part_number: str, *, storefront: Storefront = CASECE) -> str | None:
    """Validate a product URL against its exact part number, returning None for absent or NSS links."""
    if not value or part_number in UNSUPPLIED_PARTS:
        return None
    if not isinstance(value, str):
        fail("The source changed a product URL.")
    if value.startswith("/category/"):
        value = storefront.home_path + value
    url = source_link(value, storefront=storefront)
    if urlsplit(url).path.rsplit("/p/", 1)[-1] != part_number or "/p/" not in urlsplit(url).path:
        fail("The source returned a product URL for a different part.")
    return url


def search(
    body: bytes, catalog_id: str, model_path: str, mode: str, *, storefront: Storefront = CASECE
) -> dict:
    """Parse scoped search JSON, preserve qualifiers, and mark capped or incomplete results as partial."""
    if not body.strip():
        fail("The source returned an empty response.", "empty_source_response")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        if body.lstrip().startswith(b"<"):
            document(body)
        fail("The source search did not return recognized JSON.", "unexpected_source_content")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        fail("The source changed the search response structure.")
    page = data.get("pagination")
    keys = ("pageSize", "currentPage", "numberOfPages", "totalNumberOfResults")
    if not isinstance(page, dict) or any((type(page.get(key)) is not int or page[key] < 0 for key in keys)):
        fail("The source changed search pagination metadata.")
    if page["pageSize"] != 500 or page.get("sort") != "fittability-" + mode or page["currentPage"] != 0:
        fail("The source search returned an unexpected scope or cap.")
    rows = data["results"]
    if (
        len(rows) > page["pageSize"]
        or len(rows) > page["totalNumberOfResults"]
        or (rows and page["numberOfPages"] == 0)
        or (page["totalNumberOfResults"] == 0 and page["numberOfPages"] != 0)
    ):
        fail("The source returned inconsistent search counts.")
    result = []
    diagrams = []
    for row in rows:
        if not isinstance(row, dict):
            fail("The source changed a search result row.")
        if row.get("modelCode") != catalog_id:
            fail("The source returned a cross-catalog search result.")
        assembly_id = identifier(row.get("assemblyCode"))
        title = text(row.get("assemblyFullName"), required=True)
        for field in ("functionalGroupCode", "sectionCode"):
            if row.get(field) is not None:
                group_identifier(row[field], catalog_id)
        url = assembly_url(model_path, row, storefront=storefront)
        if row.get("urlAssemblyImage"):
            diagram = source_link(row["urlAssemblyImage"], asset=True, storefront=storefront)
            if diagram not in diagrams:
                diagrams.append(diagram)
        quals = note_values(row.get("technicalTypes"))
        if row.get("modelFullName"):
            quals.append(text(row["modelFullName"], required=True))
        item: dict = {
            "kind": "assembly" if mode == "figure" else "part",
            "label": title,
            "assembly_label": title,
            "assembly_ref": reference("assembly", assembly_id, catalog_id, urlsplit(url).path),
            "assembly_url": url,
            "qualifications": quals,
        }
        if mode != "figure":
            part_number = identifier(row.get("partNumber"), part=True)
            description = text(row.get("partDescription"), required=True)
            notes = note_values(row.get("usage")) + note_values(row.get("showNote"))
            product = row.get("product")
            if product is not None and (not isinstance(product, dict)):
                fail("The source changed product evidence structure.")
            product_url = (
                product_link(product.get("url"), part_number, storefront=storefront) if product else None
            )
            item.update(
                {
                    "part_number": part_number,
                    "label": description,
                    "part_ref": None if part_number in UNSUPPLIED_PARTS else reference("part", part_number),
                    "description": description,
                    "catalog_quantity": quantity(row.get("quantity")),
                    "notes": notes,
                    "callout": None,
                    "product_url": product_url,
                }
            )
        result.append(item)
    complete = (
        len(rows) == page["totalNumberOfResults"]
        and page["numberOfPages"] <= 1
        and (len(rows) < page["pageSize"])
    )
    return {
        "matches": result,
        "diagram_urls": diagrams,
        "source_pagination": {key: page[key] for key in (*keys, "sort")},
        "outcome": ("ok" if rows else "not_found") if complete else "partial",
        "coverage": {
            "status": "complete_for_scope" if complete else "partial",
            "reason": None if complete else "source_result_cap_or_incomplete_page",
        },
    }


def serial_restriction(value: str) -> bool:
    """Recognize an explicit serial applicability clause, never a hardware noun.

    An identifier label by itself does not establish a restriction. Ambiguous
    wording remains available in the unchanged description and notes.
    """
    marker = "\\b(?:serial(?:\\s+(?:numbers?|no\\.?))?|S/N|P\\.?I\\.?N\\.?)\\b"
    for clause in value.split(";"):
        for match in re.finditer(marker, clause, re.I):
            following = clause[match.end() :]
            identity = re.match(
                "[\\s.:#<>=â‰¤â‰¥]*(?:no\\.?\\s*|number\\s*)?(?:(?:before|after|from|through|thru|between|above|below|up\\s+to)\\s+)?([A-Z0-9]+(?:-[A-Z0-9]+)*)(?![\\w/.'\\\"])",
                following,
                re.I,
            )
            if identity is None:
                continue
            token = identity.group(1)
            if not any((character.isdigit() for character in token)):
                continue
            if re.fullmatch("\\d+(?:mm|cm|in|inch|inches)", token, re.I):
                continue
            bounded_range = re.fullmatch("\\d+-\\d+", token) is not None
            applicability = re.search(
                "\\b(?:before|after|from|through|thru|between|only|prior|later|earlier|above|below|onwards?|starting|ending|up\\s+to)\\b|[<>â‰¤â‰¥]",
                clause,
                re.I,
            )
            directed_to_serial = re.search("\\bfor\\s*$", clause[: match.start()], re.I)
            if bounded_range or applicability or directed_to_serial:
                return True
    return False


def assembly(
    body: bytes, assembly_id: str, assembly_page_url: str, *, storefront: Storefront = CASECE
) -> dict:
    """Parse assembly occurrences with stable IDs, quantities, notes, serial restrictions, and diagrams."""
    doc = document(body)
    rows = doc.xpath(
        '//ul[@data-env="desktop"]/li[contains(concat(" ",normalize-space(@class)," ")," js-assembly-part-list ")]'
    )
    if not rows:
        fail("The source no longer exposes recognized assembly rows.")
    if len(rows) > 2000:
        fail("The source returned too many assembly occurrences.")
    result = []
    for index, row in enumerate(rows):
        amounts = row.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," part-qnt ")]/span')
        descriptions = row.xpath(
            './/div[contains(concat(" ",normalize-space(@class)," ")," part-description ")]/span'
        )
        if len(amounts) != 1 or len(descriptions) != 1:
            fail("The source changed an assembly occurrence.")
        part_number = identifier(row.get("data-hotspot-partnumber"), part=True)
        description = plain(descriptions[0])
        if not description:
            fail("The source omitted an occurrence description.")
        notes = [part.strip() for part in description.split(";")[1:] if part.strip()]
        for note in row.xpath(
            './/*[contains(concat(" ",normalize-space(@class)," ")," part-note ") or contains(concat(" ",normalize-space(@class)," ")," part-notes ")]'
        ):
            value = plain(note)
            if value and value not in notes:
                notes.append(value)
        restrictions = [note for note in [description, *notes] if serial_restriction(note)]
        restrictions = list(dict.fromkeys(restrictions))
        callout = text(row.get("data-hotspot-ref")) or None
        identity = f"{assembly_page_url}\x00{assembly_id}\x00{index}\x00{part_number}\x00{callout or ''}"
        result.append(
            {
                "occurrence_id": hashlib.sha256(identity.encode()).hexdigest()[:32],
                "part_number": part_number,
                "part_ref": None if part_number in UNSUPPLIED_PARTS else reference("part", part_number),
                "catalog_quantity": quantity(plain(amounts[0]), required=True),
                "callout": callout,
                "description": description,
                "notes": notes,
                "serial_restrictions": restrictions,
                "product_url": product_link(row.get("data-hotspot-href"), part_number, storefront=storefront),
                "assembly_url": assembly_page_url,
            }
        )
    diagrams = []
    for value in doc.xpath("//img/@src|//img/@data-src|//*[@data-assembly-image]/@data-assembly-image"):
        if "/medias/techdrawing-assembly-" in value:
            url = source_link(value, asset=True, storefront=storefront)
            if url not in diagrams:
                diagrams.append(url)
    return {"occurrences": result, "metadata": {"diagram_urls": diagrams, "source_notes": []}}


def substitutions(body: bytes, part_number: str) -> list[dict]:
    """Parse direct replacement direction and qualifications; reject ambiguous or invalid identities."""
    doc = document(body)
    modals = doc.xpath('//*[@id="modal-alts-partlists"]')
    if len(modals) != 1 or not modals[0].xpath('.//*[@data-env="mobile"]'):
        fail("The source no longer exposes a recognized relationships modal.")
    rows = modals[0].xpath('.//*[@data-env="mobile"]//*[@id="accordion-substitutions"]//li')
    if not rows and modals[0].xpath('.//*[@id="accordion-desktop-substitutions"]//li'):
        fail("The source changed the mobile substitution structure.")
    result = []
    for row in rows:
        fields = {}
        for label in row.xpath('.//span[contains(concat(" ",normalize-space(@class)," ")," fw-b ")]'):
            name = plain(label)
            if name in ("Sub From", "Sub To", "Description", "Quantity"):
                values = label.getparent().getparent().xpath("./div[2]")
                if len(values) != 1 or name in fields:
                    fail("The source changed a substitution field.")
                fields[name] = plain(values[0])
        if set(fields) != {"Sub From", "Sub To", "Description", "Quantity"}:
            fail("The source changed a substitution row.")
        if (fields["Sub From"] == "-") == (fields["Sub To"] == "-"):
            fail("The source returned ambiguous substitution direction.")
        old = identifier(part_number if fields["Sub From"] == "-" else fields["Sub From"], part=True)
        new = identifier(part_number if fields["Sub To"] == "-" else fields["Sub To"], part=True)
        if old == new or UNSUPPLIED_PARTS.intersection((old, new)):
            fail("The source returned an invalid substitution identity.")
        result.append(
            {
                "kind": "replacement",
                "from_part_ref": reference("part", old),
                "to_part_ref": reference("part", new),
                "from_part_number": old,
                "to_part_number": new,
                "source_wording": "; ".join(
                    (f"{key}: {fields[key]}" for key in ("Sub From", "Sub To", "Description", "Quantity"))
                ),
                "qualifications": [
                    f"Description: {fields['Description']}",
                    f"Catalog quantity: {fields['Quantity']}",
                ],
            }
        )
    return result
