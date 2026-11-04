"""Bounded catalog retrieval graph and observed tool-selection state."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, after_agent, before_agent, before_model
from langchain.agents.middleware.types import ToolCallRequest
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from pydantic import ValidationError

from ..diagnostics import record_model_usage
from .handoff import PartsHandoff, catalog_choices_handoff, resolve_handoff_urls
from .prompts import CATALOG_PROMPT
from .schemas import TOOL_RESPONSE_MODELS
from .tools import OPERATIONS, TOOL_REQUEST_MODELS, PartsTools, create_langchain_tools, tool_name


def plausible_service_assembly(label: str, part_query: str) -> bool:
    """Recognize explicit parent-system overlap without guessing part identity."""

    def words(text: str) -> set[str]:
        """Normalize component words for the bounded assembly-title overlap check."""
        return set(re.findall(r"[a-z]+", text.casefold().replace("cylinders", "cylinder")))

    requested, assembly = words(part_query), words(label)
    if "dia kit" in label.casefold() or "index" in assembly or "stocking" in assembly:
        return False
    parents = {"radiator", "cylinder", "pump", "brake", "cooler", "harness", "track"}
    if not (requested & assembly & parents):
        return False
    if "cylinder" in requested:
        for function in ("lift", "bucket", "boom", "steering"):
            if function in requested and function not in assembly:
                return False
    if "pump" in requested:
        for function in ("hydraulic", "hydrostatic"):
            if function in requested and function not in assembly:
                return False
    return True


# Specialist middleware controls tool visibility, catalog selection, and pending assembly inspection.
class PartsToolBoundary(AgentMiddleware):
    """Enforce tools and observed catalog scope independently of model text."""

    def __init__(
        self,
        selected_machine_label: str | None = None,
        machine_query: str | None = None,
        client: PartsTools | None = None,
        part_query: str | None = None,
    ) -> None:
        self.selected_machine_label = selected_machine_label
        self.machine_query = machine_query
        self.machine_ref: str | None = None
        self.part_refs: set[str] = set()
        self.awaiting_selection = False
        self.client = client
        self.part_query = part_query
        self.assembly_labels: dict[str, str] = {}
        self.pending_assemblies: set[str] = set()

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """Expose synchronous model tools allowed by the current catalog-selection and inspection state."""
        return record_model_usage(handler(request.override(tools=self._available_tools(request.tools))))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Expose asynchronous model tools allowed by the current catalog-selection and inspection state."""
        return record_model_usage(await handler(request.override(tools=self._available_tools(request.tools))))

    def _available_tools(self, tools: list[Any]) -> list[Any]:
        # Do not invite calls that the execution boundary would reject. Discovery
        # remains available for pagination or live fallback until a choice is needed.
        if self.awaiting_selection or (
            self.client and self.client.budget.calls >= self.client.budget.max_calls
        ):
            return []
        if self.pending_assemblies:
            # Search hits are not verified occurrences. Inspect a plausible
            # returned parent before spending the remaining budget searching again.
            return [tool for tool in tools if tool_name(tool) == "get_assembly"]
        allowed = set() if self.machine_ref is not None else {"search_machines"}
        if self.machine_ref is not None:
            allowed.update({"list_groups", "search_catalog", "get_assembly"})
        if self.part_refs:
            allowed.add("get_part_relationships")
        return [tool for tool in tools if tool_name(tool) in allowed]

    def _rejection(self, request: Any) -> ToolMessage | None:
        name = request.tool_call["name"]
        arguments = request.tool_call["args"]
        invalid = False
        if name in TOOL_REQUEST_MODELS:
            try:
                TOOL_REQUEST_MODELS[name].model_validate(arguments)
            except ValidationError:
                invalid = True
        if name not in OPERATIONS:
            reason = "tool_not_allowed"
        elif invalid:
            reason = "invalid_input"
        elif name == "search_machines" and self.machine_ref is not None:
            reason = "machine_selection_locked"
        elif (
            name == "search_machines"
            and self.machine_query is not None
            and arguments.get("query") != self.machine_query
        ):
            reason = "machine_query_changed"
        elif self.awaiting_selection:
            reason = "machine_selection_required"
        elif name in {"list_groups", "search_catalog", "get_assembly"} and (
            self.machine_ref is None or arguments.get("machine_ref") != self.machine_ref
        ):
            reason = "machine_selection_required"
        elif name == "get_part_relationships" and arguments.get("part_ref") not in self.part_refs:
            reason = "observed_part_required"
        elif self.pending_assemblies and name != "get_assembly":
            reason = "assembly_inspection_required"
        elif self.pending_assemblies and arguments.get("assembly_ref") not in self.pending_assemblies:
            reason = "assembly_inspection_required"
        elif (
            name == "get_assembly"
            and self.part_query
            and re.search(
                r"\bPICTORIAL INDEX\b",
                self.assembly_labels.get(arguments.get("assembly_ref", ""), ""),
                re.IGNORECASE,
            )
            and not re.search(r"\b(?:index|diagram|illustration)\b", self.part_query, re.IGNORECASE)
        ):
            reason = "navigation_index_not_parts"
        elif (
            name == "get_assembly"
            and self.part_query
            and re.search(
                r"\bDIA KIT\b", self.assembly_labels.get(arguments.get("assembly_ref", ""), ""), re.IGNORECASE
            )
            and not re.search(
                r"\b(?:diagnostic|accessory|dia)\b|(?<!seal )(?<!repair )(?<!rebuild )\bkit\b",
                self.part_query,
                re.IGNORECASE,
            )
        ):
            reason = "assembly_scope_conflict"
        else:
            return None
        message = (
            "A pictorial index is navigation, not a parts occurrence table. "
            "Choose a returned service assembly for the requested component while budget remains."
            if reason == "navigation_index_not_parts"
            else "Inspect a returned assembly matching the requested parent before searching again."
            if reason == "assembly_inspection_required"
            else "This is a diagnostic/accessory kit, not the requested service component. "
            "Choose a matching service assembly or search its parent system while budget remains. "
            "This is a scope check, not a source outage or a request for machine details."
            if reason == "assembly_scope_conflict"
            else "Return clarification to the host."
        )
        payload = (
            self.client.reject_tool(name, reason, message)
            if self.client
            else {"error": {"code": reason, "retryable": False, "message": message}}
        )
        return ToolMessage(
            content=json.dumps(payload),
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    def _record(self, request: Any, result: Any) -> Any:
        operation = request.tool_call["name"]
        if operation == "search_machines":
            self.machine_ref = None
            self.part_refs.clear()
            self.assembly_labels.clear()
            self.pending_assemblies.clear()
            self.awaiting_selection = False
        elif operation == "get_assembly":
            # A source failure must not trap the model in an automatic retry.
            self.pending_assemblies.clear()
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result
        try:
            payload = json.loads(result.content) if isinstance(result.content, str) else None
            if not isinstance(payload, dict):
                raise ValueError("Invalid tool envelope")
            if isinstance(payload, dict) and "error" in payload:
                return result
            TOOL_RESPONSE_MODELS[operation].model_validate(payload)
        except (ValueError, TypeError, ValidationError):
            return ToolMessage(
                content=json.dumps({"error": {"code": "invalid_tool_response", "retryable": False}}),
                name=operation,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        if (
            request.tool_call["name"] in {"search_catalog", "get_assembly"}
            and isinstance(result, ToolMessage)
            and isinstance(result.content, str)
        ):
            if "error" not in payload:
                rows = payload["data"].get("matches", payload["data"].get("occurrences", []))
                self.part_refs.update(row["part_ref"] for row in rows if row.get("part_ref"))
                self.assembly_labels.update(
                    {
                        row["assembly_ref"]: row["assembly_label"]
                        for row in rows
                        if row.get("assembly_ref") and row.get("assembly_label")
                    }
                )
                if operation == "search_catalog" and self.part_query:
                    plausible = {
                        row["assembly_ref"]
                        for row in rows
                        if row.get("assembly_ref")
                        and plausible_service_assembly(row.get("assembly_label") or "", self.part_query)
                    }
                    # Broad parent overlap across several assemblies is ambiguous;
                    # keep refinement available rather than forcing an arbitrary one.
                    self.pending_assemblies = plausible if len(plausible) == 1 else set()
        if request.tool_call["name"] == "search_machines" and isinstance(result, ToolMessage):
            if isinstance(result.content, str):
                if "error" not in payload:
                    machines = payload["data"]["machines"]
                    self.awaiting_selection = payload["total"] > 1 and self.selected_machine_label is None
                    selected = (
                        [row for row in machines if row["label"] == self.selected_machine_label]
                        if self.selected_machine_label is not None
                        else machines
                        if (
                            len(machines) == 1
                            and payload["outcome"] in {"ok", "partial"}
                            and not payload["truncated"]
                            and not request.tool_call["args"].get("offset")
                            and (
                                self.machine_query is None
                                or request.tool_call["args"].get("query") == self.machine_query
                            )
                        )
                        else []
                    )
                    # A sole observed candidate permits research, not a claim of identity or fitment.
                    # Partial index/source coverage remains in the evidence returned to the parent.
                    if self.selected_machine_label is not None and len(selected) != 1:
                        raise ValueError(
                            "Selected catalog label must uniquely match an observed candidate in this brand"
                        )
                    if len(selected) == 1:
                        self.machine_ref = selected[0]["machine_ref"]
        return result

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Enforce catalog and evidence preconditions before a synchronous specialist tool call."""
        rejected = self._rejection(request)
        return rejected if rejected is not None else self._record(request, handler(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Enforce catalog and evidence preconditions before an asynchronous specialist tool call."""
        rejected = self._rejection(request)
        return rejected if rejected is not None else self._record(request, await handler(request))


# Graph construction bootstraps discovery and restores source handles in the final handoff.
async def retrieve_parts(
    client: PartsTools,
    model: Any,
    *,
    machine_query: str,
    part_query: str,
    selected_machine_label: str | None = None,
    brand: str | None = None,
    config: Any = None,
) -> PartsHandoff:
    """Invoke a fresh specialist in the caller's turn runtime and validate its handoff."""
    child = create_parts_retriever(
        client,
        model,
        machine_query=machine_query,
        part_query=part_query,
        selected_machine_label=selected_machine_label,
        brand=brand,
    )
    result = await child.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "machine_query": machine_query,
                            "part_query": part_query,
                            "explicit_user_selected_machine_label": selected_machine_label,
                        }
                    ),
                }
            ]
        },
        config=config,
    )
    return PartsHandoff.model_validate(result["structured_response"])


def create_parts_retriever(
    client: PartsTools,
    model: Any,
    *,
    selected_machine_label: str | None = None,
    machine_query: str | None = None,
    part_query: str | None = None,
    brand: str | None = None,
) -> Any:
    """Compile a catalog specialist with fresh lookup state and a strict evidence handoff."""
    url_handles: dict[str, str] = {}
    parts_tools = create_langchain_tools(client, url_handles=url_handles)
    boundary = PartsToolBoundary(selected_machine_label, machine_query, client, part_query)
    discovery_tool = next(tool for tool in parts_tools if tool.name == "search_machines")

    @before_agent
    async def discover_machine(state: Any, runtime: Any) -> dict[str, Any] | None:
        # The host prescribes this exact operation. Execute it once without asking the
        # model to regenerate its arguments; keep the normal tool callbacks and budget.
        """Bootstrap discovery from a trusted machine query before the first specialist model invocation."""
        if machine_query is None:
            return None
        call = ToolCall(
            name="search_machines",
            args={"query": machine_query, "brand": brand, "limit": 200},
            id="host_machine_discovery",
            type="tool_call",
        )
        request = ToolCallRequest(tool_call=call, tool=discovery_tool, state=state, runtime=runtime)

        async def execute(request: ToolCallRequest) -> Any:
            """Invoke the discovery tool using the middleware-validated tool-call request."""
            return await discovery_tool.ainvoke(request.tool_call)

        response = await boundary.awrap_tool_call(request, execute)
        return {"messages": [AIMessage(content="", tool_calls=[call]), response]}

    @before_model(can_jump_to=["end"])
    def clarify_catalog(state: Any, runtime: Any) -> dict[str, Any] | None:
        # Catalog choices and their evidence are already structured. This terminal
        # branch needs no model interpretation or another discovery/parts request.
        """Return a deterministic catalog-choice handoff when observed candidates require user selection."""
        if not boundary.awaiting_selection and boundary.machine_ref is not None:
            return None
        discovery = next(
            message
            for message in reversed(state["messages"])
            if isinstance(message, ToolMessage) and message.name == "search_machines"
        )
        if not isinstance(discovery.content, str):
            raise ValueError("Machine discovery must return a JSON string envelope")
        payload = client.resolve_handles(json.loads(discovery.content))
        if "error" in payload:
            handoff = PartsHandoff(key_facts=[payload["error"]["message"]], sources=[], store_url="")
        else:
            handoff = catalog_choices_handoff(payload)
        return {
            "structured_response": handoff,
            "jump_to": "end",
            "messages": [AIMessage(content=handoff.model_dump_json(), name="parts-catalog")],
        }

    @after_agent
    def resolve_sources(state: Any, runtime: Any) -> dict[str, Any]:
        """Validate the structured handoff and restore exact observed source URLs from child-local handles."""
        handoff = resolve_handoff_urls(PartsHandoff.model_validate(state["structured_response"]), url_handles)
        products = {
            row.get("product_url")
            for observation in client.observations
            for row in observation["response"].get("data", {}).get("matches", [])
            + observation["response"].get("data", {}).get("occurrences", [])
        }
        if handoff.store_url and handoff.store_url not in products:
            raise ValueError("store_url must be an observed product URL")
        final = state["messages"][-1]
        if not isinstance(final, AIMessage) or final.tool_calls:
            raise ValueError("The specialist did not finish with a structured handoff")
        return {
            "structured_response": handoff,
            "messages": [final.model_copy(update={"content": handoff.model_dump_json()})],
        }

    discovery_instruction = (
        "The host has already called search_machines with the trusted machine_query; its result is in the "
        "messages. Use that evidence without repeating the same search. "
        if machine_query is not None
        else "First call search_machines with the machine query supplied in the request. "
    )
    return create_agent(
        model=model,
        response_format=ProviderStrategy(PartsHandoff, strict=True),
        tools=parts_tools,
        system_prompt=CATALOG_PROMPT
        + "\nTrusted host lookup context: "
        + json.dumps(
            {
                "machine_query": machine_query,
                "part_query": part_query,
                "explicit_user_selected_machine_label": selected_machine_label,
            }
        )
        + "\n"
        + discovery_instruction
        + (
            "If an explicit label is supplied, use only the machine_ref attached to that exact returned label. "
            "Copy reference handles exactly. Use short search nouns derived from the trusted part_query, "
            "preserve its qualifiers when matching rows, inspect relevant assembly evidence, "
            "then return the structured JSON handoff with distinct part entries and notes in key_facts, "
            "observed product/assembly links in sources, and the identified part's product URL in store_url "
            "when available. "
            "Tool source_url, assembly_url, product_url and metadata.diagram_urls fields contain short url_N handles. "
            "Copy those handles exactly into sources, store_url and any facts that cite them; "
            "the host resolves them to the original URLs after your handoff. Never construct a URL. "
            "Do not restate the full evidence envelope: the host retains it separately."
        ),
        middleware=[discover_machine, clarify_catalog, boundary, resolve_sources],
        name="parts-catalog",
    )
