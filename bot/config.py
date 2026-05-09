from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr
    database_url: str = "sqlite+aiosqlite:////data/bot.db"
    log_level: str = "INFO"
    default_timezone: str = "Europe/Vilnius"


# pydantic-settings подтянет поля из env/.env
settings = Settings()  # ty: ignore[missing-argument]
