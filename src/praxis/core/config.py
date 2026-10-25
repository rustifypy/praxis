from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = ""
    azure_openai_deployment: str = ""
    query_model: str = "azure_openai:gpt-5.1"
    # The model generation's many structured calls, on a cheaper model if wanted. Empty is
    # `query_model`.
    modelgen_model: str = ""

    # One key and one address per provider. Each is handed to its SDK explicitly, because
    # `pydantic-settings` reads `.env` without putting anything in `os.environ`.
    openai_api_key: str = ""
    openai_base_url: str = ""
    # Any server speaking OpenAI's chat completions — vLLM, LM Studio, llama.cpp. Most ignore the
    # key, but the OpenAI client refuses to start without one.
    openai_compatible_base_url: str = ""
    openai_compatible_api_key: str = "unused"
    anthropic_api_key: str = ""
    google_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    # Ollama answers 400 when asked to think with a model that cannot, so this is opt-in.
    ollama_reasoning: bool = False
    litellm_api_key: str = ""
    litellm_api_base: str = ""

    reasoning_effort: str = "medium"
    reasoning_summary: str = "detailed"

    # What an existing chat does when its history came from another provider. `refuse` ends the
    # turn with a message; `new_thread` answers on a thread of the new provider's own, so the
    # agent no longer sees the turns before the switch.
    on_provider_change: Literal["refuse", "new_thread"] = "refuse"

    max_rows: int = 5_000
    query_timeout_s: float = 60.0
    # A Phase's `extract` writes a file rather than a reply, so the chat's `max_rows` does not
    # apply to it. The page is Cube's own per-request maximum; the ceiling is not a cap but a
    # refusal — past it, `extract` writes nothing rather than a file that looks finished.
    flow_page_rows: int = 50_000
    flow_max_rows: int = 5_000_000

    data_dir: str = "~/.praxis"
    # Relative on purpose, and deliberately not under `data_dir`: a Flow beside the code is a Flow
    # in git, which is what makes the prose deciding an agent's behaviour diffable. The cost is
    # that flows are found relative to where `praxis` was launched — set an absolute path here to
    # pin them. See `docs/adr/0006`.
    flows_dir: str = "flows"

    web_host: str = "127.0.0.1"
    web_port: int = 8420

    approve_scripts: bool = True

    @property
    def dir_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def db_path(self) -> Path:
        return self.dir_path / "praxis.db"

    @property
    def flows_path(self) -> Path:
        # Not `expanduser` alone: a relative `flows_dir` resolves against the launch directory,
        # which is the whole point of it, and `resolve` is what makes that explicit rather than
        # dependent on whoever calls `os.chdir` next.
        return Path(self.flows_dir).expanduser().resolve()


settings = Settings()
