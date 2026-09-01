from enum import StrEnum


class ItemKind(StrEnum):
    """Тип записи: задача (нужно сделать) или важная дата (событие)."""

    TASK = "task"
    EVENT = "event"


class TaskCategory(StrEnum):
    """Категория задачи."""

    HOMEWORK = "homework"
    LAB = "lab"
    PROJECT = "project"
    ESSAY = "essay"
    OTHER = "other"


class EventCategory(StrEnum):
    """Категория важной даты."""

    TEST = "test"
    CONTROL = "control"
    EXAM = "exam"
    DEFENSE = "defense"
    OTHER = "other"


class MemberRole(StrEnum):
    """Роль участника в группе."""

    CREATOR = "creator"
    MEMBER = "member"


TASK_CATEGORY_LABELS: dict[TaskCategory, str] = {
    TaskCategory.HOMEWORK: "Домашка",
    TaskCategory.LAB: "Лаба",
    TaskCategory.PROJECT: "Проект",
    TaskCategory.ESSAY: "Реферат",
    TaskCategory.OTHER: "Другое",
}

EVENT_CATEGORY_LABELS: dict[EventCategory, str] = {
    EventCategory.TEST: "Зачёт",
    EventCategory.CONTROL: "Контрольная",
    EventCategory.EXAM: "Экзамен",
    EventCategory.DEFENSE: "Защита",
    EventCategory.OTHER: "Другое",
}

KIND_LABELS: dict[ItemKind, str] = {
    ItemKind.TASK: "Задача",
    ItemKind.EVENT: "Важная дата",
}

KIND_ICONS: dict[ItemKind, str] = {
    ItemKind.TASK: "📝",
    ItemKind.EVENT: "📅",
}


def categories_for(kind: ItemKind) -> list[str]:
    """Возвращает список значений категорий, допустимых для данного типа записи."""
    if kind is ItemKind.TASK:
        return [c.value for c in TaskCategory]
    return [c.value for c in EventCategory]


def category_label(kind: ItemKind, category: str) -> str:
    """Возвращает человекочитаемую метку категории."""
    if kind is ItemKind.TASK:
        return TASK_CATEGORY_LABELS[TaskCategory(category)]
    return EVENT_CATEGORY_LABELS[EventCategory(category)]
