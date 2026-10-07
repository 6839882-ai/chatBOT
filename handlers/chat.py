"""handlers/chat.py — общение с нейросетью, проверка лимитов, сброс контекста."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from config import settings
from database import crud
from keyboards.builders import BTN_CHAT, BTN_RESET, MENU_BUTTONS, buy_access_kb, main_menu
from services.openrouter import AIServiceError, OpenRouterClient
from states import ChatStates, clear_unless_chatting
from utils import send_long

logger = logging.getLogger(__name__)
router = Router(name="chat")

# Один активный запрос на пользователя: защищает от гонок лимита и порчи контекста
_user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

MAX_INPUT_CHARS = 4000


@router.message(F.text == BTN_CHAT)
async def enter_chat(message: Message, state: FSMContext) -> None:
    tg = message.from_user
    user = await crud.get_or_create_user(tg.id, tg.username)
    await state.set_state(ChatStates.chatting)
    left = "безлимит" if user.is_premium else f"{user.free_left} из {user.messages_limit}"
    await message.answer(
        "🤖 <b>Режим чата включён.</b> Пишите ваш вопрос!\n"
        f"📊 Доступно запросов сегодня: <b>{left}</b>\n\n"
        "<i>Выйти из режима — /start</i>",
        reply_markup=main_menu(),
    )


@router.message(F.text == BTN_RESET)
async def reset_context(message: Message, state: FSMContext, ai: OpenRouterClient) -> None:
    await clear_unless_chatting(state)
    ai.reset(message.from_user.id)
    await message.answer("🔄 Контекст диалога очищен. Начнём с чистого листа!", reply_markup=main_menu())


@router.message(
    ChatStates.chatting,
    F.text,
    ~F.text.startswith("/"),
    F.text.not_in(MENU_BUTTONS),
)
async def on_chat_message(message: Message, bot: Bot, ai: OpenRouterClient) -> None:
    tg = message.from_user
    lock = _user_locks[tg.id]
    if lock.locked():
        await message.answer("⏳ Я ещё отвечаю на предыдущий вопрос — подождите секунду.")
        return

    async with lock:
        text = message.text.strip()
        if len(text) > MAX_INPUT_CHARS:
            await message.answer(f"✂️ Сообщение слишком длинное (максимум {MAX_INPUT_CHARS} символов).")
            return

        await crud.get_or_create_user(tg.id, tg.username)
        consumed = await crud.try_consume_message(tg.id)
        if consumed == crud.CONSUME_DENIED:
            await message.answer(
                f"🚫 Дневной лимит бесплатных запросов ({settings.free_daily_limit}) исчерпан.\n"
                "Оформите Premium — и общайтесь без ограничений.",
                reply_markup=buy_access_kb(),
            )
            return

        try:
            # ChatActionSender шлёт ChatAction.TYPING каждые ~5 с, пока идёт генерация
            async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
                answer = await ai.ask(tg.id, text)
        except AIServiceError as e:
            if consumed == crud.CONSUME_FREE:
                await crud.refund_message(tg.id)  # не списываем запрос за неудачу
            await message.answer(e.user_message)
            return
        except Exception:
            logger.exception("Неожиданная ошибка при обращении к AI")
            if consumed == crud.CONSUME_FREE:
                await crud.refund_message(tg.id)
            await message.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.")
            return

        await send_long(message, answer)


# Самый последний хендлер всего приложения: текст вне какого-либо состояния
@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def no_mode_hint(message: Message) -> None:
    await message.answer(
        "Чтобы начать диалог с ИИ, нажмите «🤖 Чат с Atlas» или выберите раздел в меню 👇",
        reply_markup=main_menu(),
    )
