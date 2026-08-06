from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ..config import settings

AZURE_PREFIX = "azure_openai:"


def build_model(**overrides) -> BaseChatModel:
    """Resolve settings.query_model into a chat model.

    Azure routes by deployment, not model name, so the endpoint/version/deployment
    have to be passed explicitly — a provider-prefixed string alone can't carry them.
    Any other provider prefix falls through to init_chat_model untouched.
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
    return init_chat_model(settings.query_model, **kwargs, **overrides)
