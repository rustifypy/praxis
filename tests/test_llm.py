"""Which keyword arguments each provider is built with — and that its class accepts them.

Building a chat model makes no request, so every provider is constructed for real here: a
misspelt field fails in this file rather than at the first turn against a live source.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from praxis.core import llm
from praxis.core.config import settings


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for name, value in {
        "azure_openai_endpoint": "https://example.openai.azure.com",
        "azure_openai_api_version": "2025-04-01-preview",
        "azure_openai_api_key": "azure-key",
        "azure_openai_deployment": "",
        "openai_api_key": "openai-key",
        "openai_compatible_base_url": "http://localhost:8000/v1",
        "anthropic_api_key": "anthropic-key",
        "google_api_key": "google-key",
        "litellm_api_key": "",
        "litellm_api_base": "",
        "ollama_reasoning": False,
        "modelgen_model": "",
        "reasoning_effort": "medium",
        "reasoning_summary": "detailed",
    }.items():
        monkeypatch.setattr(settings, name, value)


def use(monkeypatch, spec: str, setting: str = "query_model"):
    monkeypatch.setattr(settings, setting, spec)


def test_an_existing_azure_env_builds_what_it_always_did(monkeypatch):
    """The keyword arguments `build_model` passed before there was more than one provider."""
    assert llm.PROVIDERS["azure_openai"][1]("gpt-5.1") == {
        "azure_endpoint": "https://example.openai.azure.com",
        "api_version": "2025-04-01-preview",
        "azure_deployment": "gpt-5.1",
        "api_key": "azure-key",
        "use_responses_api": True,
        "output_version": "responses/v1",
        "reasoning": {"effort": "medium", "summary": "detailed"},
    }
    use(monkeypatch, "azure_openai:gpt-5.1")
    model = llm.build_model()
    assert type(model).__name__ == "AzureChatOpenAI"
    assert model.deployment_name == "gpt-5.1"


def test_openai_uses_the_responses_api_for_its_reasoning_summaries(monkeypatch):
    use(monkeypatch, "openai:gpt-5.1")
    model = llm.build_model()
    assert type(model).__name__ == "ChatOpenAI"
    assert model.use_responses_api
    assert model.reasoning == {"effort": "medium", "summary": "detailed"}


def test_an_openai_compatible_server_is_the_openai_class_at_another_address(monkeypatch):
    use(monkeypatch, "openai_compatible:qwen3-32b")
    model = llm.build_model()
    assert type(model).__name__ == "ChatOpenAI"
    assert model.openai_api_base == "http://localhost:8000/v1"
    assert model.model_name == "qwen3-32b"
    # Chat completions only: these servers have no Responses API to ask for reasoning on.
    assert not model.use_responses_api and model.reasoning is None


def test_anthropic_is_asked_for_effort_not_a_token_budget(monkeypatch):
    """Current Claude models removed `budget_tokens`, so no budget can outgrow `max_tokens`."""
    use(monkeypatch, "anthropic:claude-opus-5-5")
    model = llm.build_model()
    assert type(model).__name__ == "ChatAnthropic"
    assert model.reasoning_effort == "medium"
    assert model.thinking is None
    assert model.anthropic_api_key.get_secret_value() == "anthropic-key"


def test_gemini_is_asked_to_show_its_thoughts(monkeypatch):
    use(monkeypatch, "google_genai:gemini-2.5-pro")
    model = llm.build_model()
    assert type(model).__name__ == "ChatGoogleGenerativeAI"
    assert model.include_thoughts is True
    assert model.google_api_key.get_secret_value() == "google-key"


def test_ollama_thinks_only_when_told_the_model_can(monkeypatch):
    use(monkeypatch, "ollama:qwen3:8b")
    model = llm.build_model()
    assert type(model).__name__ == "ChatOllama"
    # The model name keeps its own colon: only the first one separates the provider.
    assert model.model == "qwen3:8b"
    assert model.reasoning is None

    monkeypatch.setattr(settings, "ollama_reasoning", True)
    assert llm.build_model().reasoning is True


def test_litellm_carries_effort_in_its_request(monkeypatch):
    use(monkeypatch, "litellm:anthropic/claude-sonnet-5")
    monkeypatch.setattr(settings, "litellm_api_key", "litellm-key")
    model = llm.build_model()
    assert type(model).__name__ == "ChatLiteLLM"
    assert model.model == "anthropic/claude-sonnet-5"
    assert model.api_key == "litellm-key"
    assert model.model_kwargs == {"reasoning_effort": "medium"}


@pytest.mark.parametrize("prefix", sorted(llm.PROVIDERS))
def test_an_empty_effort_asks_no_provider_for_reasoning(monkeypatch, prefix):
    """A model that cannot reason refuses the parameter, so emptying the setting must remove it."""
    monkeypatch.setattr(settings, "reasoning_effort", "")
    kwargs = llm.PROVIDERS[prefix][1]("some-model")
    assert not {"reasoning", "reasoning_effort", "include_thoughts", "model_kwargs"} & set(kwargs)


def test_model_generation_uses_the_query_model_unless_given_its_own(monkeypatch):
    use(monkeypatch, "azure_openai:gpt-5.1")
    assert llm.model_for("modelgen") == "azure_openai:gpt-5.1"

    use(monkeypatch, "openai:gpt-5-mini", "modelgen_model")
    assert llm.model_for("modelgen") == "openai:gpt-5-mini"
    assert llm.model_for("query") == "azure_openai:gpt-5.1"
    assert type(llm.build_model("modelgen")).__name__ == "ChatOpenAI"


def test_a_complete_configuration_has_no_problems(monkeypatch):
    use(monkeypatch, "anthropic:claude-opus-5-5")
    assert llm.check() == []


def test_a_model_without_a_provider_is_refused(monkeypatch):
    use(monkeypatch, "gpt-5.1")
    assert llm.check() == [
        "QUERY_MODEL=gpt-5.1 must start with a provider: "
        "azure_openai:, openai:, openai_compatible:, anthropic:, google_genai:, ollama:, litellm:"
    ]


def test_a_missing_key_names_the_setting(monkeypatch):
    use(monkeypatch, "anthropic:claude-opus-5-5", "modelgen_model")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert llm.check() == ["MODELGEN_MODEL uses anthropic, and ANTHROPIC_API_KEY is empty."]


def test_a_missing_package_names_the_extra(monkeypatch):
    use(monkeypatch, "google_genai:gemini-2.5-pro")
    monkeypatch.setattr(llm, "find_spec", lambda name: None)
    assert llm.check() == ["QUERY_MODEL uses google_genai. Install praxis[google]."]


@pytest.mark.parametrize(
    ("spec", "tag"),
    [
        ("azure_openai:gpt-5.1", "openai"),
        ("openai:gpt-5.1", "openai"),
        ("openai_compatible:qwen3-32b", "openai"),
        ("anthropic:claude-opus-5-5", "anthropic"),
        ("google_genai:gemini-2.5-pro", "google_genai"),
        ("ollama:qwen3:8b", "ollama"),
        ("litellm:anthropic/claude-sonnet-5", "litellm"),
    ],
)
def test_the_provider_matches_the_tag_its_package_writes(monkeypatch, spec, tag):
    """The guard compares this against `response_metadata["model_provider"]` on a stored answer,
    so it must be the string each package writes there, not the prefix."""
    use(monkeypatch, spec)
    assert llm.provider_of() == tag


def test_a_thread_is_answered_by_its_last_answer():
    history = [
        HumanMessage("first"),
        AIMessage("a", response_metadata={"model_provider": "openai"}),
        HumanMessage("second"),
        AIMessage("b", response_metadata={"model_provider": "anthropic"}),
        HumanMessage("third"),
    ]
    assert llm.answered_by(history) == "anthropic"
    # Nothing to say whose it is: an empty thread, or an answer written before tags existed.
    assert llm.answered_by([HumanMessage("first")]) is None
    assert llm.answered_by([AIMessage("old")]) is None
