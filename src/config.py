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
    # Прокси для исходящих запросов к Telegram. Поддерживается http(s)://...
    # Для socks нужно `pip install aiohttp-socks` и URL вида socks5://host:port.
    bot_proxy_url: str | None = None


# pydantic-settings подтянет поля из env/.env
settings = Settings()  # ty: ignore[missing-argument]
