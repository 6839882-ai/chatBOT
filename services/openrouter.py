"""
services/openrouter.py — асинхронный клиент OpenRouter (через AsyncOpenAI) + память диалога.

История хранится в оперативной памяти (по user_id) и обрезается до последних N сообщений
(по умолчанию 8) — это экономит токены. После перезапуска бота контекст сбрасывается.
Если нужен постоянный контекст — вынесите _history в БД/Redis (интерфейс не изменится).
"""
from __future__ import annotations

import logging

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Ошибка AI-сервиса с готовым текстом для пользователя."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        system_prompt: str,
        max_tokens: int = 1500,
        temperature: float = 0.7,
        timeout: float = 60.0,
        context_messages: int = 8,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=2,  # SDK сам повторит запрос при 429/5xx/сетевых сбоях
            default_headers={  # необязательные заголовки атрибуции OpenRouter
                "HTTP-Referer": "https://t.me/AtlasMateBot",
                "X-Title": "Atlas AI",
            },
        )
        self._model = model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Храним пары (user, assistant), поэтому лимит делаем чётным: 6–8 сообщений по ТЗ
        self._max_history = max(2, context_messages - context_messages % 2)
        self._history: dict[int, list[dict[str, str]]] = {}

    def reset(self, user_id: int) -> None:
        """Очистить историю переписки пользователя."""
        self._history.pop(user_id, None)

    async def ask(self, user_id: int, text: str) -> str:
        history = self._history.get(user_id, [])
        messages = [
            {"role": "system", "content": self._system_prompt},
            *history,
            {"role": "user", "content": text},
        ]
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
        except RateLimitError as e:
            logger.warning("OpenRouter rate limit: %s", e)
            raise AIServiceError("⏳ Сервис сейчас перегружен. Попробуйте через минуту.") from e
        except APITimeoutError as e:
            logger.warning("OpenRouter timeout")
            raise AIServiceError("⌛ Нейросеть отвечает слишком долго. Попробуйте ещё раз.") from e
        except APIConnectionError as e:
            logger.warning("OpenRouter connection error: %s", e)
            raise AIServiceError("📡 Нет связи с AI-сервисом. Попробуйте позже.") from e
        except APIStatusError as e:
            logger.error("OpenRouter HTTP %s: %s", e.status_code, e.message)
            if e.status_code in (401, 402, 403):
                raise AIServiceError("⚙️ AI-сервис временно недоступен (проблема на нашей стороне).") from e
            raise AIServiceError("⚠️ AI-сервис вернул ошибку. Попробуйте позже.") from e
        except OpenAIError as e:
            logger.exception("OpenRouter unexpected error")
            raise AIServiceError("⚠️ Не удалось получить ответ. Попробуйте позже.") from e

        answer = ""
        if resp.choices and resp.choices[0].message:
            answer = (resp.choices[0].message.content or "").strip()
        if not answer:
            # OpenRouter иногда отдаёт 200 с пустым choices/ошибкой провайдера
            logger.error("Пустой ответ модели: %s", resp)
            raise AIServiceError("⚠️ Модель вернула пустой ответ. Попробуйте переформулировать запрос.")

        # Контекст обновляем только после УСПЕШНОГО ответа — история не «ломается» при ошибках
        history = [*history, {"role": "user", "content": text}, {"role": "assistant", "content": answer}]
        self._history[user_id] = history[-self._max_history :]
        return answer

    async def close(self) -> None:
        await self._client.close()
