"""handlers/common.py — /start, /help, /cancel и «Мой профиль»."""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from database import crud
from keyboards.builders import BTN_PROFILE, main_menu
from states import clear_unless_chatting
from utils import fmt_dt

logger = logging.getLogger(__name__)
router = Router(name="common")

WELCOME = (
    "👋 Привет, <b>{name}</b>! Я <b>Atlas AI</b> — ваш ИИ-ассистент.\n\n"
    "🤖 <b>Чат с Atlas</b> — задайте любой вопрос\n"
    "💳 <b>Подписка</b> — безлимитный доступ\n"
    "👤 <b>Профиль</b> — лимиты и срок подписки\n"
    "🎫 <b>Поддержка</b> — связь с командой\n"
    "🔄 <b>Сбросить контекст</b> — начать диалог с чистого листа\n\n"
    "Бесплатно доступно <b>{limit}</b> запросов в сутки."
)

HELP = (
    "ℹ️ <b>Справка</b>\n\n"
    "• Нажмите «🤖 Чат с Atlas» и пишите вопросы — я помню последние сообщения диалога.\n"
    "• «🔄 Сбросить контекст» очищает память диалога.\n"
    "• /start — главное меню (выход из режима чата)\n"
    "• /cancel — отменить текущее действие\n"
    "• /help — эта справка"
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    tg = message.from_user
    user = await crud.get_or_create_user(tg.id, tg.username)
    await message.answer(
        WELCOME.format(name=html.escape(tg.first_name or "друг"), limit=user.messages_limit),
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено. Вы в главном меню.", reply_markup=main_menu())


@router.message(F.text == BTN_PROFILE)
async def show_profile(message: Message, state: FSMContext) -> None:
    await clear_unless_chatting(state)
    tg = message.from_user
    user = await crud.get_or_create_user(tg.id, tg.username)

    if user.is_lifetime:
        sub = "💎 навсегда"
        limits = "безлимит"
    elif user.is_premium:
        sub = f"✅ активна до {fmt_dt(user.subscription_until)}"
        limits = "безлимит"
    else:
        sub = "❌ нет"
        limits = f"осталось {user.free_left} из {user.messages_limit} на сегодня"

    username = f"@{html.escape(user.username)}" if user.username else "—"
    await message.answer(
        "👤 <b>Мой профиль</b>\n\n"
        f"🆔 ID: <code>{user.telegram_id}</code>\n"
        f"👥 Username: {username}\n"
        f"📅 Регистрация: {fmt_dt(user.registered_at)}\n"
        f"💳 Подписка: {sub}\n"
        f"📊 Запросы: {limits}",
        reply_markup=main_menu(),
    )
