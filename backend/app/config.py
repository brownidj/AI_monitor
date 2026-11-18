from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    api_token: SecretStr = Field(default=SecretStr(""), validation_alias="MONITOR_API_TOKEN")
    openai_admin_key: SecretStr = Field(default=SecretStr(""), validation_alias="OPENAI_ADMIN_KEY")
    database_path: Path = Field(
        default=BACKEND_ROOT / "data" / "monitor.sqlite3",
        validation_alias="MONITOR_DATABASE_PATH",
    )
    timezone: str = Field(default="Australia/Brisbane", validation_alias="MONITOR_TIMEZONE")
    scheduler_enabled: bool = Field(default=True, validation_alias="MONITOR_SCHEDULER_ENABLED")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value
