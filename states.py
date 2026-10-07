"""states.py — классы FSM (StatesGroup) и вспомогательные функции для состояний."""
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


class ChatStates(StatesGroup):
    """Режим диалога с нейросетью: пока он активен, любой текст уходит в AI."""

    chatting = State()


class SupportStates(StatesGroup):
    """Сценарий создания тикета: ввод текста -> подтверждение."""

    waiting_text = State()
    confirming = State()


class AdminStates(StatesGroup):
    """Администратор пишет ответ на тикет (ticket_id и user_id лежат в FSM data)."""

    replying = State()


async def clear_unless_chatting(state: FSMContext) -> None:
    """Сбросить «формовые» состояния (поддержка и т.п.), но не выкидывать из режима чата."""
    if await state.get_state() != ChatStates.chatting.state:
        await state.clear()
