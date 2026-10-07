"""handlers/support.py — FSM тикетов поддержки и админ-панель ответов."""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import settings
from database import crud
from keyboards.builders import (
    BTN_SUPPORT,
    MENU_BUTTONS,
    admin_ticket_kb,
    cancel_kb,
    main_menu,
    support_confirm_kb,
    support_start_kb,
)
from states import AdminStates, SupportStates, clear_unless_chatting
from utils import fmt_dt, safe_edit

logger = logging.getLogger(__name__)
router = Router(name="support")

MIN_LEN, MAX_LEN = 5, 3000


def _is_admin(user_id: int) -> bool:
    return user_id == settings.admin_id


def _admin_text(ticket: crud.Ticket, username: str | None) -> str:
    uname = f"@{html.escape(username)}" if username else "—"
    return (
        f"🎫 <b>Тикет #{ticket.id}</b> · {fmt_dt(ticket.created_at)}\n"
        f"👤 ID: <code>{ticket.user_id}</code> · {uname}\n\n"
        f"{html.escape(ticket.message_text)}"
    )


# ------------------------------------------------------------------ пользователь
@router.message(F.text == BTN_SUPPORT)
async def support_menu(message: Message, state: FSMContext) -> None:
    await clear_unless_chatting(state)
    await message.answer(
        "🎫 <b>Поддержка</b>\n\nВозник вопрос или проблема? Опишите её — мы ответим прямо в этом боте.",
        reply_markup=support_start_kb(),
    )


@router.callback_query(F.data == "support:new")
async def support_new(cb: CallbackQuery, state: FSMContext) -> None:
    open_count = await crud.count_open_tickets(cb.from_user.id)
    if open_count >= settings.support_max_open_tickets:
        await cb.answer(
            "У вас уже есть открытые обращения. Дождитесь ответа на них.", show_alert=True
        )
        return
    await state.set_state(SupportStates.waiting_text)
    await safe_edit(
        cb,
        f"✍️ Опишите вашу проблему одним сообщением ({MIN_LEN}–{MAX_LEN} символов).",
        cancel_kb(),
    )
    await cb.answer()


@router.message(SupportStates.waiting_text, F.text, ~F.text.startswith("/"), F.text.not_in(MENU_BUTTONS))
async def support_got_text(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < MIN_LEN:
        await message.answer(f"Слишком коротко — опишите проблему подробнее (от {MIN_LEN} символов).")
        return
    if len(text) > MAX_LEN:
        await message.answer(f"Слишком длинно — сократите до {MAX_LEN} символов (сейчас {len(text)}).")
        return
    await state.update_data(text=text)
    await state.set_state(SupportStates.confirming)
    await message.answer(
        f"📝 <b>Ваше обращение:</b>\n\n<blockquote>{html.escape(text)}</blockquote>\n\nОтправить в поддержку?",
        reply_markup=support_confirm_kb(),
    )


@router.message(SupportStates.waiting_text, ~F.text)
async def support_not_text(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте обращение текстом.", reply_markup=cancel_kb())


@router.message(SupportStates.confirming, F.text, ~F.text.startswith("/"), F.text.not_in(MENU_BUTTONS))
async def support_confirm_hint(message: Message) -> None:
    await message.answer("Используйте кнопки под обращением: «✅ Отправить» или «❌ Отмена».")


@router.callback_query(F.data == "support:cancel")
async def support_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(cb, "❌ Обращение отменено.", None)
    await cb.answer()


@router.callback_query(SupportStates.confirming, F.data == "support:send")
async def support_send(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("text")
    if not text:
        await state.clear()
        await cb.answer("Сессия устарела, начните заново.", show_alert=True)
        return

    tg = cb.from_user
    await crud.get_or_create_user(tg.id, tg.username)
    ticket_id = await crud.create_ticket(tg.id, text)  # сначала сохраняем в БД
    await state.clear()
    await safe_edit(
        cb,
        f"✅ Обращение <b>#{ticket_id}</b> отправлено. Ответ придёт сюда, в бота.",
        None,
    )
    await cb.answer()

    ticket = await crud.get_ticket(ticket_id)
    try:
        await bot.send_message(
            settings.admin_id, _admin_text(ticket, tg.username), reply_markup=admin_ticket_kb(ticket_id)
        )
    except TelegramAPIError:
        # Тикет не потерян: он в БД, админ увидит его через /tickets
        logger.exception("Не удалось уведомить администратора о тикете #%s", ticket_id)


# ------------------------------------------------------------------ администратор
@router.message(Command("tickets"), F.from_user.id == settings.admin_id)
async def admin_list_tickets(message: Message) -> None:
    tickets = await crud.list_open_tickets(limit=10)
    if not tickets:
        await message.answer("Открытых тикетов нет 🎉")
        return
    for t in tickets:
        user = await crud.get_user(t.user_id)
        await message.answer(_admin_text(t, user.username if user else None), reply_markup=admin_ticket_kb(t.id))


@router.callback_query(F.data.startswith("ticket:reply:"))
async def admin_reply_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    ticket = await crud.get_ticket(int(cb.data.split(":")[2]))
    if ticket is None:
        await cb.answer("Тикет не найден", show_alert=True)
        return
    await state.set_state(AdminStates.replying)
    await state.update_data(ticket_id=ticket.id, user_id=ticket.user_id)
    if cb.message:
        await cb.message.answer(f"✍️ Введите ответ на тикет <b>#{ticket.id}</b>. Отмена — /cancel")
    await cb.answer()


@router.message(AdminStates.replying, F.text, ~F.text.startswith("/"), F.text.not_in(MENU_BUTTONS))
async def admin_reply_send(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    ticket_id, user_id = data.get("ticket_id"), data.get("user_id")
    if ticket_id is None or user_id is None:
        await state.clear()
        await message.answer("Сессия ответа устарела. Откройте тикет заново (/tickets).")
        return

    try:
        await bot.send_message(
            user_id,
            f"📩 <b>Ответ от службы поддержки</b> (обращение #{ticket_id})\n\n{html.escape(message.text)}",
            reply_markup=main_menu(),
        )
    except TelegramForbiddenError:
        await message.answer("⚠️ Пользователь заблокировал бота — ответ не доставлен.")
    except TelegramAPIError as e:
        logger.exception("Не удалось доставить ответ по тикету #%s", ticket_id)
        await message.answer(f"⚠️ Ошибка доставки: {html.escape(str(e))}")
    else:
        await crud.set_ticket_status(ticket_id, "answered")
        await message.answer(f"✅ Ответ на тикет #{ticket_id} доставлен.")
        await state.clear()


@router.callback_query(F.data.startswith("ticket:close:"))
async def admin_close_ticket(cb: CallbackQuery) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    ticket_id = int(cb.data.split(":")[2])
    await crud.set_ticket_status(ticket_id, "closed")
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(f"Тикет #{ticket_id} закрыт")
