"""
services/cryptopay.py — обёртка над Crypto Pay API (@CryptoBot).
Документация: https://help.crypt.bot/crypto-pay-api
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class CryptoPayError(Exception):
    """Ошибка Crypto Pay API или сети."""


@dataclass(frozen=True, slots=True)
class CryptoInvoice:
    invoice_id: int
    status: str  # active | paid | expired
    pay_url: str
    amount: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "CryptoInvoice":
        return cls(
            invoice_id=int(raw["invoice_id"]),
            status=str(raw.get("status", "")),
            # bot_invoice_url — актуальное поле, pay_url — устаревшее (для совместимости)
            pay_url=str(raw.get("bot_invoice_url") or raw.get("pay_url") or ""),
            amount=str(raw.get("amount", "")),
        )


class CryptoPayClient:
    def __init__(self, token: str, base_url: str = "https://pay.crypt.bot/api", timeout: float = 15.0) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _call(self, method: str, params: dict[str, Any], *, post: bool, retries: int = 0) -> Any:
        url = f"{self._base_url}/{method}"
        headers = {"Crypto-Pay-API-Token": self._token}
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                session = self._get_session()
                if post:
                    ctx = session.post(url, json=params, headers=headers)
                else:
                    ctx = session.get(url, params=params, headers=headers)
                async with ctx as resp:
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning("CryptoPay %s: сетевая ошибка (попытка %d): %s", method, attempt + 1, e)
                if attempt < retries:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue
            except ValueError as e:  # невалидный JSON
                raise CryptoPayError(f"{method}: некорректный ответ API") from e

            if not isinstance(data, dict) or not data.get("ok"):
                err = data.get("error") if isinstance(data, dict) else data
                logger.error("CryptoPay %s error: %s", method, err)
                raise CryptoPayError(f"{method}: {err}")
            return data["result"]

        raise CryptoPayError(f"{method}: сеть недоступна") from last_error

    async def create_invoice(
        self, amount: str, description: str, payload: str, expires_in: int = 3600
    ) -> CryptoInvoice:
        """createInvoice: счёт в USD-эквиваленте, пользователь выбирает монету при оплате."""
        result = await self._call(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "USD",
                "amount": amount,
                "description": description[:1024],
                "payload": payload[:4096],
                "expires_in": expires_in,
                "allow_comments": False,
                "allow_anonymous": False,
            },
            post=True,  # POST без ретраев — чтобы не плодить дубликаты счетов
        )
        invoice = CryptoInvoice.from_api(result)
        if not invoice.pay_url:
            raise CryptoPayError("createInvoice: API не вернул ссылку на оплату")
        return invoice

    async def get_invoice(self, invoice_id: int) -> CryptoInvoice | None:
        """getInvoices по одному id. GET идемпотентен — можно безопасно ретраить."""
        result = await self._call("getInvoices", {"invoice_ids": str(invoice_id)}, post=False, retries=2)
        items = result.get("items", []) if isinstance(result, dict) else result  # разные версии API
        for raw in items or []:
            if int(raw.get("invoice_id", -1)) == invoice_id:
                return CryptoInvoice.from_api(raw)
        return None
