"""Public orchestrator tool: fresh state and a deadline around the entire specialist."""

import asyncio
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ..diagnostics import Diagnostics, current
from .specialist import retrieve_parts
from .tools import PartsTools


class PartsLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine_query: str = Field(
        min_length=1,
        max_length=200,
        description="User's machine wording, including supplied year, make, suffix and variant.",
    )
    part_query: str = Field(
        min_length=1,
        max_length=1000,
        description="Requested part, preserving size, side, function and other qualifiers.",
    )
    selected_machine_label: str | None = Field(
        default=None, description="Exact catalog label explicitly chosen by the user."
    )
    brand: Literal["casece", "caseih"] | None = None


def make_lookup_parts_tool(*, catalog, model):
    async def lookup_parts(
        machine_query: str,
        part_query: str,
        selected_machine_label: str | None = None,
        brand: str | None = None,
    ) -> str:
        diagnostics = current.get() or Diagnostics()
        token = current.set(diagnostics)
        client = PartsTools(catalog)
        try:
            async with asyncio.timeout(client.budget.remaining_seconds):
                handoff = await retrieve_parts(
                    client,
                    model,
                    machine_query=machine_query,
                    part_query=part_query,
                    selected_machine_label=selected_machine_label,
                    brand=brand,
                    config={"recursion_limit": 24},
                )
            diagnostics.handoffs.append(
                {"machine_query": machine_query, "part_query": part_query, "handoff": handoff.model_dump()}
            )
            return handoff.model_dump_json()
        finally:
            client.handles.clear()
            current.reset(token)

    return StructuredTool.from_function(
        coroutine=lookup_parts,
        name="lookup_parts",
        args_schema=PartsLookup,
        description="Find a part for a machine in the CNH US catalog. Returns cited facts, source URLs and a product URL or clarification.",
    )
