from aiogram.fsm.state import State, StatesGroup


class AddItem(StatesGroup):
    """Мастер добавления задачи или важной даты."""

    kind = State()
    subject = State()
    new_subject = State()
    category = State()
    title = State()
    description = State()
    deadline = State()
    custom_time = State()
    links = State()
    confirm = State()
    reminders = State()


class EditItem(StatesGroup):
    """Изменение отдельного поля записи."""

    field = State()
    title = State()
    description = State()
    links = State()
    deadline = State()
    custom_time = State()


class GroupOnboarding(StatesGroup):
    """Создание группы или вход по коду."""

    name = State()
    code = State()


class SubjectInput(StatesGroup):
    """Ввод названия предмета."""

    name = State()


class AdminInput(StatesGroup):
    """Ввод настроек группы."""

    timezone = State()
