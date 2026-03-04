from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ENV_FILE = _BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    mock_mode: bool = Field(default=True, alias="MOCK_MODE")
    cors_origins: str = Field(default="http://localhost:3000,http://127.0.0.1:3000", alias="CORS_ORIGINS")
    upload_dir: Path = Field(default=Path("./uploads"), alias="UPLOAD_DIR")
    default_max_retries: int = Field(default=3, alias="DEFAULT_MAX_RETRIES")

    vision_provider: str = Field(default="claude", alias="VISION_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    gpt4o_model: str = Field(default="gpt-4o", alias="GPT4O_MODEL")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-6", alias="CLAUDE_MODEL")

    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    http_proxy: str = Field(default="", alias="HTTP_PROXY")
    https_proxy: str = Field(default="", alias="HTTPS_PROXY")
    all_proxy: str = Field(default="", alias="ALL_PROXY")

    browser_mode: str = Field(default="real", alias="BROWSER_MODE")
    browser_use_enabled: bool = Field(default=True, alias="BROWSER_USE_ENABLED")
    browser_headless: bool = Field(default=False, alias="BROWSER_HEADLESS")
    browser_keep_alive: bool = Field(default=True, alias="BROWSER_KEEP_ALIVE")
    browser_operation_timeout_sec: int = Field(default=240, alias="BROWSER_OPERATION_TIMEOUT_SEC")

    browser_executable_path: str = Field(default="", alias="BROWSER_EXECUTABLE_PATH")
    browser_user_data_dir: str = Field(default="", alias="BROWSER_USER_DATA_DIR")
    browser_profile_directory: str = Field(default="", alias="BROWSER_PROFILE_DIRECTORY")

    browser_cloud_project_id: str = Field(default="", alias="BROWSER_CLOUD_PROJECT_ID")
    browser_cloud_live_url: str = Field(default="", alias="BROWSER_CLOUD_LIVE_URL")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_cloud_mode(self) -> bool:
        return self.browser_mode.lower() == "cloud"

    @property
    def is_real_mode(self) -> bool:
        return self.browser_mode.lower() == "real"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.http_proxy:
        os.environ["HTTP_PROXY"] = settings.http_proxy
    if settings.https_proxy:
        os.environ["HTTPS_PROXY"] = settings.https_proxy
    if settings.all_proxy:
        os.environ["ALL_PROXY"] = settings.all_proxy
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings
