from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cube_base_path: str = "/cubejs-api"

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

    web_host: str = "127.0.0.1"
    web_port: int = 8420

    approve_scripts: bool = True

    @property
    def dir_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def db_path(self) -> Path:
        return self.dir_path / "analyst.db"


settings = Settings()
