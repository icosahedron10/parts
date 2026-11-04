"""Query-only Deep Agents harness with an enforced one-tool boundary."""

import asyncio
import re
from urllib.parse import urlsplit

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from cnh_parts_agent import make_lookup_parts_tool
from cnh_parts_agent.agent.handoff import PartsHandoff, encode_handoff_urls, resolve_handoff_urls
from cnh_parts_agent.agent.prompts import ORCHESTRATOR_PROMPT
from cnh_parts_agent.agent.tools import tool_name
from cnh_parts_agent.diagnostics import current


class LookupOnly(AgentMiddleware):
    def __init__(self):
        self.calls = 0
        self.url_handles = {}

    async def awrap_model_call(self, request, handler):
        allowed = (
            [tool for tool in request.tools if tool_name(tool) == "lookup_parts"] if not self.calls else []
        )
        return await handler(request.override(tools=allowed))

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "lookup_parts" or self.calls:
            raise ValueError("The eval parent may call only lookup_parts, once per question")
        if request.tool_call["args"].get("selected_machine_label") is not None:
            raise ValueError("A single-turn evaluation cannot invent a user's catalog selection")
        self.calls += 1
        result = await handler(request)
        if not isinstance(result, ToolMessage) or not isinstance(result.content, str):
            raise ValueError("Expected a textual lookup handoff")
        handoff = PartsHandoff.model_validate_json(result.content)
        encoded = encode_handoff_urls(handoff, self.url_handles)
        # Preserve URL roles lost when a long catalog path becomes an opaque handle.
        # This derives only from the public handoff, never from private observations.
        assemblies = [
            handle
            for handle, url in self.url_handles.items()
            if re.search(r"/cn/[^/]+/[^/]+$", urlsplit(url).path)
        ]
        if assemblies:
            encoded.key_facts.append(
                "Assembly source links in this handoff: "
                + ", ".join(assemblies)
                + ". Cite the assembly supporting each reported part in your final answer."
            )
        return result.model_copy(update={"content": encoded.model_dump_json()})


async def run_query(query: str, *, catalog, model, orchestrator_model=None):
    tool = make_lookup_parts_tool(catalog=catalog, model=model)
    boundary = LookupOnly()
    graph = create_deep_agent(
        model=orchestrator_model or model,
        tools=[tool],
        system_prompt=ORCHESTRATOR_PROMPT
        + "\nFor this single question, make at most one lookup. Ask the user if catalogs are ambiguous. "
        "Tool-result URLs are serialized as url_N handles. Copy those handles exactly into citations; "
        "the harness restores the exact observed URLs afterward. Never write or modify a literal URL.",
        middleware=[boundary],
        subagents=[],
    )
    async with asyncio.timeout(65):
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": query}]}, config={"recursion_limit": 24}
        )
    if (diagnostics := current.get()) is not None:
        diagnostics.model_usage.extend(
            message.usage_metadata
            for message in result["messages"]
            if isinstance(message, AIMessage) and message.usage_metadata
        )
    message = result["messages"][-1]
    if not isinstance(message, AIMessage) or message.tool_calls or not message.text:
        raise ValueError("Orchestrator did not return a final textual answer")
    return resolve_handoff_urls(
        PartsHandoff(key_facts=[message.text], sources=[], store_url=""), boundary.url_handles
    ).key_facts[0]
