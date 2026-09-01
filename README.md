# group-deadlines-bot

Telegram-бот для общего списка дедлайнов учебной группы. Любой участник
добавляет задачу или важную дату — все участники видят список и получают
личные напоминания и еженедельный дайджест.

## Возможности

- Группы с invite-кодом, передача роли создателя, мульти-членство
- Два типа записей: **задачи** (что нужно сделать, дедлайн необязателен —
  «📌 До конца модуля») и **важные даты** (контрольная, экзамен, зачёт,
  защита; дата обязательна). В списке они идут отдельными секциями.
- Дедлайн выбирается инлайн-календарём — текстом дату вводить не нужно
- Настраиваемые сроки напоминаний: набор по умолчанию задаётся группе
  (отдельно для задач и важных дат) и переопределяется на каждой записи
- Напоминания, заданные в днях, приходят в выбранный час по времени группы,
  короткие — ровно за указанный интервал. Recovery после простоя.
- Еженедельный дайджест в привязанный групповой чат или в личку каждому
- Ссылка-приглашение `t.me/бот?start=join-КОД` — вход в группу одним тапом
- Inline-режим: `@бот` в любом чате вставляет ближайшие дедлайны
- Дайджест закрепляется в чате, предыдущий закреп снимается
- Бот сам предлагает привязать чат, когда его туда добавили
- Личный чек-лист: отметка видна только тебе и снимает напоминания по записи
- Дедлайны показываются в часовом поясе читателя (entity `date_time`)
- Длинные описания, ссылки и блок прошедших — сворачиваемыми цитатами
- Reply-клавиатура для разделов, всё остальное — инлайн-кнопками

## Стек

Python 3.12, aiogram 3, SQLAlchemy async + aiosqlite, alembic, APScheduler,
pydantic, uv.

## Локальный запуск

```bash
uv sync

cp .env.example .env
# BOT_TOKEN=...
# DATABASE_URL=sqlite+aiosqlite:///./data/dev.db
# DEFAULT_TIMEZONE=Europe/Vilnius
# BOT_PROXY_URL=http://127.0.0.1:12334   # только если нет прямого доступа

mkdir -p data
uv run alembic upgrade head
uv run main.py
```

## Архитектура

Слои разделены строго: ORM живёт только в `db/`, репозитории возвращают
доменные pydantic-модели, сервисы оперируют доменом, `views/` превращают
домен в текст, хендлеры только собирают ввод и вызывают сервис плюс view.

```
main.py                 — entrypoint, порядок роутеров
src/
  config.py             — Settings (pydantic-settings)
  scheduler.py          — APScheduler: расписание напоминаний, дайджест-тик
  states.py             — FSM-состояния
  utils.py              — invite-коды, парсинг ссылок
  domain/               — чистая логика без БД и Telegram
    enums.py            — ItemKind, категории задач и важных дат, роли
    models.py           — Item, ItemDraft, ItemPatch, Group, Member, Subject
    reminders.py        — пресеты сроков, resolve_offsets, plan_reminders
    calendar.py         — сетка месяца, навигация, пресеты и парсинг времени
    humanize.py         — русские склонения, форматирование дат
  db/
    models.py           — таблицы: groups, users, group_members, subjects,
                          items, reminder_log
    session.py          — async engine, sessionmaker, sqlite WAL/foreign_keys
    repositories/       — users, groups, subjects, items, reminders, completions
  services/
    groups.py           — GroupManager: группы, участники, предметы
    items.py            — ItemManager: CRUD записей, сборка списка
    reminders.py        — ReminderService: рассылка с идемпотентностью
    digest.py           — DigestService: слоты и отправка дайджеста
  views/                — рендер текста (parse_mode=HTML)
    common.py           — экранирование, нарезка под лимит Telegram
    items.py            — список, карточка, предпросмотр, дайджест
    reminders.py        — текст напоминания
    texts.py            — все строки интерфейса
  ui/
    callbacks.py        — типизированные CallbackData-фабрики
    keyboards.py        — сборка клавиатур, включая календарь
  handlers/             — тонкие обработчики
    ui.py               — show/notify, загрузка контекста пользователя
    menu.py             — /start, /menu, /help, /cancel, главное меню
    reply.py            — нижние кнопки; выше FSM-роутеров, прерывает ввод
    items.py            — список, карточка, мастер добавления, календарь
    subjects.py         — предметы
    groupctl.py         — создание, вход, переключение, выход
    settings.py         — личные настройки и настройки группы
    chat.py             — групповой чат: /bind_chat, /digest, приветствие
    inline.py           — inline-режим: дедлайны в любом чате
migrations/             — alembic
tests/                  — домен, репозитории, сервисы, views, UI, сквозные
```

Порядок роутеров в `main.py` значим: `reply` подключается выше FSM-роутеров,
поэтому нажатие нижней кнопки всегда прерывает незаконченный ввод.

## Команды

В личке: `/menu`, `/join <код>`, `/cancel`, `/help`. Всё остальное — кнопками.

В групповом чате: `/bind_chat`, `/digest`, `/help`.

Inline-режим нужно включить у @BotFather: `/setinline` для бота, иначе
`@бот` в чатах работать не будет.

## Разработка

```bash
uv run pytest                                     # тесты
uv run ruff check . && uv run ruff format .       # линтер и формат
uv run ty check                                   # типы
uv run alembic revision --autogenerate -m "..."   # новая миграция
uv run alembic upgrade head                       # накат
```

## Деплой

```bash
docker compose up -d --build
```

Контейнер сам прогоняет `alembic upgrade head` перед стартом. Обязательные
env: `BOT_TOKEN`, `DATABASE_URL`, `DEFAULT_TIMEZONE`, `LOG_LEVEL`; опционально
`BOT_PROXY_URL`. Порты не нужны — long polling. `HEALTHCHECK` в Dockerfile.

## Ограничения

- Один экземпляр процесса: APScheduler держит задания в памяти, rate-limiter
  `/join` — тоже. Горизонтальное масштабирование потребует переноса jobstore
  в БД.
- Бэкапы базы не предусмотрены. Volume `bot_data` стоит класть на диск со
  snapshot-стратегией хостинга.
