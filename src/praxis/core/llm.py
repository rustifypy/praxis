from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from .config import settings

AZURE_PREFIX = "azure_openai:"
TEXT_TYPES = ("text", "output_text")
REASONING_TYPES = ("reasoning", "thinking")


def build_model(**overrides) -> BaseChatModel:
    kwargs: dict = {}
    if settings.query_model.startswith(AZURE_PREFIX):
        model_name = settings.query_model.removeprefix(AZURE_PREFIX)
        kwargs = {
            "azure_endpoint": settings.azure_openai_endpoint,
            "api_version": settings.azure_openai_api_version,
            "azure_deployment": settings.azure_openai_deployment or model_name,
        }
        if settings.azure_openai_api_key:
            kwargs["api_key"] = settings.azure_openai_api_key
        kwargs |= {
            "use_responses_api": True,
            "output_version": "responses/v1",
            "reasoning": {
                "effort": settings.reasoning_effort,
                "summary": settings.reasoning_summary,
            },
        }
    return init_chat_model(settings.query_model, **{**kwargs, **overrides})


def text_of(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") in TEXT_TYPES
    )


def reasoning_of(content: str | list) -> str:
    if isinstance(content, str):
        return ""
    summaries: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in REASONING_TYPES:
            continue
        for item in block.get("summary") or []:
            if isinstance(item, dict) and item.get("text"):
                summaries.append(item["text"])
        if block.get("thinking"):
            summaries.append(block["thinking"])
    return "\n\n".join(summaries)
