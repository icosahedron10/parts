"""Observed model constraints and deterministic lexical ranking, entirely offline."""

import re
from functools import lru_cache
from urllib.parse import unquote, urlsplit

from rapidfuzz.fuzz import token_set_ratio

STOP_WORDS = set(
    "i have a an the my our case ih machine model need for from built in year of and with looking".split()
)

CONCEPTS = {
    "excavator": ("excavator", "excavators", "digger", "diggers", "trackhoe"),
    "dozer": ("dozer", "dozers", "bulldozer", "bulldozers"),
    "backhoe": ("backhoe", "backhoes", "back hoe"),
    "skidsteer": ("skid steer", "skid-steer", "skidsteer", "skidsteers", "skid loader"),
    "trackloader": ("compact track loader", "tracked loader", "track loader", "ctl"),
    "wheelloader": ("wheel loader", "wheel-loader", "wheeled loader", "front end loader", "payloader"),
    "longtrack": ("long track", "lt"),
    "widetrack": ("wide track", "wt"),
    "longreach": ("long reach", "lr"),
    "longcarriage": ("long carriage", "lc"),
    "lowgroundpressure": ("low ground pressure", "lgp"),
    "tractor": ("tractor", "tractors"),
    "combine": ("combine", "combines"),
    "roundbaler": ("round baler", "round balers"),
}


def normalize(value: str) -> str:
    """Normalize model spacing and punctuation without absorbing a year into a model code."""
    value = value.casefold()
    # Users may spell the model prefix as separate letters: C X 350 or C-X350.
    value = re.sub(
        r"\b((?:[a-z][\s.-]+){1,4}[a-z])[\s.-]*(?=\d)", lambda match: re.sub(r"[\s.-]+", "", match[0]), value
    )
    # CX 350 B and CX-350B are spelling variants, not new models.
    value = re.sub(
        r"\b([a-z]{1,3})[ -]+(\d{2,4})\s*([a-z]?)\b",
        lambda match: (
            match[0]
            if (match[1] in STOP_WORDS or re.fullmatch(r"(?:19|20)\d{2}", match[2]))
            else "".join(match.groups())
        ),
        value,
    )
    return " ".join(re.findall(r"[a-z0-9]+(?:\.[0-9]+)*", value))


def features(value: str) -> set[str]:
    """Extract normalized query terms and observed equipment concepts used by lexical ranking."""
    normalized = normalize(value)
    terms = set(normalized.split()) - STOP_WORDS
    for concept, aliases in CONCEPTS.items():
        if any(re.search(r"\b" + re.escape(normalize(alias)) + r"\b", normalized) for alias in aliases):
            terms.add("type:" + concept)
    return terms


def year_matches(dates: str | None, year: int) -> bool:
    """Check an observed production range while leaving missing or unrecognized dates unresolved."""
    if not dates:
        return True  # Unknown dates are not evidence of incompatibility.
    match = re.fullmatch(r"\d{2}/(\d{2})\s*-\s*(?:\d{2}/(\d{2}))?", dates)
    if not match:
        return True

    def full_year(value: str) -> int:
        """Expand source two-digit years into the observed 1940 through 2039 catalog window."""
        number = int(value)
        return 1900 + number if number >= 40 else 2000 + number

    start = full_year(match[1])
    end = full_year(match[2]) if match[2] else None
    # Open end remains unknown; it is not an assertion of current production.
    return year >= start and (end is None or year <= end)


CATEGORY_CONCEPTS = {
    "crawler-excavators": "excavator",
    "mini-crawler-excavator": "excavator",
    "midi-excavators": "excavator",
    "wheeled-excavators": "excavator",
    "crawler-dozers": "dozer",
    "loader-backhoes": "backhoe",
    "skid-steer-loaders": "skidsteer",
    "compact-track-loaders": "trackloader",
    "mini-track-loaders": "trackloader",
    "wheel-loaders": "wheelloader",
    "compact-wheel-loaders": "wheelloader",
    "forklifts": "forklift",
    "telescopic-handlers": "telehandler",
    "compaction": "compaction",
    "motor-grader": "grader",
    "trenchers-layers": "trencher",
    "dump-trucks": "dumptruck",
    "loader-tractors": "loadertractor",
    "articulated-loader": "articulatedloader",
}

EQUIPMENT_ALIASES = {
    "forklift": ("forklift", "forklifts", "fork lift"),
    "telehandler": ("telehandler", "telehandlers", "telescopic handler", "telescopic handlers"),
    "compaction": ("compaction", "compactor", "compactors", "roller", "rollers", "steamroller"),
    "grader": ("grader", "graders", "motor grader"),
    "trencher": ("trencher", "trenchers"),
    "dumptruck": ("dump truck", "dump trucks"),
    "loadertractor": ("loader tractor", "loader tractors"),
    "articulatedloader": ("articulated loader", "articulated loaders"),
}

VARIANT_ALIASES = {
    "extralongtrack": ("extra long track", "xlt"),
    "shortswing": ("short radius", "short swing", "sr"),
    "narrowcarriage": ("narrow carriage", "narrow long carriage", "nlc"),
    "allwheeldrive": ("all wheel drive", "awd"),
    "fourwheeldrive": ("four wheel drive", "4wd"),
    "twowheeldrive": ("two wheel drive", "2wd"),
    "pat": ("pat", "power angle tilt"),
    "rops": ("rops",),
    "ep": ("ep",),
    "silage": ("silage", "silage pack"),
}

MODEL_CODE = re.compile(r"(?:[a-z]{1,10}\d+(?:\.\d+)?[a-z0-9]*|\d+[a-z][a-z0-9]*|\d+)\Z")

MODEL_PARTS = re.compile(r"([a-z]*)(\d+(?:\.\d+)?)([a-z0-9]*)\Z")


def _category(record: dict) -> str:
    # The route is validated before indexing. Do not derive facts from discovery queries.
    return unquote(urlsplit(record["machine"]["source_url"]).path.split("/")[6])


def document_text(record: dict) -> str:
    """Use only observed label, category, qualifications and production-date text."""
    row = record["machine"]
    lines = [row["label"], "Equipment category: " + _category(record).replace("-", " ")]
    if row.get("qualifications"):
        lines.append("Catalog qualifications: " + "; ".join(row["qualifications"]))
    if row.get("production_dates"):
        lines.append("Observed production dates: " + row["production_dates"])
    return "\n".join(lines)


def _requirements(text: str) -> set[str]:
    text = re.sub(r"\bmy\s+((?:19|20)\d{2})\b", r"\1", text)
    normalized = normalize(text)
    result = {term for term in features(text) if term.startswith("type:")}
    for concept, aliases in EQUIPMENT_ALIASES.items():
        if any(re.search(r"\b" + re.escape(normalize(alias)) + r"\b", normalized) for alias in aliases):
            result.add("type:" + concept)
    for concept, aliases in VARIANT_ALIASES.items():
        if any(re.search(r"\b" + re.escape(normalize(alias)) + r"\b", normalized) for alias in aliases):
            result.add("variant:" + concept)
    for match in re.finditer(r"\b(?:tier\s*|t)([1-5])\s*([ab]?)\b(?!\.\d)", normalized):
        result.add("tier:" + match[1])
        if match[2]:
            result.add("tier:" + match[1] + match[2])
    for match in re.finditer(r"\bseries\s+(\d+)\b", normalized):
        result.add("series:" + match[1])
    for match in re.finditer(r"\bstage\s+(v|iv|iii|ii|i|[1-5])\b", normalized):
        stage = {"v": "5", "iv": "4", "iii": "3", "ii": "2", "i": "1"}.get(match[1], match[1])
        result.add("stage:" + stage)
    for match in re.finditer(r"\bmy\s*((?:19|20)?\d{2})\b", normalized):
        value = match[1]
        year = int(value) if len(value) == 4 else (1900 if int(value) >= 40 else 2000) + int(value)
        result.add("modelyear:" + str(year))
    if re.search(r"\belectric\b", normalized):
        result.add("fuel:electric")
    if "variant:extralongtrack" in result:
        result.add("type:longtrack")
    if "variant:narrowcarriage" in result:
        result.add("type:longcarriage")
    return result


def _catalog_model(record: dict) -> str | None:
    prefix = normalize(record["machine"]["label"].split(" - ", 1)[0])
    # Model identifiers occupy the observed label prefix, never a serial/date field.
    first = prefix.split()[0]
    if MODEL_CODE.fullmatch(first):
        return first
    return prefix if prefix not in {"case", "davis", "utility"} else None


def _requested_models(query: str, named_models: set[str]) -> set[str]:
    text = query.casefold()
    # An explicitly introduced engine is a qualification to inspect, not a second
    # machine catalog identifier. Keep the original wording in the specialist task.
    text = re.sub(
        r"(?:\b(?:with|using|powered by|fitted with)\s+|,\s*)(?:an?\s+|the\s+)?"
        r"(?:[a-z0-9][a-z0-9./-]*\s+engine|engine\s+[a-z0-9][a-z0-9./-]*)\b",
        " ",
        text,
    )
    # These numbers qualify a model; they are not themselves model identifiers.
    text = re.sub(
        r"\b(?:tier\s*|t)[1-5]\s*[ab]?\b(?!\.\d)|\bstage\s+(?:[iv]+|[1-5])\b|\bseries\s+\d+\b", " ", text
    )
    text = re.sub(
        r"\b(?:s\s*/\s*n|serial(?:\s+number)?)\s*[:#]?\s*\d+(?:\s*(?:to|through|-)\s*\d+)?", " ", text
    )
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:tons?|tonnes?|hp|horsepower|kw|lbs?|pounds?|speed)\b", " ", text)
    text = re.sub(r"\b(?:19|20)\d{2}\b|\b\d{2}/\d{2}\b|\b[24]\s*wd\b", " ", text)
    text = re.sub(r"\bmy\s*(?:19|20)?\d{2}\b", " ", text)
    normalized = normalize(text)
    # Preserve an unrecognized separated suffix as part of an unknown model;
    # otherwise "CX350 Z" would silently truncate to the observed CX350 code.
    normalized = re.sub(r"\b([a-z]{1,10}\d+|\d{2,})[\s-]+([a-z])\b", r"\1\2", normalized)
    # The established normalizer handles CX 350 B; extend it to one-digit codes.
    normalized = re.sub(r"\b(cx|dh|fh|sr|w)\s+(\d)\s*([a-z]?)\b", r"\1\2\3", normalized)
    named = {name for name in named_models if re.search(r"\b" + re.escape(name) + r"\b", normalized)}
    # MAXI-SNEAKER C is more specific than its observed MAXI-SNEAKER prefix.
    named = {name for name in named if not any(other.startswith(name + " ") for other in named)}
    # A named model such as MAGNUM 250 is one identity, not both MAGNUM 250 and 250.
    for name in sorted(named, key=len, reverse=True):
        normalized = re.sub(r"\b" + re.escape(name) + r"\b", " ", normalized)
    models_requested = named | {term for term in normalized.split() if MODEL_CODE.fullmatch(term)}
    return models_requested


def _model_matches(candidate: str | None, requested: str) -> bool:
    if candidate is None:
        return False
    candidate_parts = MODEL_PARTS.fullmatch(candidate)
    requested_parts = MODEL_PARTS.fullmatch(requested)
    if candidate_parts is None or requested_parts is None:
        return candidate == requested
    if candidate_parts.group(1, 2) != requested_parts.group(1, 2):
        return False
    suffix, request_suffix = candidate_parts[3], requested_parts[3]
    # A bare family such as CX350 retains letter variants. A fully specified code
    # only allows explicitly attached carriage/reach variants, never fuzzy codes.
    return (
        not request_suffix
        or suffix == request_suffix
        or suffix in {request_suffix + ending for ending in ("lc", "lr", "nlc", "sr")}
    )


@lru_cache(maxsize=8)
def _compile_model_aliases(model_codes: frozenset[str]) -> tuple[re.Pattern[str], dict[str, str]]:
    """Compile spelling aliases only for identifiers observed in this snapshot."""
    aliases = {}
    for code in sorted(model_codes, key=lambda value: (len(value), value)):
        # Expanded storefronts contain both FARMALL 110 U and FARMALL 110U.
        # Share spelling normalization, but retain every catalog as a candidate.
        aliases.setdefault(re.sub(r"[\s-]+", "", code), code)
    alternatives = [
        r"[\s-]*".join(re.escape(character) for character in compact)
        for compact in sorted(aliases, key=lambda value: (-len(value), value))
    ]
    # Longest code first, with complete token boundaries. A separate single-letter
    # suffix must join a known longer code, never truncate to its shorter prefix.
    pattern = re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(alternatives or [r"(?!)"]) + r")(?![a-z0-9]|[\s-]+[a-z]\b)"
    )
    return pattern, aliases


def _canonical_query(query: str, model_pattern: re.Pattern[str], aliases: dict[str, str]) -> str:
    value = model_pattern.sub(lambda match: aliases[re.sub(r"[\s-]+", "", match[0])], query.casefold())
    value = re.sub(
        r"\b(?:case\s+ih|case\s+construction|casece|caseih|case|new\s*holland(?:ag|ce)?(?:\s+(?:agriculture|construction))?|nh)\b[\s-]*",
        "",
        value,
    )
    # Preserve dates, variant punctuation and words around the matched model.
    # The same text feeds model constraints and lexical ordering.
    return " ".join(value.split())


def prepare(records):
    prepared = []
    for record in records:
        doc = document_text(record)
        requirements = _requirements(doc)
        category = CATEGORY_CONCEPTS.get(_category(record))
        if category:
            requirements.add("type:" + category)
        prepared.append(
            {
                "record": record,
                "model": _catalog_model(record),
                "requirements": requirements,
                "fuels": set(re.findall(r"\b(?:electric|diesel)\b", doc.casefold())),
            }
        )
    return prepared


def match(prepared, query):
    models = {p["model"] for p in prepared if p["model"]}
    pattern, aliases = _compile_model_aliases(frozenset(models))
    models = set(aliases.values())
    query = _canonical_query(query, pattern, aliases)
    requirements = _requirements(query)
    requested = _requested_models(query, {m for m in models if not MODEL_CODE.fullmatch(m)})
    if not requested:
        return []
    exact = requested & models
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", query)]
    diesel = bool(re.search(r"\bdiesel\b", query))
    matches = []
    for item in prepared:
        row = item["record"]["machine"]
        model_identity = aliases.get(re.sub(r"[\s-]+", "", item["model"] or ""))
        if (
            requirements <= item["requirements"]
            and all(
                model_identity == model if model in exact else _model_matches(model_identity, model)
                for model in requested
            )
            and not (diesel and item["fuels"] == {"electric"})
            and all(year_matches(row.get("production_dates"), year) for year in years)
        ):
            matches.append((token_set_ratio(normalize(query), normalize(row["label"])), item["record"]))
    return [
        record
        for _, record in sorted(
            matches,
            key=lambda pair: (
                -pair[0],
                pair[1]["machine"]["label"],
                pair[1]["machine"]["machine_ref"]["$ref"]["id"],
            ),
        )
    ]
