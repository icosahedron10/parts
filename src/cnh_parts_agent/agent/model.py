"""Default OpenAI model profile; callers may inject another compatible chat model."""

from langchain_openai import ChatOpenAI

from ..settings import Settings


def create_parts_model(model: str | None = None):
    settings = Settings()
    return ChatOpenAI(
        model=model or settings.model,
        use_responses_api=True,
        reasoning={"effort": "none"},
        max_tokens=settings.model_max_output_tokens,
        max_retries=0,
        timeout=settings.model_timeout_seconds,
        store=False,
        include=["reasoning.encrypted_content"],
    )
