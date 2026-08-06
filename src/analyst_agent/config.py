from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cube_url: str = "http://localhost:4000"
    cube_base_path: str = "/cubejs-api"
    cube_api_secret: str = "dev-secret-change-me"

    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = ""
    # Defaults to the model name in query_model when the deployment shares its name.
    azure_openai_deployment: str = ""
    query_model: str = "azure_openai:gpt-5.1"

    max_rows: int = 5_000
    query_timeout_s: float = 60.0

    grounding_dir: str = "grounding"

    @property
    def api_root(self) -> str:
        return f"{self.cube_url.rstrip('/')}{self.cube_base_path}"


settings = Settings()
