from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cube_url: str = "http://localhost:4000"
    cube_base_path: str = "/cubejs-api"
    # Must equal CUBEJS_API_SECRET and be at least 32 bytes — mint_token enforces it.
    cube_api_secret: str = ""
    # Serves the playground, watches the model mount, and SKIPS JWT verification. On because
    # the local design assumes all three; `docker.argv` still defaults it off, so it is never
    # on by accident.
    cubejs_dev_mode: bool = True

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = ""
    # Defaults to the model name in query_model when the deployment shares its name.
    azure_openai_deployment: str = ""
    query_model: str = "azure_openai:gpt-5.1"

    # Depth of the reasoning summary the transcript shows. Costs latency on every
    # planning call, so it is a knob rather than a constant.
    reasoning_effort: str = "medium"
    # "auto" lets the model decide whether a summary is worth emitting, and on short
    # planning calls it often decides no — leaving the thinking panel empty.
    reasoning_summary: str = "detailed"

    max_rows: int = 5_000
    query_timeout_s: float = 60.0

    data_dir: str = "~/.analyst"

    web_host: str = "127.0.0.1"
    web_port: int = 8420

    approve_scripts: bool = True
    """Show a script and wait for a click before running it. Not a safety gate — the sandbox is
    that — but the only moment you can read what the agent wrote before it runs."""

    @property
    def api_root(self) -> str:
        return f"{self.cube_url.rstrip('/')}{self.cube_base_path}"

    @property
    def dir_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def db_path(self) -> Path:
        """Callers create the parent — a property should not touch the filesystem."""
        return self.dir_path / "analyst.db"


settings = Settings()
