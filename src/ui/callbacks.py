from aiogram.filters.callback_data import CallbackData


class Nav(CallbackData, prefix="nav"):
    """Переход на экран меню."""

    to: str


class ItemAction(CallbackData, prefix="item"):
    """Действие над записью."""

    action: str
    item_id: int


class AddStep(CallbackData, prefix="add"):
    """Шаг мастера добавления записи."""

    step: str
    value: str = ""


class EditStep(CallbackData, prefix="edit"):
    """Шаг редактирования записи."""

    field: str
    item_id: int
    value: str = ""


class CalendarNav(CallbackData, prefix="cal"):
    """Навигация и выбор в инлайн-календаре."""

    action: str
    year: int = 0
    month: int = 0
    day: int = 0


class TimePick(CallbackData, prefix="time"):
    """Выбор времени дедлайна."""

    action: str
    hour: int = 0
    minute: int = 0


class OffsetToggle(CallbackData, prefix="off"):
    """Редактор набора сроков напоминаний.

    scope: draft | item | group_task | group_event
    """

    scope: str
    target_id: int
    action: str
    minutes: int = 0


class GroupAction(CallbackData, prefix="grp"):
    """Действия с группами пользователя."""

    action: str
    group_id: int = 0


class AdminAction(CallbackData, prefix="adm"):
    """Действия создателя группы."""

    action: str
    value: str = ""


class SubjectAction(CallbackData, prefix="subj"):
    """Действия с предметами."""

    action: str
    subject_id: int = 0


class MemberAction(CallbackData, prefix="mem"):
    """Действия над участником группы."""

    action: str
    user_id: int


NOOP = "noop"
