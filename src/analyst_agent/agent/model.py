from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ..config import settings

AZURE_PREFIX = "azure_openai:"
# Azure serves the Responses API — and therefore reasoning summaries — only from this
# version on. Verified empirically: on 2024-12-01-preview the same deployment returns
# plain string content with zero reasoning tokens, and rejects use_responses_api with a
# 400. The YYYY-MM-DD-preview format sorts lexicographically, so a string compare works.
RESPONSES_API_MIN_VERSION = "2025-03-01-preview"


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
        if settings.azure_openai_api_key:
            kwargs["api_key"] = settings.azure_openai_api_key
        missing = [k for k in ("azure_endpoint", "api_version") if not kwargs[k]]
        if missing:
            raise ValueError(
                f"{settings.query_model} needs {', '.join(missing)} — set "
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_VERSION in .env"
            )
        if kwargs["api_version"] < RESPONSES_API_MIN_VERSION:
            raise ValueError(
                f"AZURE_OPENAI_API_VERSION={kwargs['api_version']} cannot serve reasoning "
                f"summaries — set it to {RESPONSES_API_MIN_VERSION} or later in .env"
            )
        kwargs |= {
            "use_responses_api": True,
            # Content arrives as typed blocks rather than a string; agent/content.py
            # is what turns it back into text for the CLI and the HTTP API.
            "output_version": "responses/v1",
            "reasoning": {
                "effort": settings.reasoning_effort,
                "summary": settings.reasoning_summary,
            },
        }
    # Overrides win over the derived kwargs — passing one twice would be a TypeError.
    return init_chat_model(settings.query_model, **{**kwargs, **overrides})
