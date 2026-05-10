# group-deadlines-bot

Telegram-бот для общего списка дедлайнов учебной группы. Любой участник
добавляет задание (предмет, тип, дедлайн, описание, ссылки) — все участники
видят список и получают личные напоминания и еженедельный дайджест.

## Возможности

- Группы с invite-кодом, передача роли creator, мульти-членство
- Предметы, задания (homework / test / exam / lab / project / other)
- Дедлайн обязательный или «📌 До конца модуля»
- Личные напоминания за 7/3/1 день (зависит от типа), recovery после downtime
- Еженедельный дайджест в привязанный групповой чат или в личку каждому
- Reply-keyboard навигация + inline-меню для действий
- Команды и slash-suggestions (`/menu`, `/add`, `/list`, `/digest`, …)

## Стек

Python 3.12, aiogram 3, SQLAlchemy async + aiosqlite, alembic, APScheduler,
pydantic-settings, uv.

## Локальный запуск

```bash
# 1. Зависимости
uv sync

# 2. Конфиг
cp .env.example .env
# в .env заполнить:
#   BOT_TOKEN=...
#   DATABASE_URL=sqlite+aiosqlite:///./data/dev.db
#   DEFAULT_TIMEZONE=Europe/Moscow
#   BOT_PROXY_URL=http://127.0.0.1:12334   # только если нет прямого доступа

# 3. БД
mkdir -p data
uv run alembic upgrade head

# 4. Запуск
uv run main.py
```

## Деплой

Контейнерный, через любой PaaS с Docker Compose (Coolify, Dokploy, Komodo,
docker-compose на VPS):

```bash
docker compose up -d --build
```

`docker-compose.yml` поднимает два сервиса:

- `migrate` — однократно прогоняет `alembic upgrade head` на named volume
  `bot_data:/data`, выходит с кодом 0
- `bot` — стартует через `python main.py` после успешной миграции,
  `restart: unless-stopped`

Обязательные env-переменные у обоих сервисов: `BOT_TOKEN`, `DATABASE_URL`,
`DEFAULT_TIMEZONE`, `LOG_LEVEL`. Опционально `BOT_PROXY_URL` для исходящего
прокси к Telegram.

`HEALTHCHECK` уже в Dockerfile (`pgrep -f "python.*main.py"`). Open ports не
нужны — long polling.

## Структура

```
main.py                 — entrypoint
src/
  config.py             — Settings (pydantic)
  scheduler.py          — APScheduler, schedule/cancel/bootstrap, дайджест-тик
  states.py             — FSM-состояния
  texts.py              — все строки (русский)
  keyboards.py          — inline / reply фабрики
  utils.py              — invite-коды, парсинг ссылок, format_deadline_display
  db/
    models.py           — Group, User, GroupMember, Subject, Assignment,
                          AssignmentCompletion (legacy), ReminderLog
    session.py          — async engine, sessionmaker, sqlite WAL/foreign_keys
  services/
    groups.py           — create / join / leave / transfer / delete
    subjects.py         — list/add/delete (case-insensitive)
    assignments.py      — CRUD + soft-delete + archive_to_passed
    reminders.py        — OFFSETS_BY_TYPE, send_reminder_batch
    digest.py           — еженедельный дайджест на 14 дней
    deadline_parser.py  — «сегодня 18:00», «15.12 23:59», и т.п.
    users.py            — get_or_create_user
  handlers/
    common.py           — /start, /help, /cancel, /digest
    menu.py             — корневой меню-роутер, render(), open_home()
    menu_reply.py       — reply-keyboard → секции
    menu_tasks.py       — список заданий, карточка, done/del/edit/archive
    menu_subjects.py    — предметы (FSM добавления, удаление)
    menu_settings.py    — личные настройки
    menu_groups.py      — хаб группы (switch/create/join/leave)
    menu_admin.py       — TZ/digest_day/digest_hour/regenerate/transfer/delete
    menu_add.py         — entry в FSM AddAssignment
    groups.py           — slash-команды групп: /create_group, /join, /my_groups, /bind_chat, …
    subjects.py         — slash-команды предметов
    assignments.py      — FSM /add, /edit + /delete, /list (proxy)
migrations/             — alembic
docs/SPEC.md            — оригинальная спецификация
```

## Команды

В личке: `/menu`, `/add`, `/list`, `/digest`, `/my_groups`, `/settings`,
`/cancel`, `/help`.

В группах: `/bind_chat` (creator-only пикер), `/digest` (если чат привязан),
`/help`.

Полный список — `/help` в боте.

## Разработка

```bash
uv run ruff check src/ main.py        # линтер
uv run alembic revision --autogenerate -m "..."   # новая миграция
uv run alembic upgrade head           # накат
```

Тестов пока нет (см. SPEC `## Тесты` — план).

## Ограничения

- Один экземпляр процесса: APScheduler in-memory + in-process rate-limiter
  для `/join`. Горизонтальное масштабирование невозможно без переноса
  jobstore в БД.
- Бэкапы базы не предусмотрены в MVP. Volume `bot_data` лучше класть на
  диск с какой-то snapshot-стратегией хостинга.
