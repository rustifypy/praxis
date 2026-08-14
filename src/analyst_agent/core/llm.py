"""The chat model, and how to read what it returns.

One module because the two halves are the same seam: enabling the Responses API in
`build_model` is exactly what turns `.content` from a string into a list of blocks, so a
caller that has one always needs the other.
"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from .config import settings

AZURE_PREFIX = "azure_openai:"


def build_model(**overrides) -> BaseChatModel:
    """Resolve settings.query_model into a chat model. Azure routes by deployment, so
    endpoint/version/deployment are explicit; other prefixes fall through to init_chat_model.
    """
    kwargs: dict = {}
    if settings.query_model.startswith(AZURE_PREFIX):
        model_name = settings.query_model.removeprefix(AZURE_PREFIX)
        kwargs = {
            "azure_endpoint": settings.azure_openai_endpoint,
            "api_version": settings.azure_openai_api_version,
            # Deployments are usually named after the model they serve.
            "azure_deployment": settings.azure_openai_deployment or model_name,
        }
        # Passed explicitly: pydantic-settings reads `.env` into `Settings` and does not
        # put anything in `os.environ`, so the OpenAI SDK's own AZURE_OPENAI_API_KEY
        # lookup finds nothing and the call fails with "Missing credentials".
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
    # Overrides win over the derived kwargs — passing one twice would be a TypeError.
    return init_chat_model(settings.query_model, **{**kwargs, **overrides})


# Message content is a list of typed blocks, not a string — the Responses API returns
# reasoning, tool calls and text separately. Reading `.content` directly elsewhere is a bug.

# Chat Completions and the Responses API label answer text differently.
TEXT_TYPES = ("text", "output_text")
REASONING_TYPES = ("reasoning", "thinking")


def text_of(content: str | list) -> str:
    """The visible answer. Reasoning and tool-call blocks are dropped."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") in TEXT_TYPES
    )


def reasoning_of(content: str | list) -> str:
    """The reasoning summary, or "" when the turn produced none. Empty is normal."""
    if isinstance(content, str):
        return ""
    out: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in REASONING_TYPES:
            continue

        for item in block.get("summary") or []:
            if isinstance(item, dict) and item.get("text"):
                out.append(item["text"])
        if block.get("thinking"):
            out.append(block["thinking"])
    return "\n\n".join(out)
