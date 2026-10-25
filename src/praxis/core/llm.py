from importlib.util import find_spec

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from .config import settings

TEXT_TYPES = ("text", "output_text")
REASONING_TYPES = ("reasoning", "thinking")

# Which setting names the model for each role.
ROLES = {"query": "query_model", "modelgen": "modelgen_model"}


def _azure(name: str) -> dict:
    kwargs: dict = {
        "azure_endpoint": settings.azure_openai_endpoint,
        "api_version": settings.azure_openai_api_version,
        "azure_deployment": settings.azure_openai_deployment or name,
    }
    if settings.azure_openai_api_key:
        kwargs["api_key"] = settings.azure_openai_api_key
    return kwargs | _responses()


def _openai(_name: str) -> dict:
    kwargs: dict = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return kwargs | _responses()


def _responses() -> dict:
    # Reasoning summaries exist only on the Responses API, so OpenAI and Azure both use it.
    kwargs: dict = {"use_responses_api": True, "output_version": "responses/v1"}
    if settings.reasoning_effort:
        kwargs["reasoning"] = {
            "effort": settings.reasoning_effort,
            "summary": settings.reasoning_summary,
        }
    return kwargs


def _compatible(_name: str) -> dict:
    # Chat completions and nothing else: these servers do not implement the Responses API, and
    # there is no common way to ask one for reasoning.
    return {
        "base_url": settings.openai_compatible_base_url,
        "api_key": settings.openai_compatible_api_key,
    }


def _anthropic(_name: str) -> dict:
    kwargs: dict = {"api_key": settings.anthropic_api_key}
    if settings.reasoning_effort:
        # `effort` and not a `budget_tokens`: current Claude models removed the budget. On those,
        # the package turns on adaptive thinking with summarized reasoning by itself.
        kwargs["reasoning_effort"] = settings.reasoning_effort
    return kwargs


def _gemini(_name: str) -> dict:
    kwargs: dict = {"api_key": settings.google_api_key}
    if settings.reasoning_effort:
        # Gemini sets its own thinking budget; this only asks to see the thoughts.
        kwargs["include_thoughts"] = True
    return kwargs


def _ollama(_name: str) -> dict:
    kwargs: dict = {"base_url": settings.ollama_base_url}
    if settings.ollama_reasoning:
        kwargs["reasoning"] = True
    return kwargs


def _litellm(_name: str) -> dict:
    kwargs: dict = {}
    if settings.litellm_api_key:
        kwargs["api_key"] = settings.litellm_api_key
    if settings.litellm_api_base:
        kwargs["api_base"] = settings.litellm_api_base
    if settings.reasoning_effort:
        kwargs["model_kwargs"] = {"reasoning_effort": settings.reasoning_effort}
    return kwargs


# prefix → (`init_chat_model` provider, its keyword arguments, the package it imports, the extra
# that installs it, the setting that must not be empty). `openai_compatible` is ours, not
# LangChain's: it is the `openai` class pointed at another server.
PROVIDERS = {
    "azure_openai": ("azure_openai", _azure, "langchain_openai", None, None),
    "openai": ("openai", _openai, "langchain_openai", None, "openai_api_key"),
    "openai_compatible": (
        "openai",
        _compatible,
        "langchain_openai",
        None,
        "openai_compatible_base_url",
    ),
    "anthropic": ("anthropic", _anthropic, "langchain_anthropic", "anthropic", "anthropic_api_key"),
    "google_genai": ("google_genai", _gemini, "langchain_google_genai", "google", "google_api_key"),
    "ollama": ("ollama", _ollama, "langchain_ollama", "ollama", None),
    "litellm": ("litellm", _litellm, "langchain_litellm", "litellm", None),
}


def model_for(role: str) -> str:
    return getattr(settings, ROLES[role]) or settings.query_model


def build_model(role: str = "query", **overrides) -> BaseChatModel:
    prefix, _, name = model_for(role).partition(":")
    provider, kwargs, *_ = PROVIDERS[prefix]
    return init_chat_model(name, model_provider=provider, **{**kwargs(name), **overrides})


def check() -> list[str]:
    """Every reason a configured model cannot be built, worded for whoever edits `.env`.

    At startup rather than at the first turn, because a missing package or key otherwise
    surfaces as one failed chat after another with nothing pointing at the setting.
    """
    problems: list[str] = []
    for setting in ROLES.values():
        spec = getattr(settings, setting)
        if not spec:
            continue
        prefix = spec.partition(":")[0]
        if prefix not in PROVIDERS or ":" not in spec:
            problems.append(
                f"{setting.upper()}={spec} must start with a provider: "
                + ", ".join(f"{p}:" for p in PROVIDERS)
            )
            continue
        _, _, package, extra, key = PROVIDERS[prefix]
        if find_spec(package) is None:
            problems.append(f"{setting.upper()} uses {prefix}. Install praxis[{extra}].")
        if key and not getattr(settings, key):
            problems.append(f"{setting.upper()} uses {prefix}, and {key.upper()} is empty.")
    return problems


def provider_of(role: str = "query") -> str:
    """The `model_provider` the role's messages are tagged with. Azure and every OpenAI-compatible
    server come from the `openai` package and share its tag."""
    return PROVIDERS[model_for(role).partition(":")[0]][0].removeprefix("azure_")


def answered_by(messages: list[BaseMessage]) -> str | None:
    """The provider that wrote a thread's last answer, or None when nothing says so.

    A history carries one provider's own blocks — Responses API reasoning ids, signed thinking —
    and another provider refuses them, so a thread is only safe to continue on the provider that
    wrote it.
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message.response_metadata.get("model_provider")
    return None


def text_of(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") in TEXT_TYPES
    )


def reasoning_of(message: BaseMessage) -> str:
    content = message.content
    summaries: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") not in REASONING_TYPES:
            continue
        for item in block.get("summary") or []:
            if isinstance(item, dict) and item.get("text"):
                summaries.append(item["text"])
        if block.get("thinking"):
            summaries.append(block["thinking"])
    # Ollama and LiteLLM keep reasoning beside the content rather than in it.
    if not summaries and message.additional_kwargs.get("reasoning_content"):
        summaries.append(message.additional_kwargs["reasoning_content"])
    return "\n\n".join(summaries)
