# ТЗ: Telegram-бот для учёта дедлайнов учебной группы

## Стек
- Python 3.11+
- `aiogram` 3.x
- `SQLAlchemy` 2.x + `aiosqlite`
- `APScheduler`
- `pydantic-settings`
- `alembic`
- Тулинг: `uv` (зависимости и venv), `ruff` (линтер + форматтер), `ty` (type checker)
- Деплой: Coolify, Docker Compose, SQLite на persistent volume

## Архитектура

Бот работает в режиме long polling. Группа в боте — логическая сущность, не привязанная к конкретному Telegram-чату. Пользователь общается с ботом в личке; опционально бот может быть добавлен в групповой чат для еженедельных дайджестов.

Подключение к группе через invite-код формата `XXXX-XXXX-XXXX`. Алфавит — Crockford base32 без неоднозначных символов (исключены `0/O`, `1/I/L`); 12 символов дают ~60 бит энтропии. Создатель группы может перевыпустить код. На `/join` действует rate-limit: 5 попыток в минуту на `tg_user_id`.

У пользователя может быть несколько групп; одна из них «активная» — все командные действия (`/add`, `/list`, `/done`, `/settings` и т.д.) выполняются в её контексте. Активная группа хранится в `User.active_group_id`. Переключение через `/my_groups`. Если активной группы нет (только что присоединился, вышел из последней), бот предлагает выбрать или создать.

## Модель данных

```
Group
  id: int (PK)
  name: str
  invite_code: str (unique)
  timezone: str (IANA, напр. "Europe/Vilnius")
  digest_day: int (0-6, дефолт 6 — воскресенье)
  digest_hour: int (0-23, дефолт 19)
  chat_id: int | None
  created_by_user_id: int (FK)
  created_at: datetime

User
  id: int (PK)
  tg_user_id: int (unique)
  tg_username: str | None
  full_name: str
  active_group_id: int | None (FK на Group; обнуляется при /leave из активной)
  created_at: datetime

GroupMember
  id: int (PK)
  group_id: int (FK)
  user_id: int (FK)
  role: enum("creator", "member")
  personal_reminders_enabled: bool (дефолт True)
  joined_at: datetime
  UNIQUE(group_id, user_id)

Subject
  id: int (PK)
  group_id: int (FK)
  name: str
  UNIQUE(group_id, name)

Assignment
  id: int (PK)
  group_id: int (FK)
  short_id: int (последовательный в рамках группы; именно его пользователь видит в /list и передаёт в /done, /delete, /edit)
  subject_id: int (FK)
  type: enum("homework", "test", "exam", "lab", "project", "other")
  title: str
  description: str | None
  links: list[str] (JSON)
  deadline: datetime (UTC в БД, отображается в TZ группы)
  created_by_user_id: int (FK)
  created_at: datetime
  is_active: bool (False = софт-удалено через /delete. Автоматически при прошедшем дедлайне НЕ сбрасывается — задание остаётся `is_active=True` и попадает в раздел «Прошедшие» в `/list` в течение 2 недель после дедлайна. Отметки и логи сохраняются)
  UNIQUE(group_id, short_id)

AssignmentCompletion
  id: int (PK)
  assignment_id: int (FK)
  user_id: int (FK)
  completed_at: datetime
  UNIQUE(assignment_id, user_id)

ReminderLog
  id: int (PK)
  assignment_id: int (FK)
  user_id: int | None (NULL = групповой дайджест)
  reminder_type: enum("digest", "advance_7d", "advance_3d", "advance_1d")
  sent_at: datetime
  UNIQUE(assignment_id, user_id, reminder_type)
```

`ReminderLog` обеспечивает идемпотентность отправки.

### Настройки SQLite

При создании engine через `aiosqlite` на каждом коннекте устанавливаются прагмы (через `event.listens_for(engine.sync_engine, "connect")`):
- `journal_mode=WAL` — читатели не блокируют писателя
- `synchronous=NORMAL` — компромисс durability/скорость, достаточный для бота с парой событий в секунду
- `busy_timeout=5000` — 5 секунд ожидания вместо мгновенного `database is locked`
- `foreign_keys=ON` — иначе SQLite FK не проверяет

## Команды

**Без группы:**
- `/start` — приветствие, краткая справка
- `/create_group <название>` — создать группу, бот возвращает invite-код
- `/join <код>` — присоединиться к группе
- `/help` — справка

**С активной группой:**
- `/add` — добавить задание (FSM: тип → предмет → название → описание → дедлайн → ссылки → подтверждение). На шаге описания и ссылок можно отправить `-`, чтобы пропустить. Ссылки вводятся одной строкой через пробел или каждая с новой строки; валидация — простой regex на `https?://…`
- `/cancel` — прервать любой активный FSM-сценарий
- `/list` — два раздела:
  - **Активные:** `is_active=True AND deadline >= now()`, по возрастанию дедлайна. Выполненные мной показываются с маркером (например, `✓` или strikethrough), не скрываются.
  - **Прошедшие:** `is_active=True AND now() − 14 дней ≤ deadline < now()`, по убыванию дедлайна. Старше 2 недель — не показываются (но в БД остаются).

  Id в выводе — `short_id` в рамках группы.
- `/list_subject <предмет>` — то же деление, фильтр по предмету
- `/done <id>` — отметить выполненным (только для себя)
- `/undone <id>` — снять отметку
- `/delete <id>` — удалить (только автор или создатель группы); это soft-delete: `is_active=False`, отметки сохраняются, джобы напоминаний отменяются
- `/edit <id>` — редактировать (только автор или создатель группы). Запускает FSM по тем же шагам, что и `/add` (тип → предмет → название → описание → дедлайн → ссылки), на каждом шаге отправка `-` оставляет старое значение. При фактическом изменении дедлайна — см. раздел «Логика напоминаний»
- `/subjects` — список предметов
- `/add_subject <название>` — добавить предмет
- `/delete_subject <название>` — удалить предмет; разрешено только если на нём нет активных заданий (проверка `WHERE subject_id=? AND is_active=True`)
- `/my_groups` — список групп пользователя + переключение активной
- `/leave` — выйти из активной группы. Если выходящий — единственный creator и в группе остались участники, команда просит сначала `/transfer_ownership`. Если creator один в группе — `/leave` удаляет группу
- `/settings` — личные настройки в активной группе (вкл/выкл личные напоминания — `GroupMember.personal_reminders_enabled`)

**Только для создателя группы:**
- `/group_settings` — TZ, день и час дайджеста
- `/regenerate_code` — перевыпустить invite-код
- `/bind_chat` — выполняется в групповом чате, привяжет его для дайджестов (см. «Привязка группового чата»)
- `/unbind_chat` — отвязать ранее привязанный чат
- `/transfer_ownership` — без аргументов; бот показывает inline-список участников группы, creator выбирает нового владельца, требуется явное подтверждение в следующем сообщении
- `/delete_group` — удалить группу с двухступенчатым подтверждением; каскадно удаляет членства, задания, отметки, логи

## Логика напоминаний

Дефолтные офсеты по типу:
- `homework`: за 1 день
- `test`, `exam`: за 7, 3, 1 день
- `lab`, `project`: за 3, 1 день
- `other`: за 1 день

Реализация:
- APScheduler работает с in-memory jobstore; `SQLAlchemyJobStore` не подключается, источник истины по джобам — таблицы БД.
- При создании/редактировании задания планируются джобы APScheduler с учётом TZ группы.
- Перед отправкой проверка `ReminderLog`, дубли скипаются.
- При soft-delete задания / прошедшем дедлайне джобы отменяются.
- При старте бота — пересоздание активных джоб из БД.
- Recovery после downtime: при пересоздании для каждой пары `(assignment, offset)` смотрим `ReminderLog`. Если запись есть — скип. Если записи нет и время напоминания уже прошло, но до дедлайна остаётся ≥ 30 минут — отправляем сейчас и пишем лог. Если до дедлайна < 30 минут — скипаем (поздно беспокоить).
- При `/edit` дедлайна задания все записи `ReminderLog` для этого `assignment_id` удаляются и джобы пересоздаются — это допускает повторную отправку напоминаний на новых офсетах от нового дедлайна, что считается ожидаемым UX «дедлайн перенесли, ждите свежие напоминания».

Личные напоминания идут только пользователям с `personal_reminders_enabled=True` и без отметки `AssignmentCompletion` для этого задания.

Еженедельный дайджест:
- Джоба раз в час проверяет, наступил ли `digest_day` + `digest_hour` в TZ группы.
- Дайджест содержит активные задания с дедлайнами в ближайшие 14 дней, сгруппированные по дням.
- Отправляется в `chat_id` если привязан, иначе всем участникам в личку.
- Если суммарный текст превышает ~4000 символов, дайджест разбивается на несколько сообщений по границам дней (не разрывать день между сообщениями).

## Привязка группового чата

`/bind_chat` выполняется в групповом чате creator-ом группы. Бот:
- проверяет, что отправитель — creator одной из групп, активной для этого пользователя;
- проверяет, что у бота есть право писать (`getChat` → `permissions.can_send_messages` или статус администратора);
- если у группы уже привязан другой `chat_id` — спрашивает подтверждение перед заменой;
- сохраняет `chat_id` в `Group`.

`/unbind_chat` (в личке у бота, creator-only) — обнуляет `chat_id`; дайджест после этого уходит участникам в личку.

## Парсинг дедлайнов

Принимать форматы:
- `завтра 23:59`, `сегодня 18:00`
- `15.12 18:00`
- `15.12.2026 18:00`

Если год не указан — текущий; если получившаяся дата (без указания года) уже прошла — следующий год. Время в TZ группы, в БД хранится UTC.

Если конкретная дата+время указана явно (включая «сегодня 18:00» в 19:00) и уже в прошлом — бот просит переввод, а не молча сдвигает на следующий день. Все TZ-операции опираются на TZ группы, индивидуальный TZ участников в MVP не поддерживается.

### Формат отображения

В `/list`, в напоминаниях и в дайджесте дата+время выводятся в TZ группы:
- Будущее, в этом году: `15.12 23:59 (через 14 часов)` или `15.12 23:59 (через 3 дня)`
- Будущее, не в этом году: `15.12.2026 23:59 (через 8 дней)`
- Прошедшее: `15.12 23:59 (просрочено на 2 дня)`

Относительная часть в скобках округляется: `< 1 часа` — «через X минут»; `< 1 суток` — «через X часов»; `≥ 1 суток` — «через N дней».

## Структура проекта

```
bot/
  __init__.py
  main.py
  config.py
  texts.py
  db/
    __init__.py
    models.py
    session.py
  handlers/
    __init__.py
    common.py
    groups.py
    assignments.py
    subjects.py
    settings.py
  services/
    __init__.py
    groups.py
    assignments.py
    reminders.py
    deadline_parser.py
  scheduler.py
  keyboards.py
  states.py
  utils.py
migrations/
tests/
  test_deadline_parser.py
  test_reminders.py
  test_services.py
.env.example
docker-compose.yml
Dockerfile
pyproject.toml
README.md
```

## Конфиг (env)

```
BOT_TOKEN=
DATABASE_URL=sqlite+aiosqlite:////data/bot.db
LOG_LEVEL=INFO
DEFAULT_TIMEZONE=Europe/Vilnius
```

## Деплой (Coolify)

**Dockerfile:**
- база `python:3.11-slim`
- установка зависимостей через `uv sync --frozen --no-dev` (используется `uv.lock` из репо)
- non-root user
- единый образ для бота и для миграций; конкретная команда задаётся в `docker-compose.yml`. `entrypoint.sh` не нужен — миграции вынесены в отдельный сервис

**docker-compose.yml:**
- сервис `migrate` (init-контейнер, одноразовый): тот же образ, команда `alembic upgrade head`, монтирует `bot_data:/data`, `restart: "no"`. Завершается с кодом 0 при успехе
- сервис `bot`: тот же образ, команда — запуск бота (long polling). `depends_on: { migrate: { condition: service_completed_successfully } }`, монтирует тот же `bot_data:/data`, `restart: unless-stopped`
- env-переменные подтягиваются из Coolify обоими сервисами (`DATABASE_URL`, `BOT_TOKEN`, `LOG_LEVEL`, `DEFAULT_TIMEZONE`)
- без `ports` (бот на long polling, HTTP не нужен)
- если миграция падает — `bot` не стартует, ошибку видно в логах сервиса `migrate` отдельно от бота

**Coolify настройки:**
- Source: Git-репозиторий, ветка `main`
- Build Pack: Docker Compose
- Persistent Storage: `/data` смонтирован в named volume
- Environment Variables: `BOT_TOKEN`, `DATABASE_URL`, `LOG_LEVEL`, `DEFAULT_TIMEZONE` — `BOT_TOKEN` помечен как секрет
- Webhook автодеплоя на push в `main`
- No domain (HTTP не нужен)
- Healthcheck: TBD на этапе 8. Кандидаты:
  - (а) отключить healthcheck в Coolify — простейший вариант
  - (б) `HEALTHCHECK` в Dockerfile через `pgrep -f "python.*bot.main"` — без лишних зависимостей
  - (в) поднять рядом с polling-ом тривиальный aiohttp-эндпоинт `/health` (aiohttp всё равно идёт транзитивной зависимостью aiogram), и Coolify ходит туда

  Решить при первом деплое; склонность — к (б), если pgrep есть в slim-образе, иначе (в).

**Бэкапы:** на старте — cron на хосте, копирующий volume в S3 / на другой сервер. Позже — `litestream` sidecar для непрерывной репликации SQLite в S3.

## Требования к коду

- Все хендлеры async
- Логирование через `logging.getLogger(__name__)`, без print
- Type hints, синтаксис 3.10+ (`str | None`, `list[X]`)
- Pydantic — для конфига (`pydantic-settings`); внутри хендлеров не нужен, валидация делается через FSM и сервисный слой
- Внутренние функции с префиксом `_`
- Docstring на русском только там, где имя/сигнатура неочевидны; не писать `"""Создаёт группу."""` над `def create_group(...)`
- Импорты, форматирование и линтинг — через `ruff` (конфиг в `pyproject.toml`); типы — через `ty`
- Все строки бота в `bot/texts.py` (русский)
- Менеджер зависимостей — `uv` (`uv sync`, `uv run`); локов в репо: `uv.lock`

## Тесты

Стек: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"` в `pyproject.toml`). БД в тестах — отдельный SQLite-файл во временной директории, миграции через `alembic upgrade head` в фикстуре сессии (на каждый тест-модуль).

Минимум:
- Парсер дедлайнов — все форматы и граничные случаи (включая прошедшее время, переход года, TZ группы)
- Логика выбора офсетов по типу задания
- Идемпотентность `ReminderLog`
- Recovery после downtime (порог 30 минут)
- Проверка прав на удаление/редактирование
- `short_id` уникален и последователен в рамках группы

## Этапы

1. Скелет проекта (`uv init`, `ruff`/`ty` в `pyproject.toml`), конфиг, БД, миграции, Dockerfile, docker-compose
2. `/start`, `/create_group`, `/join`, `/my_groups`, переключение активной группы
3. Предметы + `/add` (FSM с `/cancel`), `/list`, `/delete` (soft)
4. `/done`, `/edit`, проверка прав, `/transfer_ownership`, `/delete_group`
5. Планировщик: личные напоминания за N дней + recovery после downtime
6. Еженедельный дайджест (с разбивкой по 4000 символов), `/bind_chat` / `/unbind_chat`
7. Настройки группы и пользователя
8. Деплой в Coolify, проверка автодеплоя, настройка бэкапов

## Вне MVP

- Импорт расписания
- Realtime-уведомления участникам о новых заданиях
- Веб-интерфейс
- Тонкая настройка ролей
- Локализация (только русский)