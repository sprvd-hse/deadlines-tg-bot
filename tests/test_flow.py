import pytest
from aiogram import Dispatcher

from src.handlers import chat, groupctl, inline, items, menu, reply, subjects
from src.handlers import settings as settings_handlers
from tests.fake_telegram import (
    added_to_chat_update,
    callback_update,
    inline_update,
    make_bot,
    message_update,
)

pytestmark = pytest.mark.usefixtures("app_db")


def _dispatcher() -> Dispatcher:
    """Роутеры — модульные синглтоны, поэтому диспетчер собирается один раз на прогон."""
    dp = Dispatcher()
    for router in (
        menu.router,
        reply.router,
        items.router,
        subjects.router,
        groupctl.router,
        settings_handlers.router,
        chat.router,
        inline.router,
    ):
        dp.include_router(router)
    return dp


_DP = _dispatcher()


@pytest.fixture
async def app():
    """Бот-заглушка поверх общего диспетчера с чистым FSM-хранилищем."""
    await _DP.storage.close()
    _DP.storage.storage.clear()
    bot, session = make_bot()
    counter = {"n": 0}

    async def send(text: str):
        counter["n"] += 1
        await _DP.feed_update(bot, message_update(text, update_id=counter["n"]))

    async def press(data: str):
        counter["n"] += 1
        await _DP.feed_update(bot, callback_update(data, update_id=counter["n"]))

    async def tap(fragment: str):
        await press(session.data_of(session.find_button(fragment)))

    async def inline(query: str):
        counter["n"] += 1
        await _DP.feed_update(bot, inline_update(query, update_id=counter["n"]))

    async def chat_added():
        counter["n"] += 1
        await _DP.feed_update(bot, added_to_chat_update(update_id=counter["n"]))

    session.inline = inline
    session.chat_added = chat_added
    session.send = send
    session.press = press
    session.tap = tap
    return session


async def _bootstrap_group(app) -> None:
    await app.send("/start")
    await app.send("📚 Предметы")
    await app.send("👥 Группа")
    await app.tap("Создать")
    await app.send("кабаньи дедлайны")


def _second_user():
    """Второй пользователь на том же диспетчере."""
    import tests.fake_telegram as ft

    bot, session = make_bot()
    counter = {"n": 5000}

    async def send(text: str):
        counter["n"] += 1
        await _DP.feed_update(
            bot,
            message_update(
                text,
                update_id=counter["n"],
                user_id=ft.USER_ID + 1,
                chat_id=ft.CHAT_ID + 1,
            ),
        )

    session.send = send
    return session


async def _seed_one_task(app, title: str) -> None:
    """Группа с предметом и одной задачей на завтра."""
    await _bootstrap_group(app)
    await _add_subject(app, "Матан")
    await app.send("➕ Добавить")
    await app.tap("Задача")
    await app.tap("Матан")
    await app.tap("Лаба")
    await app.send(title)
    await app.tap("Пропустить")
    await app.tap("Завтра")
    await app.tap("23:59")
    await app.tap("Пропустить")
    await app.tap("Создать")


async def _add_subject(app, name: str) -> None:
    await app.send("📚 Предметы")
    await app.tap("Добавить")
    await app.send(name)


class TestOnboarding:
    async def test_start_offers_group_creation(self, app):
        await app.send("/start")
        assert "нет активной группы" in app.last_text

    async def test_group_created_with_invite_code(self, app):
        await _bootstrap_group(app)
        assert "кабаньи дедлайны" in app.last_text or "Invite-код" in "".join(app.texts)

    async def test_adding_item_without_subjects_is_blocked(self, app):
        await _bootstrap_group(app)
        await app.send("➕ Добавить")
        assert "предмет" in app.last_text.lower()


class TestAddTaskFlow:
    async def test_full_task_flow_via_calendar(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")

        await app.send("➕ Добавить")
        assert "Что добавляем" in app.last_text
        await app.tap("Задача")

        assert "Предмет" in app.last_text
        await app.tap("Матан")

        assert "Категория" in app.last_text
        await app.tap("Лаба")

        assert "Название" in app.last_text
        await app.send("Лабораторная 4")

        assert "Описание" in app.last_text
        await app.tap("Пропустить")

        # календарь: дата выбирается кнопкой, текстом ничего вводить не нужно
        assert "Когда сдавать" in app.last_text
        await app.tap("Завтра")

        assert "Теперь время" in app.last_text
        await app.tap("23:59")

        assert "Ссылки" in app.last_text
        await app.tap("Пропустить")

        assert "Лабораторная 4" in app.last_text
        await app.tap("Создать")

        assert "Лабораторная 4" in app.last_text
        assert "Напоминания как в группе" in app.last_text

    async def test_task_can_have_no_deadline(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        await app.send("➕ Добавить")
        await app.tap("Задача")
        await app.tap("Матан")
        await app.tap("Лаба")
        await app.send("Без срока")
        await app.tap("Пропустить")
        await app.tap("До конца модуля")
        await app.tap("Пропустить")
        await app.tap("Создать")
        assert "до конца модуля" in app.last_text

    async def test_event_flow_has_no_open_deadline_option(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Право")
        await app.send("➕ Добавить")
        await app.tap("Важная дата")
        await app.tap("Право")
        await app.tap("Экзамен")
        await app.send("Экзамен по праву")
        await app.tap("Пропустить")
        labels = [b["text"] if isinstance(b, dict) else b.text for b in app.buttons()]
        assert not any("До конца модуля" in label for label in labels)

    async def test_cancel_returns_to_list(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        await app.send("➕ Добавить")
        await app.tap("Задача")
        await app.tap("Отмена")
        assert "Задачи" in app.last_text or "Пока пусто" in app.last_text

    async def test_reply_button_interrupts_wizard(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        await app.send("➕ Добавить")
        await app.tap("Задача")
        await app.tap("Матан")
        await app.tap("Лаба")
        # находимся на шаге ввода названия — жмём нижнюю кнопку другого раздела
        await app.send("📚 Предметы")
        assert "Предметы группы" in app.last_text


class TestListAndCard:
    async def _seed_task(self, app, title="Лабораторная 4"):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        await app.send("➕ Добавить")
        await app.tap("Задача")
        await app.tap("Матан")
        await app.tap("Лаба")
        await app.send(title)
        await app.tap("Пропустить")
        await app.tap("Завтра")
        await app.tap("23:59")
        await app.tap("Пропустить")
        await app.tap("Создать")

    async def test_list_shows_task_section(self, app):
        await self._seed_task(app)
        await app.send("📋 Задания")
        assert "📝 Задачи" in app.last_text
        assert "Лабораторная 4" in app.last_text

    async def test_events_listed_separately(self, app):
        await self._seed_task(app)
        await app.send("➕ Добавить")
        await app.tap("Важная дата")
        await app.tap("Матан")
        await app.tap("Экзамен")
        await app.send("Экзамен")
        await app.tap("Пропустить")
        await app.tap("+7 дней")
        await app.tap("12:00")
        await app.tap("Пропустить")
        await app.tap("Создать")

        await app.send("📋 Задания")
        assert "📝 Задачи" in app.last_text
        assert "📅 Важные даты" in app.last_text
        assert app.last_text.index("Лабораторная") < app.last_text.index("Экзамен")

    async def test_open_card_and_delete(self, app):
        await self._seed_task(app)
        await app.send("📋 Задания")
        await app.tap("Лабораторная 4")
        assert "🕒" in app.last_text
        await app.tap("Удалить")
        await app.tap("Да, удалить")
        assert "Лабораторная 4" not in app.last_text

    async def test_empty_list_message(self, app):
        await _bootstrap_group(app)
        await app.send("📋 Задания")
        assert "Пока пусто" in app.last_text


class TestReminderSettings:
    async def test_group_defaults_are_editable(self, app):
        await _bootstrap_group(app)
        await app.send("⚙️ Настройки")
        await app.tap("Настройки группы")
        await app.tap("Напоминания")
        assert "за день" in app.last_text

        await app.tap("Для задач")
        await app.tap("за 2 часа")
        await app.tap("Готово")
        assert "за 2 часа" in app.last_text

    async def test_reminder_hour_is_configurable(self, app):
        await _bootstrap_group(app)
        await app.send("⚙️ Настройки")
        await app.tap("Настройки группы")
        await app.tap("Напоминания")
        await app.tap("Время рассылки")
        await app.tap("08")
        assert "08:00" in app.last_text

    async def test_personal_reminders_toggle(self, app):
        await _bootstrap_group(app)
        await app.send("⚙️ Настройки")
        assert "включены" in app.last_text
        await app.tap("Выключить личные напоминания")
        assert "выключены" in app.last_text

    async def test_item_reminders_override_group(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        await app.send("➕ Добавить")
        await app.tap("Задача")
        await app.tap("Матан")
        await app.tap("Лаба")
        await app.send("Лаба")
        await app.tap("Пропустить")
        await app.tap("+7 дней")
        await app.tap("23:59")
        await app.tap("Пропустить")
        await app.tap("Создать")

        await app.tap("Напоминания")
        assert "как в группе" in app.last_text
        await app.tap("Настроить своё")
        await app.tap("за 3 дня")
        await app.tap("Готово")
        assert "Напоминания: " in app.last_text
        assert "как в группе" not in app.last_text

    async def test_item_reminders_can_be_muted(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        await app.send("➕ Добавить")
        await app.tap("Задача")
        await app.tap("Матан")
        await app.tap("Лаба")
        await app.send("Лаба")
        await app.tap("Пропустить")
        await app.tap("Завтра")
        await app.tap("23:59")
        await app.tap("Пропустить")
        await app.tap("Создать")
        await app.tap("Напоминания")
        await app.tap("Выключить")
        assert "выключены" in app.last_text


class TestSubjects:
    async def test_add_and_delete_subject(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        assert "Матан" in str(app.last_markup)
        await app.send("📚 Предметы")
        await app.tap("🗑")
        assert "Матан" not in str(app.last_markup)

    async def test_duplicate_subject_rejected(self, app):
        await _bootstrap_group(app)
        await _add_subject(app, "Матан")
        await app.send("📚 Предметы")
        await app.tap("Добавить")
        await app.send("матан")
        assert "уже есть" in app.last_text


class TestInviteLinks:
    async def test_group_screen_shows_deep_link(self, app):
        await _bootstrap_group(app)
        await app.send("👥 Группа")
        assert "https://t.me/testbot?start=join-" in app.last_text

    async def test_created_group_message_has_link(self, app):
        await _bootstrap_group(app)
        assert any("?start=join-" in text for text in app.texts)

    async def test_start_with_payload_joins_group(self, app):
        await _bootstrap_group(app)
        await app.send("👥 Группа")
        code = app.last_text.split("?start=join-")[1].split()[0]

        other = _second_user()
        await other.send(f"/start join-{code}")
        assert any("Ты в группе" in text for text in other.texts)
        assert "Активная группа: <b>кабаньи дедлайны</b>" in other.last_text

    async def test_start_with_broken_payload_falls_back_to_menu(self, app):
        await app.send("/start join-ZZZZ-ZZZZ-ZZZZ")
        assert "не найдена" in app.last_text

    async def test_regenerated_code_gives_new_link(self, app):
        await _bootstrap_group(app)
        await app.send("👥 Группа")
        old_link = app.last_text.split("?start=join-")[1].split()[0]
        await app.tap("Настройки группы")
        await app.tap("Новый invite-код")
        assert "?start=join-" in app.last_text
        assert old_link not in app.last_text


class TestInlineMode:
    async def test_inline_without_group_returns_hint(self, app):
        await app.send("/start")
        await app.inline("")
        assert app.inline_results[0]["title"] == "Нет активной группы"

    async def test_inline_lists_upcoming_items(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.inline("")
        titles = [r["title"] for r in app.inline_results]
        assert any("Лабораторная 4" in title for title in titles)
        assert titles[0] == "📅 Все ближайшие дедлайны"

    async def test_inline_filters_by_query(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.inline("экзамен")
        assert app.inline_results[0]["title"] == "Ничего не нашлось"

    async def test_inline_query_matches_subject(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.inline("мат")
        titles = [r["title"] for r in app.inline_results]
        assert any("Лабораторная 4" in title for title in titles)


class TestChatBinding:
    async def test_bot_offers_binding_when_added(self, app):
        await app.chat_added()
        assert "привязать этот чат" in app.last_text


class TestChecklist:
    async def test_list_renders_checkboxes(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.send("📋 Задания")
        assert '<input type="checkbox">' in app.last_text
        assert "Лабораторная 4" in app.last_text

    async def test_toggle_from_list_checks_the_box(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.send("📋 Задания")
        await app.tap("☐")
        assert '<input type="checkbox" checked>' in app.last_text
        assert "<s>Лабораторная 4</s>" in app.last_text

    async def test_toggle_twice_returns_to_unchecked(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.send("📋 Задания")
        await app.tap("☐")
        await app.tap("✅")
        assert '<input type="checkbox">' in app.last_text
        assert "<s>" not in app.last_text

    async def test_card_shows_and_toggles_completion(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.send("📋 Задания")
        await app.tap("Лабораторная 4")
        assert "✅ Выполнено" in [
            b["text"] if isinstance(b, dict) else b.text for b in app.buttons()
        ]
        await app.tap("✅ Выполнено")
        assert "✅ <b>Лабораторная 4</b>" in app.last_text
        labels = [b["text"] if isinstance(b, dict) else b.text for b in app.buttons()]
        assert "↩️ Вернуть в работу" in labels

    async def test_completion_is_personal(self, app):
        await _seed_one_task(app, "Лабораторная 4")
        await app.send("👥 Группа")
        code = app.last_text.split("?start=join-")[1].split()[0]
        await app.send("📋 Задания")
        await app.tap("☐")
        assert '<input type="checkbox" checked>' in app.last_text

        other = _second_user()
        await other.send(f"/start join-{code}")
        await other.send("📋 Задания")
        assert '<input type="checkbox">' in other.last_text
        assert "<s>" not in other.last_text

    async def test_empty_list_stays_plain(self, app):
        await _bootstrap_group(app)
        await app.send("📋 Задания")
        assert "Пока пусто" in app.last_text
