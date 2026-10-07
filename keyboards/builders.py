"""keyboards/builders.py — главное меню (Reply) и все инлайн-клавиатуры."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from config import PLANS

BTN_CHAT = "🤖 Чат с Atlas"
BTN_SUB = "💳 Подписка / Премиум"
BTN_PROFILE = "👤 Мой профиль"
BTN_SUPPORT = "🎫 Поддержка"
BTN_RESET = "🔄 Сбросить контекст"

MENU_BUTTONS = {BTN_CHAT, BTN_SUB, BTN_PROFILE, BTN_SUPPORT, BTN_RESET}


def _ikb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CHAT)],
            [KeyboardButton(text=BTN_SUB), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_RESET)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие или напишите сообщение",
    )


# ------------------------------------------------------------------ подписка
def plans_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{p.title} — ${p.price:f}", callback_data=f"plan:{p.key}")]
        for p in PLANS.values()
    ]
    return _ikb(rows)


def plan_details_kb(plan_key: str) -> InlineKeyboardMarkup:
    price = PLANS[plan_key].price
    return _ikb(
        [
            [InlineKeyboardButton(text=f"💳 Оплатить ${price:f}", callback_data=f"pay:{plan_key}")],
            [InlineKeyboardButton(text="⬅️ К тарифам", callback_data="sub:plans")],
        ]
    )


def invoice_kb(pay_url: str, invoice_id: int) -> InlineKeyboardMarkup:
    return _ikb(
        [
            [InlineKeyboardButton(text="💳 Оплатить в CryptoBot", url=pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check:{invoice_id}")],
            [InlineKeyboardButton(text="⬅️ К тарифам", callback_data="sub:plans")],
        ]
    )


def back_to_plans_kb() -> InlineKeyboardMarkup:
    return _ikb([[InlineKeyboardButton(text="⬅️ К тарифам", callback_data="sub:plans")]])


def buy_access_kb() -> InlineKeyboardMarkup:
    """Показывается, когда бесплатный лимит исчерпан."""
    return _ikb([[InlineKeyboardButton(text="💎 Купить доступ", callback_data="sub:plans")]])


# ------------------------------------------------------------------ поддержка
def support_start_kb() -> InlineKeyboardMarkup:
    return _ikb([[InlineKeyboardButton(text="✍️ Написать в поддержку", callback_data="support:new")]])


def cancel_kb() -> InlineKeyboardMarkup:
    return _ikb([[InlineKeyboardButton(text="❌ Отмена", callback_data="support:cancel")]])


def support_confirm_kb() -> InlineKeyboardMarkup:
    return _ikb(
        [
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data="support:send"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="support:cancel"),
            ]
        ]
    )


def admin_ticket_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return _ikb(
        [
            [
                InlineKeyboardButton(text="✉️ Ответить пользователю", callback_data=f"ticket:reply:{ticket_id}"),
                InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"ticket:close:{ticket_id}"),
            ]
        ]
    )
