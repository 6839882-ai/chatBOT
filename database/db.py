"""
database/db.py — инициализация SQLite и «пул» соединений.

SQLite — файловая БД с одним писателем, поэтому настоящий пул соединений не даёт выигрыша.
Используем ОДНО долгоживущее aiosqlite-соединение + asyncio.Lock, который сериализует
транзакции (атомарность «прочитал -> проверил -> записал» гарантируется блокировкой).
Включаем WAL-режим для устойчивости и скорости.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

import aiosqlite

logger = logging.getLogger(__name__)

# Даты хранятся как ISO-8601 строки в UTC (например 2026-10-06T12:00:00+00:00).
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id        INTEGER PRIMARY KEY,           -- BIGINT (INTEGER в SQLite 64-битный)
    username           TEXT,
    registered_at      TEXT    NOT NULL,
    subscription_until TEXT,                          -- NULL = подписки нет
    messages_limit     INTEGER NOT NULL DEFAULT 10,   -- дневной лимит бесплатных запросов
    messages_used      INTEGER NOT NULL DEFAULT 0,    -- сколько израсходовано в usage_date
    usage_date         TEXT                           -- дата (UTC) последнего списания
);

CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    message_text TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open', 'answered', 'closed')),
    created_at   TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (telegram_id)
);
CREATE INDEX IF NOT EXISTS idx_tickets_user_status ON tickets (user_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status);

-- Служебная таблица: связывает инвойс CryptoBot с пользователем и тарифом.
-- Нужна, чтобы (а) нельзя было «проверить» чужой счёт и (б) оплата не засчитывалась дважды.
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id INTEGER PRIMARY KEY,                   -- id инвойса в Crypto Pay
    user_id    INTEGER NOT NULL,
    plan       TEXT    NOT NULL,
    amount     TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'active',     -- active | paid | expired
    created_at TEXT    NOT NULL,
    paid_at    TEXT,
    FOREIGN KEY (user_id) REFERENCES users (telegram_id)
);
CREATE INDEX IF NOT EXISTS idx_invoices_user ON invoices (user_id);
"""


class Database:
    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("База данных готова: %s", path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Соединение с БД закрыто")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("БД не инициализирована: вызовите init_db()")
        return self._conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Эксклюзивная транзакция: commit при успехе, rollback при исключении."""
        async with self._lock:
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            try:
                return await cur.fetchone()
            finally:
                await cur.close()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            try:
                return list(await cur.fetchall())
            finally:
                await cur.close()


# Глобальный экземпляр, которым пользуется crud.py
db = Database()


async def init_db(path: str) -> None:
    await db.connect(path)


async def close_db() -> None:
    await db.close()
