"""
config.py — единая точка загрузки настроек.

Значения читаются из переменных окружения и файла `.env` (через pydantic-settings,
который под капотом использует python-dotenv). Если обязательная переменная не задана,
приложение упадёт на старте с понятной ошибкой — это лучше, чем падение посреди работы.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Обязательные секреты ---
    bot_token: str
    openrouter_api_key: str
    cryptopay_token: str
    admin_id: int

    # --- AI (OpenRouter) ---
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_max_tokens: int = 1500
    ai_temperature: float = 0.7
    ai_timeout: float = 60.0
    # Сколько последних сообщений (user + assistant суммарно) отправляем модели
    context_messages: int = 8
    system_prompt: str = (
        "Ты — Atlas AI, дружелюбный и точный ИИ-ассистент в Telegram. "
        "Отвечай на языке пользователя, кратко и по делу. "
        "Для кода используй блоки ```, для выделения — **жирный**."
    )

    # --- Crypto Pay ---
    # Для тестов: https://testnet-pay.crypt.bot/api
    cryptopay_base_url: str = "https://pay.crypt.bot/api"
    invoice_ttl_seconds: int = 3600

    # --- БД и лимиты ---
    db_path: str = "data/atlas.db"
    free_daily_limit: int = 10
    support_max_open_tickets: int = 3

    # --- Тарифы (USD) ---
    price_month: Decimal = Decimal("5")
    price_lifetime: Decimal = Decimal("25")
    month_days: int = 30

    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]


@dataclass(frozen=True, slots=True)
class Plan:
    key: str
    title: str
    price: Decimal
    days: int | None  # None = навсегда


PLANS: dict[str, Plan] = {
    "month": Plan("month", "1 месяц", settings.price_month, settings.month_days),
    "lifetime": Plan("lifetime", "Навсегда", settings.price_lifetime, None),
}
