from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = ""
    azure_openai_deployment: str = ""
    query_model: str = "azure_openai:gpt-5.1"

    reasoning_effort: str = "medium"
    reasoning_summary: str = "detailed"

    max_rows: int = 5_000
    query_timeout_s: float = 60.0

    data_dir: str = "~/.analyst"
    # Relative on purpose, and deliberately not under `data_dir`: a Flow beside the code is a Flow
    # in git, which is what makes the prose deciding an agent's behaviour diffable. The cost is
    # that flows are found relative to where `analyst` was launched — set an absolute path here to
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
        return self.dir_path / "analyst.db"

    @property
    def flows_path(self) -> Path:
        # Not `expanduser` alone: a relative `flows_dir` resolves against the launch directory,
        # which is the whole point of it, and `resolve` is what makes that explicit rather than
        # dependent on whoever calls `os.chdir` next.
        return Path(self.flows_dir).expanduser().resolve()


settings = Settings()
