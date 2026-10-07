"""
utils.py — общие помощники: разбиение длинных сообщений, Markdown -> Telegram HTML,
безопасная отправка с fallback и безопасное редактирование сообщений.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

# Лимит Telegram — 4096 символов. Берём запас: после конвертации в HTML текст растёт
# (&lt; &gt; &amp; и теги), поэтому режем с запасом, а при превышении — fallback на plain.
CHUNK_LIMIT = 3500


def fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d.%m.%Y %H:%M UTC") if dt else "—"


def split_message(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Режет текст на части ≤ limit, стараясь резать по абзацам, строкам, пробелам."""
    text = text.strip()
    if not text:
        return ["…"]
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit  # нет подходящего разделителя — режем жёстко
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


_CODE_BLOCK = re.compile(r"```([\w+#-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_HEADER = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD2 = re.compile(r"(?<!\w)__(.+?)__(?!\w)", re.DOTALL)
_ITALIC = re.compile(r"(?<![\*\w])\*(?![\s\*])(.+?)(?<![\s\*])\*(?![\*\w])")
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^\s)"<>]+)\)')


def md_to_html(text: str) -> str:
    """
    Конвертирует типичный Markdown от LLM в подмножество HTML, которое понимает Telegram.
    Всё остальное экранируется, поэтому «<» и «&» в ответе модели не ломают разметку.
    """
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    def code_block(m: re.Match[str]) -> str:
        lang, code = m.group(1), html.escape(m.group(2).strip("\n"), quote=False)
        if lang:
            return keep(f'<pre><code class="language-{html.escape(lang)}">{code}</code></pre>')
        return keep(f"<pre>{code}</pre>")

    text = _CODE_BLOCK.sub(code_block, text)
    text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = html.escape(text, quote=False)
    text = _HEADER.sub(r"<b>\1</b>", text)
    text = _BULLET.sub(r"\1• ", text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _BOLD2.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


async def _send(message: Message, text: str, parse_mode: str | None, reply_markup: Any) -> None:
    try:
        await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except TelegramRetryAfter as e:  # флуд-контроль Telegram: ждём и повторяем один раз
        await asyncio.sleep(e.retry_after + 1)
        await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def send_long(message: Message, text: str, reply_markup: Any = None) -> None:
    """
    Отправляет ответ AI: режет на части ≤ 4096 символов, форматирует как HTML.
    Fallback: если Telegram не смог распарсить разметку (TelegramBadRequest),
    то та же часть уходит как обычный текст без parse_mode (т.е. без разбора тегов).
    """
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        rendered = md_to_html(chunk)
        try:
            if len(rendered) > 4096:
                raise TelegramBadRequest(method=None, message="rendered chunk is too long")  # type: ignore[arg-type]
            await _send(message, rendered, ParseMode.HTML, markup)
        except TelegramBadRequest as e:
            logger.warning("Ошибка парсинга разметки, отправляю plain-text: %s", e)
            await _send(message, chunk[:4096], None, markup)


async def safe_edit(cb: CallbackQuery, text: str, reply_markup: Any = None) -> None:
    """edit_text, который не падает на «message is not modified» и недоступных сообщениях."""
    msg = cb.message
    if not isinstance(msg, Message):
        return
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise
