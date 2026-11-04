"""Five narrow tools and lookup-owned budgets, reference handles, and observations."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..catalog.errors import PartsError
from ..catalog.models import DATA_MODELS
from ..diagnostics import current


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pagination(Input):
    limit: int = Field(default=50, ge=1, le=200, strict=True)
    offset: int = Field(default=0, ge=0, strict=True)


class MachineSearch(Pagination):
    query: str = Field(min_length=1, max_length=200)
    brand: Literal["casece", "caseih"] | None = None


class Groups(Input):
    machine_ref: str


class CatalogSearch(Pagination):
    machine_ref: str
    query: str = Field(min_length=1, max_length=120)
    mode: Literal["part_number", "part_description", "assembly_title"]


class Assembly(Pagination):
    machine_ref: str
    assembly_ref: str


class Relationships(Input):
    part_ref: str


TOOL_REQUEST_MODELS = {
    "search_machines": MachineSearch,
    "list_groups": Groups,
    "search_catalog": CatalogSearch,
    "get_assembly": Assembly,
    "get_part_relationships": Relationships,
}
OPERATIONS = tuple(DATA_MODELS)
DESCRIPTIONS = {
    "search_machines": "Find observed catalogs locally. Preserve the user's year, make, model and variants.",
    "list_groups": "List the selected machine's catalog groups.",
    "search_catalog": "Locate parts or assemblies inside the selected catalog; inspect assembly rows before concluding.",
    "get_assembly": "Inspect assembly occurrences, part numbers, quantities, notes and product links.",
    "get_part_relationships": "Read displayed direct substitutions for an already observed part; not interchangeability.",
}


@dataclass
class WorkflowBudget:
    max_calls: int = 8
    timeout_seconds: float = 30
    calls: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def remaining_seconds(self):
        return max(0, self.timeout_seconds - (time.monotonic() - self.started_at))

    def consume(self):
        if self.calls >= self.max_calls or self.remaining_seconds <= 0:
            raise PartsError("workflow_budget_exhausted", "Stop tool work and answer from existing evidence.")
        self.calls += 1


class PartsTools:
    def __init__(self, catalog, *, budget=None):
        self.catalog = catalog
        self.budget = budget or WorkflowBudget(catalog.settings.max_calls, catalog.settings.timeout_seconds)
        self.observations = []
        self.handles = {}

    def encode_references(self, value, field=""):
        if isinstance(value, dict):
            if field.endswith("_ref"):
                for handle, ref in self.handles.items():
                    if ref == value:
                        return handle
                prefix = {"machine": "m", "assembly": "a", "part": "p", "group": "g"}[value["kind"]]
                handle = prefix + str(len(self.handles) + 1)
                self.handles[handle] = json.loads(json.dumps(value))
                return handle
            return {key: self.encode_references(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self.encode_references(item, field) for item in value]
        return value

    def resolve_handles(self, value, field=""):
        if isinstance(value, dict):
            return {key: self.resolve_handles(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_handles(item, field) for item in value]
        if field.endswith("_ref") and isinstance(value, str):
            if value not in self.handles:
                raise PartsError("unknown_lookup_reference", "Copy a reference handle from this lookup.")
            return dict(self.handles[value])
        return value

    def record(self, observation):
        self.observations.append(observation)
        if (diagnostics := current.get()) is not None:
            diagnostics.observations.append(observation)

    def reject_tool(self, operation, code, message):
        try:
            self.budget.consume()
            result = PartsError(code, message).payload()
        except PartsError as exc:
            result = exc.payload()
        self.record({"operation": operation, "response": result})
        return result

    async def tool_call(self, operation, arguments):
        decoded = arguments
        try:
            self.budget.consume()
            if operation not in TOOL_REQUEST_MODELS:
                raise PartsError("unsupported_operation", "Choose one of the five catalog tools.")
            try:
                args = TOOL_REQUEST_MODELS[operation].model_validate(arguments).model_dump()
            except ValidationError:
                raise PartsError("invalid_input", "Arguments do not match the tool schema.") from None
            decoded = self.resolve_handles(args)
            async with asyncio.timeout(self.budget.remaining_seconds):
                result = await self.catalog.execute(operation, decoded)
        except PartsError as exc:
            result = exc.payload()
        self.record({"operation": operation, "arguments": decoded, "response": result})
        return result


def tool_name(tool):
    return tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)


def create_langchain_tools(client: PartsTools, *, url_handles: dict):
    by_url = {}

    def encode(value, field=""):
        if isinstance(value, dict):
            return {key: encode(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [encode(item, field) for item in value]
        if field in {"source_url", "assembly_url", "product_url", "diagram_urls"} and value:
            if value not in by_url:
                handle = "url_" + str(len(by_url) + 1)
                by_url[value] = handle
                url_handles[handle] = value
            return by_url[value]
        return value

    def bind(operation):
        async def invoke(**arguments):
            return encode(client.encode_references(await client.tool_call(operation, arguments)))

        return invoke

    return [
        StructuredTool.from_function(
            coroutine=bind(name),
            name=name,
            description=DESCRIPTIONS[name],
            args_schema=TOOL_REQUEST_MODELS[name],
        )
        for name in OPERATIONS
    ]
