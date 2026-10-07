"""handlers/subscription.py — тарифы, генерация инвойсов CryptoBot, проверка оплаты."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import PLANS, settings
from database import crud
from keyboards.builders import (
    BTN_SUB,
    back_to_plans_kb,
    invoice_kb,
    plan_details_kb,
    plans_kb,
)
from services.cryptopay import CryptoPayClient, CryptoPayError
from states import clear_unless_chatting
from utils import fmt_dt, safe_edit

logger = logging.getLogger(__name__)
router = Router(name="subscription")


def _plans_text(user: crud.User) -> str:
    if user.is_lifetime:
        status = "💎 У вас подписка <b>навсегда</b>."
    elif user.is_premium:
        status = f"✅ Подписка активна до <b>{fmt_dt(user.subscription_until)}</b>."
    else:
        status = f"❌ Подписки нет. Бесплатно: {user.messages_limit} запросов в сутки."
    lines = "\n".join(
        f"• <b>{p.title}</b> — ${p.price:f}" for p in PLANS.values()
    )
    return (
        "💳 <b>Подписка / Премиум</b>\n\n"
        f"{status}\n\n"
        "Premium даёт <b>безлимитные запросы</b> к Atlas AI.\n\n"
        f"<b>Тарифы:</b>\n{lines}\n\n"
        "Оплата криптовалютой через @CryptoBot (USDT, TON, BTC и др.)."
    )


@router.message(F.text == BTN_SUB)
async def show_subscription(message: Message, state: FSMContext) -> None:
    await clear_unless_chatting(state)
    tg = message.from_user
    user = await crud.get_or_create_user(tg.id, tg.username)
    await message.answer(_plans_text(user), reply_markup=plans_kb())


@router.callback_query(F.data == "sub:plans")
async def back_to_plans(cb: CallbackQuery) -> None:
    user = await crud.get_or_create_user(cb.from_user.id, cb.from_user.username)
    await safe_edit(cb, _plans_text(user), plans_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("plan:"))
async def show_plan(cb: CallbackQuery) -> None:
    plan = PLANS.get(cb.data.split(":", 1)[1])
    if plan is None:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    term = "навсегда" if plan.days is None else f"на {plan.days} дн."
    await safe_edit(
        cb,
        f"🧾 <b>Тариф: {plan.title}</b>\n\n"
        f"Доступ {term}, безлимитные запросы.\n"
        f"Стоимость: <b>${plan.price:f}</b>\n\n"
        "Нажмите «Оплатить», чтобы получить счёт в CryptoBot.",
        plan_details_kb(plan.key),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pay:"))
async def create_invoice(cb: CallbackQuery, cryptopay: CryptoPayClient) -> None:
    plan = PLANS.get(cb.data.split(":", 1)[1])
    if plan is None:
        await cb.answer("Тариф не найден", show_alert=True)
        return

    await cb.answer("Создаю счёт…")
    tg = cb.from_user
    await crud.get_or_create_user(tg.id, tg.username)
    try:
        invoice = await cryptopay.create_invoice(
            amount=f"{plan.price:f}",
            description=f"Atlas AI — {plan.title}",
            payload=f"{tg.id}:{plan.key}",
            expires_in=settings.invoice_ttl_seconds,
        )
    except CryptoPayError:
        logger.exception("Не удалось создать инвойс")
        await safe_edit(
            cb,
            "⚠️ Не удалось создать счёт. Попробуйте ещё раз через минуту.",
            back_to_plans_kb(),
        )
        return

    await crud.create_invoice_record(invoice.invoice_id, tg.id, plan.key, f"{plan.price:f}")
    minutes = settings.invoice_ttl_seconds // 60
    await safe_edit(
        cb,
        f"🧾 <b>Счёт №{invoice.invoice_id}</b>\n"
        f"Тариф: <b>{plan.title}</b> — ${plan.price:f}\n\n"
        "1️⃣ Нажмите «Оплатить в CryptoBot» и завершите платёж.\n"
        "2️⃣ Вернитесь сюда и нажмите «Проверить оплату».\n\n"
        f"<i>Счёт действителен {minutes} мин.</i>",
        invoice_kb(invoice.pay_url, invoice.invoice_id),
    )


@router.callback_query(F.data.startswith("check:"))
async def check_payment(cb: CallbackQuery, cryptopay: CryptoPayClient) -> None:
    try:
        invoice_id = int(cb.data.split(":", 1)[1])
    except ValueError:
        await cb.answer("Некорректный счёт", show_alert=True)
        return

    record = await crud.get_invoice(invoice_id)
    # Проверяем владельца: нельзя активировать подписку чужим счётом
    if record is None or record.user_id != cb.from_user.id:
        await cb.answer("Счёт не найден", show_alert=True)
        return
    if record.status == "paid":
        await cb.answer("Этот счёт уже оплачен и активирован ✅", show_alert=True)
        return

    try:
        remote = await cryptopay.get_invoice(invoice_id)
    except CryptoPayError:
        await cb.answer("Не удалось проверить платёж. Попробуйте позже.", show_alert=True)
        return
    if remote is None:
        await cb.answer("Счёт не найден в CryptoBot.", show_alert=True)
        return

    if remote.status == "paid":
        plan = PLANS.get(record.plan)
        if plan is None:
            logger.error("Оплачен инвойс %s с неизвестным тарифом %s", invoice_id, record.plan)
            await cb.answer("Ошибка тарифа. Напишите в поддержку.", show_alert=True)
            return
        until = await crud.settle_invoice(invoice_id, plan.days)
        if until is None:  # параллельный клик уже засчитал оплату
            await cb.answer("Оплата уже засчитана ✅", show_alert=True)
            return
        when = "навсегда" if plan.days is None else f"до {fmt_dt(until)}"
        await safe_edit(
            cb,
            f"✅ <b>Оплата получена!</b>\nПодписка «{plan.title}» активна {when}.\n"
            "Теперь у вас безлимитный доступ к Atlas AI 🚀",
            None,
        )
        await cb.answer("Подписка активирована!")
    elif remote.status == "expired":
        await crud.expire_invoice(invoice_id)
        await safe_edit(cb, "⌛ Срок действия счёта истёк. Создайте новый.", back_to_plans_kb())
        await cb.answer()
    else:
        await cb.answer("⏳ Оплата пока не поступила. Завершите платёж и нажмите снова.", show_alert=True)
