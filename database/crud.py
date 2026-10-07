"""
database/crud.py — функции чтения/записи (users, tickets, invoices).

Все операции, меняющие данные, выполняются в транзакции `db.transaction()`.
Время — всегда UTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings
from database.db import db

# «Навсегда» = дата в далёком будущем (так проверка `until > now` работает единообразно)
LIFETIME_UNTIL = datetime(2999, 12, 31, tzinfo=timezone.utc)

# Результат попытки списать запрос
CONSUME_PREMIUM = "premium"  # подписка активна, лимиты не тратятся
CONSUME_FREE = "free"        # списан один бесплатный запрос
CONSUME_DENIED = "denied"    # лимит исчерпан


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _today() -> str:
    return _now().date().isoformat()


# --------------------------------------------------------------------------- модели
@dataclass(slots=True)
class User:
    telegram_id: int
    username: str | None
    registered_at: datetime
    subscription_until: datetime | None
    messages_limit: int
    messages_used: int
    usage_date: str | None

    @property
    def is_premium(self) -> bool:
        return self.subscription_until is not None and self.subscription_until > _now()

    @property
    def is_lifetime(self) -> bool:
        return self.subscription_until is not None and self.subscription_until >= LIFETIME_UNTIL

    @property
    def free_left(self) -> int:
        used = self.messages_used if self.usage_date == _today() else 0
        return max(self.messages_limit - used, 0)


@dataclass(slots=True)
class Ticket:
    id: int
    user_id: int
    message_text: str
    status: str
    created_at: datetime


@dataclass(slots=True)
class InvoiceRecord:
    invoice_id: int
    user_id: int
    plan: str
    amount: str
    status: str


def _user(row: Any) -> User:
    return User(
        telegram_id=row["telegram_id"],
        username=row["username"],
        registered_at=_parse(row["registered_at"]),  # type: ignore[arg-type]
        subscription_until=_parse(row["subscription_until"]),
        messages_limit=row["messages_limit"],
        messages_used=row["messages_used"],
        usage_date=row["usage_date"],
    )


def _ticket(row: Any) -> Ticket:
    return Ticket(
        id=row["id"],
        user_id=row["user_id"],
        message_text=row["message_text"],
        status=row["status"],
        created_at=_parse(row["created_at"]),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- users
async def get_or_create_user(telegram_id: int, username: str | None) -> User:
    """Создаёт пользователя при первом обращении, иначе обновляет username."""
    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, username, registered_at, messages_limit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username
            """,
            (telegram_id, username, _iso(_now()), settings.free_daily_limit),
        )
        cur = await conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cur.fetchone()
        await cur.close()
    return _user(row)


async def get_user(telegram_id: int) -> User | None:
    row = await db.fetchone("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    return _user(row) if row else None


async def try_consume_message(telegram_id: int) -> str:
    """
    Атомарно проверяет лимит и списывает один бесплатный запрос.
    Дневной счётчик сбрасывается автоматически при смене даты (UTC).
    """
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return CONSUME_DENIED
        user = _user(row)
        if user.is_premium:
            return CONSUME_PREMIUM
        today = _today()
        used = user.messages_used if user.usage_date == today else 0
        if used >= user.messages_limit:
            return CONSUME_DENIED
        await conn.execute(
            "UPDATE users SET messages_used = ?, usage_date = ? WHERE telegram_id = ?",
            (used + 1, today, telegram_id),
        )
        return CONSUME_FREE


async def refund_message(telegram_id: int) -> None:
    """Вернуть списанный запрос (если AI не смог ответить)."""
    async with db.transaction() as conn:
        await conn.execute(
            """UPDATE users SET messages_used = MAX(messages_used - 1, 0)
               WHERE telegram_id = ? AND usage_date = ?""",
            (telegram_id, _today()),
        )


async def _extend_subscription(conn: Any, telegram_id: int, days: int | None) -> datetime:
    cur = await conn.execute(
        "SELECT subscription_until FROM users WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    current = _parse(row["subscription_until"]) if row else None

    if days is None:
        until = LIFETIME_UNTIL
    elif current is not None and current >= LIFETIME_UNTIL:
        until = current  # уже «навсегда» — не уменьшаем
    else:
        base = max(_now(), current) if current else _now()
        until = base + timedelta(days=days)

    await conn.execute(
        "UPDATE users SET subscription_until = ? WHERE telegram_id = ?",
        (_iso(until), telegram_id),
    )
    return until


async def extend_subscription(telegram_id: int, days: int | None) -> datetime:
    """Продлить подписку на N дней (None = навсегда). Возвращает новую дату окончания."""
    async with db.transaction() as conn:
        return await _extend_subscription(conn, telegram_id, days)


# --------------------------------------------------------------------------- invoices
async def create_invoice_record(invoice_id: int, user_id: int, plan: str, amount: str) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            """INSERT OR IGNORE INTO invoices (invoice_id, user_id, plan, amount, status, created_at)
               VALUES (?, ?, ?, ?, 'active', ?)""",
            (invoice_id, user_id, plan, amount, _iso(_now())),
        )


async def get_invoice(invoice_id: int) -> InvoiceRecord | None:
    row = await db.fetchone("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
    if row is None:
        return None
    return InvoiceRecord(
        invoice_id=row["invoice_id"],
        user_id=row["user_id"],
        plan=row["plan"],
        amount=row["amount"],
        status=row["status"],
    )


async def settle_invoice(invoice_id: int, days: int | None) -> datetime | None:
    """
    Помечает инвойс оплаченным И продлевает подписку в ОДНОЙ транзакции.
    Возвращает новую дату окончания, либо None, если инвойс уже был засчитан ранее
    (защита от двойного начисления при повторных нажатиях «Проверить оплату»).
    """
    async with db.transaction() as conn:
        cur = await conn.execute(
            """UPDATE invoices SET status = 'paid', paid_at = ?
               WHERE invoice_id = ? AND status != 'paid'""",
            (_iso(_now()), invoice_id),
        )
        changed = cur.rowcount
        await cur.close()
        if changed != 1:
            return None
        cur = await conn.execute("SELECT user_id FROM invoices WHERE invoice_id = ?", (invoice_id,))
        row = await cur.fetchone()
        await cur.close()
        return await _extend_subscription(conn, row["user_id"], days)


async def expire_invoice(invoice_id: int) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE invoices SET status = 'expired' WHERE invoice_id = ? AND status = 'active'",
            (invoice_id,),
        )


# --------------------------------------------------------------------------- tickets
async def create_ticket(user_id: int, text: str) -> int:
    async with db.transaction() as conn:
        cur = await conn.execute(
            "INSERT INTO tickets (user_id, message_text, status, created_at) VALUES (?, ?, 'open', ?)",
            (user_id, text, _iso(_now())),
        )
        ticket_id = cur.lastrowid
        await cur.close()
    return int(ticket_id)


async def get_ticket(ticket_id: int) -> Ticket | None:
    row = await db.fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    return _ticket(row) if row else None


async def set_ticket_status(ticket_id: int, status: str) -> None:
    async with db.transaction() as conn:
        await conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))


async def count_open_tickets(user_id: int) -> int:
    row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM tickets WHERE user_id = ? AND status = 'open'", (user_id,)
    )
    return int(row["c"]) if row else 0


async def list_open_tickets(limit: int = 10) -> list[Ticket]:
    rows = await db.fetchall(
        "SELECT * FROM tickets WHERE status = 'open' ORDER BY id ASC LIMIT ?", (limit,)
    )
    return [_ticket(r) for r in rows]
