"""Small typed catalog records with evidence and local pagination."""

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

SourceId = str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceReference(StrictModel):
    """Public catalog identity; never an authorization token."""

    source_id: SourceId
    kind: Literal["machine", "group", "assembly", "part"]
    id: str = Field(min_length=1, max_length=256)
    context: str | None = Field(default=None, max_length=256)
    path: str | None = Field(default=None, max_length=2048)


class Machine(StrictModel):
    machine_ref: ResourceReference
    label: str
    production_dates: str | None = None
    qualifications: list[str] = Field(default_factory=list)
    source_url: str


class Group(StrictModel):
    group_ref: ResourceReference
    label: str


class Match(StrictModel):
    kind: Literal["part", "assembly"]
    assembly_ref: ResourceReference
    assembly_label: str | None = None
    label: str
    assembly_url: str
    part_number: str | None = None
    part_ref: ResourceReference | None = None
    description: str | None = None
    catalog_quantity: str | None = None
    callout: str | None = None
    notes: list[str] = Field(default_factory=list)
    serial_restrictions: list[str] = Field(default_factory=list)
    product_url: str | None = None
    qualifications: list[str] = Field(default_factory=list)


class Occurrence(StrictModel):
    occurrence_id: str
    part_number: str
    part_ref: ResourceReference | None = None
    catalog_quantity: str
    callout: str | None = None
    description: str
    notes: list[str] = Field(default_factory=list)
    serial_restrictions: list[str] = Field(default_factory=list)
    product_url: str | None = None
    assembly_url: str
    qualifications: list[str] = Field(default_factory=list)


class Relationship(StrictModel):
    kind: Literal["replacement"]
    from_part_ref: ResourceReference
    to_part_ref: ResourceReference
    from_part_number: str
    to_part_number: str
    source_wording: str
    qualifications: list[str] = Field(default_factory=list)


class Metadata(StrictModel):
    diagram_urls: list[str] = Field(default_factory=list)
    source_pagination: dict[str, int | str] | None = None
    source_notes: list[str] = Field(default_factory=list)


class MachineData(StrictModel):
    machines: list[Machine]
    refinement_fields: list[str] = Field(default_factory=list)
    metadata: Metadata | None = None


class GroupData(StrictModel):
    groups: list[Group]
    model_label: str | None = None
    metadata: Metadata | None = None


class SearchData(StrictModel):
    matches: list[Match]
    metadata: Metadata | None = None


class AssemblyRoute(StrictModel):
    assembly_ref: ResourceReference
    title: str
    group: str
    figure: str | None = None
    observed_path: str | None = None
    assembly_url: str
    stale: bool = False


class DirectoryData(StrictModel):
    assemblies: list[AssemblyRoute]
    metadata: Metadata | None = None


class AssemblyData(StrictModel):
    assembly_ref: ResourceReference | None = None
    assembly_label: str | None = None
    referenced_assemblies: list[AssemblyRoute] = Field(default_factory=list)
    occurrences: list[Occurrence]
    metadata: Metadata | None = None


class RelationshipData(StrictModel):
    relationships: list[Relationship]
    metadata: Metadata | None = None


class Coverage(StrictModel):
    status: Literal["complete_for_scope", "partial", "unknown"]
    reason: str | None = None
    scope: dict[str, str | None] | None = None


class Evidence(StrictModel):
    source_url: str
    observed_at: datetime
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    parser_version: str


DataT = TypeVar("DataT")


class OpResult(StrictModel, Generic[DataT]):
    outcome: Literal["ok", "ambiguous", "not_found", "partial"]
    data: DataT
    coverage: Coverage
    total: int
    truncated: bool = False
    evidence: list[Evidence]


DATA_MODELS = {
    "search_machines": MachineData,
    "list_groups": GroupData,
    "list_assemblies": DirectoryData,
    "search_catalog": SearchData,
    "get_assembly": AssemblyData,
    "get_part_relationships": RelationshipData,
}
ROWS = {
    "search_machines": "machines",
    "list_groups": "groups",
    "list_assemblies": "assemblies",
    "search_catalog": "matches",
    "get_assembly": "occurrences",
    "get_part_relationships": "relationships",
}


def expand_references(value, source_id):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return {**value["$ref"], "source_id": source_id}
        return {key: expand_references(item, source_id) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_references(item, source_id) for item in value]
    return value
