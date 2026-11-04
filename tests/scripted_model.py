"""A deterministic local test model exercising the actual parent and retrieval graphs, not inference."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from cnh_parts_agent.agent.handoff import PartsHandoff


# A deterministic local test model exercising the actual parent and retrieval graphs, not inference.
def _lookup_context(messages: list[BaseMessage]) -> dict[str, Any]:
    """Read the child graph's host-written lookup scope without retaining another call's state."""
    for message in messages:
        if message.type == "system" and isinstance(message.content, str):
            marker = "Trusted host lookup context: "
            if marker in message.content:
                return json.JSONDecoder().raw_decode(message.content.split(marker, 1)[1])[0]
    for message in reversed(messages):
        if message.type == "human" and isinstance(message.content, str):
            try:
                context = json.loads(message.content)
            except ValueError:
                continue
            if isinstance(context, dict) and "machine_query" in context:
                return context
    return {}


def _source_urls(value: Any) -> list[str]:
    """Collect observed customer and evidence links without inventing a store URL."""
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_url", "assembly_url", "product_url"} and isinstance(item, str) and item:
                urls.append(item)
            elif isinstance(item, (dict, list)):
                urls.extend(_source_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_source_urls(item))
    return list(dict.fromkeys(urls))


# Deterministic specialist responses exercise the real tool boundary without inference.
class ScriptedCatalogModel(BaseChatModel):
    """Test-only provider with tool binding and responses driven by observed API evidence."""

    machine_query: str = "CX350"
    part_query: str = "radiator"
    selected_machine_label: str | None = None
    relationship_part_number: str | None = None
    bound_tool_names: list[str] = Field(default_factory=list, exclude=True)
    bound_response_format: dict[str, Any] | None = Field(default=None, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "parts-scripted-test-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """Record exposed tool names for deterministic graph demonstrations and return this test model."""
        self.bound_tool_names[:] = [tool.name if hasattr(tool, "name") else tool["name"] for tool in tools]
        self.bound_response_format = kwargs.get("response_format")
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        context = _lookup_context(messages)
        machine_query = context.get("machine_query") or self.machine_query
        part_query = context.get("part_query") or self.part_query
        selected_machine_label = context.get(
            "explicit_user_selected_machine_label", self.selected_machine_label
        )
        observations = []
        for message in messages:
            if isinstance(message, ToolMessage) and isinstance(message.content, str):
                observations.append({"operation": message.name, "response": json.loads(message.content)})

        def call(name: str, **arguments: Any) -> ChatResult:
            """Construct a deterministic tool-call response with its matching call identifier."""
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": name,
                                    "args": arguments,
                                    "id": f"demo_{len(observations)}",
                                }
                            ],
                        )
                    )
                ]
            )

        def finish(facts: list[str], *, store_url: str = "") -> ChatResult:
            """Build a scripted structured handoff from observed fixture evidence and exact returned links."""
            for observation in observations:
                payload = observation["response"]
                if "error" in payload:
                    continue
                facts.append(
                    f"{observation['operation']}: outcome {payload['outcome']}; "
                    f"coverage {payload['coverage']['status']}; "
                    f"more pages {'available' if payload['truncated'] else 'not returned'}; "
                    "Observation dates are retained below."
                )
                if payload["coverage"].get("reason"):
                    facts.append(
                        f"{observation['operation']} coverage qualification: {payload['coverage']['reason']}"
                    )
                facts.extend(
                    f"{observation['operation']} source note: {note}"
                    for note in (payload["data"].get("metadata") or {}).get("source_notes", [])
                )
                facts.extend(
                    f"{observation['operation']} source observed at {item['observed_at']}."
                    for item in payload["evidence"]
                )
            handoff = PartsHandoff(key_facts=facts, sources=_source_urls(observations), store_url=store_url)
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=handoff.model_dump_json()))]
            )

        if not observations:
            return call("search_machines", query=machine_query)
        if "error" in observations[-1]["response"]:
            error = observations[-1]["response"]["error"]
            return finish(
                [
                    f"Lookup unavailable: {error['code']}: {error['message']}",
                    f"Retryable: {error['retryable']}; retry delay: {error.get('retry_after')} seconds. "
                    "No automatic retry was attempted.",
                ]
            )
        machines = observations[0]["response"]["data"]["machines"]
        selected = (
            [row for row in machines if row["label"] == selected_machine_label]
            if selected_machine_label
            else machines
            if len(machines) == 1
            else []
        )
        if len(selected) != 1:
            return finish(
                [
                    "Clarification required: Which exact model/variant and production period is yours?",
                    *[
                        f"Catalog candidate: {row['label']}; "
                        f"production dates: {row.get('production_dates') or 'not returned'}; "
                        f"qualifications: {'; '.join(row.get('qualifications', [])) or 'none returned'}."
                        for row in machines
                    ],
                ]
            )
        machine = selected[0]
        if len(observations) == 1:
            return call(
                "search_catalog",
                machine_ref=machine["machine_ref"],
                query=part_query,
                mode="part_description",
            )
        search = observations[1]["response"]
        refs = list(dict.fromkeys(row["assembly_ref"] for row in search["data"]["matches"]))[:1]
        assemblies = [row for row in observations if row["operation"] == "get_assembly"]
        if len(assemblies) < len(refs):
            return call(
                "get_assembly", machine_ref=machine["machine_ref"], assembly_ref=refs[len(assemblies)]
            )
        candidates = [
            row
            for assembly in assemblies
            for row in assembly["response"]["data"]["occurrences"]
            if part_query.casefold() == row["description"].split(";", 1)[0].strip().casefold()
        ]
        if self.relationship_part_number and not any(
            row["operation"] == "get_part_relationships" for row in observations
        ):
            part_refs = {
                row["part_ref"]
                for row in candidates
                if row["part_number"] == self.relationship_part_number and row.get("part_ref")
            }
            if len(part_refs) == 1:
                return call("get_part_relationships", part_ref=next(iter(part_refs)))
        facts = [
            "Catalog evidence is available."
            if candidates
            else "No candidate found in the inspected evidence.",
            f"{'Selected' if selected_machine_label else 'Candidate'} catalog: {machine['label']}; "
            f"production dates: {machine.get('production_dates') or 'not returned'}; "
            f"qualifications: {'; '.join(machine.get('qualifications', [])) or 'none returned'}.",
        ]
        for row in candidates:
            facts.append(
                f"Part {row['part_number']}: {row['description']} "
                f"Callout: {row['callout']}; catalog quantity: {row['catalog_quantity']}. "
                f"Notes: {'; '.join(row['notes']) or 'none returned'}. "
                f"Serial restrictions: {'; '.join(row['serial_restrictions']) or 'none returned'}. "
                f"Qualifications: {'; '.join(row.get('qualifications', [])) or 'none returned'}."
            )
        for observation in observations:
            for row in observation["response"]["data"].get("relationships", []):
                facts.append(
                    f"Source replacement direction: {row['from_part_number']} -> {row['to_part_number']}. "
                    f"Source wording: {row['source_wording']}. "
                    f"Qualifications: {'; '.join(row.get('qualifications', [])) or 'none returned'}. "
                    "This direction does not establish equivalence or serial-specific fitment."
                )
        facts.extend(
            [
                "* Serial-specific fitment and inventory are unverified. Confirm applicability before ordering.",
                "Only up to two assemblies and their returned pages were inspected; retain response coverage.",
            ]
        )
        product_urls = {row["product_url"] for row in candidates if row.get("product_url")}
        distinct_parts = {row["part_number"] for row in candidates}
        store_url = next(iter(product_urls)) if len(distinct_parts) == 1 and len(product_urls) == 1 else ""
        return finish(facts, store_url=store_url)


# The scripted parent delegates research and explains observed specialist evidence.
class ScriptedSupervisorModel(BaseChatModel):
    """Deterministic conversational fixture; exercises delegation and synthesis without inference."""

    lookup_requests: list[dict[str, str]] | None = None
    direct_response: str | None = None
    bound_tool_names: list[str] = Field(default_factory=list, exclude=True)
    bound_response_format: dict[str, Any] | None = Field(default=None, exclude=True)
    observed_messages: list[BaseMessage] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "parts-scripted-supervisor-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """Record exposed tool names for deterministic graph demonstrations and return this test model."""
        self.bound_tool_names[:] = [tool.name if hasattr(tool, "name") else tool["name"] for tool in tools]
        self.bound_response_format = kwargs.get("response_format")
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.observed_messages[:] = messages
        current = max(index for index, message in enumerate(messages) if message.type == "human")
        user = str(messages[current].content)
        outputs = [message for message in messages[current + 1 :] if isinstance(message, ToolMessage)]
        if self.direct_response is not None:
            response = self.direct_response
            lookups: list[dict[str, str]] = []
        elif self.lookup_requests is not None:
            response = "Tell me the machine model and the part you need."
            lookups = self.lookup_requests
        else:
            response, lookups = _scripted_request(user, messages[:current])
        if len(outputs) < len(lookups):
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "lookup_parts",
                        "args": lookups[len(outputs)],
                        "id": f"delegate_parts_{len(outputs)}",
                    }
                ],
            )
        else:
            if outputs:
                response = "\n\n".join(
                    _scripted_answer(json.loads(str(output.content))) for output in outputs
                )
            message = AIMessage(content=response)
        return ChatResult(generations=[ChatGeneration(message=message)])


def _parse_lookup(text: str) -> dict[str, str] | None:
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("machine_query") and payload.get("part_query"):
        return {key: payload[key] for key in ("machine_query", "part_query")}
    match = re.search(
        r"(?:find|look up|i need|i want|can you find)?\s*(?:an?\s+)?(.+?)\s+for\s+(?:(?:my|the|an?)\s+)?(.+)",
        text.strip().rstrip(".?!"),
        flags=re.IGNORECASE,
    )
    if match:
        return {"part_query": match[1].strip(), "machine_query": match[2].strip()}
    return None


def _scripted_request(user: str, history: list[BaseMessage]) -> tuple[str, list[dict[str, str]]]:
    """A few explicit demo phrases; live language understanding belongs to the real model."""
    lowered = user.strip().lower().rstrip(".!?")
    if lowered in {"hi", "hello", "hey", "help", "what can you do"}:
        return (
            "I can help identify catalog parts and explain the evidence. What machine and part are you working on?",
            [],
        )
    if lowered in {"thanks", "thank you"}:
        return "You're welcome. Let me know if you need another part checked.", []
    if "quantity" in lowered or "in stock" in lowered or "inventory" in lowered:
        return (
            "Catalog quantity describes how many of an item appear in the assembly. "
            "It does not show inventory or confirm serial-specific fitment."
        ), []
    if "incl" in lowered and ("mean" in lowered or "explain" in lowered):
        return (
            "An inclusion note describes what the listed assembly includes. "
            "Keep it attached to that part; it does not establish that separate components are interchangeable."
        ), []
    lookup = _parse_lookup(user)
    if lookup is None and (
        "use" in lowered or "select" in lowered or "that's" in lowered or "that is" in lowered
    ):
        for message in reversed(history):
            if message.type == "human":
                lookup = _parse_lookup(str(message.content))
                if lookup:
                    break
    if lookup:
        return "", [lookup]
    return "Tell me the machine model and the part you're looking for so I can check the catalog.", []


def _scripted_answer(payload: dict[str, Any]) -> str:
    if "error" in payload:
        return f"I couldn't complete that catalog lookup: {payload['error']['code']}."
    handoff = PartsHandoff.model_validate(payload.get("handoff", payload))
    candidates = [
        fact.removeprefix("Catalog candidate: ")
        for fact in handoff.key_facts
        if fact.startswith("Catalog candidate: ")
    ]
    if candidates:
        return (
            "The available catalog choices are:\n\n"
            + "\n".join(f"- {candidate}" for candidate in candidates)
            + "\n\n* These are catalog candidates; exact fitment remains unverified."
            + "\n\nWhich exact catalog matches your machine?"
        )
    relevant = [
        fact
        for fact in handoff.key_facts
        if not fact.startswith(
            ("search_machines:", "search_catalog:", "get_assembly:", "get_part_relationships:")
        )
    ]
    answer = "The catalog evidence shows:\n\n" + "\n\n".join(relevant)
    if handoff.sources:
        answer += "\n\nSources: " + ", ".join(handoff.sources)
    return answer
