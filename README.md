# 🤖 Atlas AI — Telegram-бот (@AtlasMateBot)

Коммерческий Telegram-бот на **aiogram 3.x**: чат с нейросетью через **OpenRouter**, подписка с оплатой через **Crypto Pay (@CryptoBot)**, система тикетов поддержки, SQLite.

## Возможности
- 🤖 Чат с LLM: контекст последних 8 сообщений, индикатор «печатает…», разбиение ответов >4096 символов, конвертация Markdown → Telegram HTML с fallback на plain-text
- 💳 Подписка: тарифы (1 месяц — $5, навсегда — $25), счёт через `createInvoice`, кнопки «Оплатить в CryptoBot» / «Проверить оплату», защита от двойного начисления и от проверки чужих счетов
- 👤 Профиль: ID, срок подписки, остаток дневного лимита
- 🎫 Поддержка: FSM (текст → подтверждение), уведомление админу с кнопкой «Ответить пользователю», ответ доставляется с пометкой «Ответ от службы поддержки»; админ-команда `/tickets`
- 🔄 Сброс контекста диалога
- Дневной лимит бесплатных запросов (сброс по UTC), запрос не списывается при ошибке AI

## Структура
```
├── bot.py                 # запуск, роутеры, graceful shutdown
├── config.py              # настройки (.env), тарифы
├── states.py              # FSM-состояния
├── utils.py               # разбиение сообщений, Markdown→HTML, safe_edit
├── database/
│   ├── db.py              # схема, соединение SQLite (WAL), транзакции
│   └── crud.py            # users / tickets / invoices
├── keyboards/builders.py  # меню и инлайн-клавиатуры
├── handlers/
│   ├── common.py          # /start, /help, /cancel, профиль
│   ├── chat.py            # диалог с AI, лимиты, сброс контекста
│   ├── subscription.py    # тарифы, инвойсы, проверка оплаты
│   └── support.py         # тикеты + админ-ответы
└── services/
    ├── openrouter.py      # AsyncOpenAI → OpenRouter + память диалога
    └── cryptopay.py       # клиент Crypto Pay API (aiohttp)
```

## Запуск
1. Python 3.11+
2. Установка:
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Настройка: `cp .env.example .env` и заполните
   - `BOT_TOKEN` — от [@BotFather](https://t.me/BotFather)
   - `OPENROUTER_API_KEY` — https://openrouter.ai/keys (пополните баланс или выберите бесплатную модель в `OPENROUTER_MODEL`)
   - `CRYPTOPAY_TOKEN` — в [@CryptoBot](https://t.me/CryptoBot) → Crypto Pay → Create App (для тестов используйте @CryptoTestnetBot и `CRYPTOPAY_BASE_URL=https://testnet-pay.crypt.bot/api`)
   - `ADMIN_ID` — ваш Telegram ID (узнать: [@userinfobot](https://t.me/userinfobot)). **Админ должен хотя бы раз нажать /start у бота**, иначе уведомления о тикетах не дойдут.
4. Старт: `python bot.py`

## Запуск как сервис (systemd)
```ini
[Unit]
Description=Atlas AI bot
After=network-online.target

[Service]
WorkingDirectory=/opt/chatBOT
ExecStart=/opt/chatBOT/.venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Заметки
- Контекст диалога и FSM-состояния хранятся в памяти и сбрасываются при перезапуске (БД с подписками/тикетами сохраняется). Для масштабирования замените `MemoryStorage` на Redis.
- Для таблицы `users` добавлены служебные колонки `messages_used`, `usage_date` (счётчик дневного лимита) и таблица `invoices` (привязка счетов CryptoBot к пользователю).
- Модель меняется переменной `OPENROUTER_MODEL`.
