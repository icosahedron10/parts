"""Default OpenAI model profile; callers may inject another compatible chat model."""

from langchain_openai import ChatOpenAI

from ..settings import Settings


def create_parts_model(model: str | None = None):
    return ChatOpenAI(
        model=model or Settings().model,
        use_responses_api=True,
        reasoning={"effort": "none"},
        max_tokens=1536,
        max_retries=0,
        timeout=30,
        store=False,
        include=["reasoning.encrypted_content"],
    )
