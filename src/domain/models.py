from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.enums import ItemKind, MemberRole, categories_for

DEFAULT_TASK_OFFSETS: list[int] = [1440]
DEFAULT_EVENT_OFFSETS: list[int] = [10080, 4320, 1440]
DEFAULT_REMINDER_HOUR = 10

TITLE_MAX = 256
DESCRIPTION_MAX = 2048


class DomainModel(BaseModel):
    """Базовая модель домена: читается из ORM, неизменяема."""

    model_config = ConfigDict(from_attributes=True, frozen=True)


class Subject(DomainModel):
    """Учебный предмет группы."""

    id: int
    group_id: int
    name: str


class GroupReminderSettings(DomainModel):
    """Настройки напоминаний группы: дефолтные офсеты и час рассылки."""

    task_offsets: list[int] = Field(default_factory=lambda: list(DEFAULT_TASK_OFFSETS))
    event_offsets: list[int] = Field(default_factory=lambda: list(DEFAULT_EVENT_OFFSETS))
    reminder_hour: int = Field(default=DEFAULT_REMINDER_HOUR, ge=0, le=23)

    def defaults_for(self, kind: ItemKind) -> list[int]:
        """Возвращает дефолтный набор офсетов (в минутах) для данного типа записи."""
        return self.task_offsets if kind is ItemKind.TASK else self.event_offsets


class Group(DomainModel):
    """Учебная группа."""

    id: int
    name: str
    invite_code: str
    timezone: str
    digest_day: int = Field(ge=0, le=6)
    digest_hour: int = Field(ge=0, le=23)
    chat_id: int | None = None
    digest_last_sent_at: datetime | None = None
    reminders: GroupReminderSettings = Field(default_factory=GroupReminderSettings)
    created_by_user_id: int

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"неизвестный часовой пояс: {value}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        """Часовой пояс группы."""
        return ZoneInfo(self.timezone)


class User(DomainModel):
    """Пользователь бота."""

    id: int
    tg_user_id: int
    tg_username: str | None = None
    full_name: str
    active_group_id: int | None = None


class Member(DomainModel):
    """Участник группы вместе с профилем пользователя."""

    user: User
    group_id: int
    role: MemberRole
    personal_reminders_enabled: bool = True

    @property
    def is_creator(self) -> bool:
        """Является ли участник создателем группы."""
        return self.role is MemberRole.CREATOR


class Item(DomainModel):
    """Задание или важная дата группы."""

    id: int
    group_id: int
    subject_id: int
    short_id: int
    kind: ItemKind
    category: str
    title: str = Field(max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    links: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    reminder_offsets: list[int] | None = None
    reminders_muted: bool = False
    created_by_user_id: int
    created_at: datetime
    is_active: bool = True

    @model_validator(mode="after")
    def _check_consistency(self) -> "Item":
        if self.category not in categories_for(self.kind):
            raise ValueError(f"категория {self.category} недопустима для типа {self.kind}")
        if self.kind is ItemKind.EVENT and self.due_at is None:
            raise ValueError("у важной даты обязательно должна быть дата")
        return self

    @property
    def is_open(self) -> bool:
        """Задача без дедлайна («до конца модуля»)."""
        return self.due_at is None


class ItemDraft(BaseModel):
    """Черновик записи, собираемый в ходе диалога добавления."""

    kind: ItemKind
    subject_id: int
    category: str
    title: str = Field(max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    links: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    reminder_offsets: list[int] | None = None
    reminders_muted: bool = False

    @model_validator(mode="after")
    def _check_consistency(self) -> "ItemDraft":
        if self.category not in categories_for(self.kind):
            raise ValueError(f"категория {self.category} недопустима для типа {self.kind}")
        if self.kind is ItemKind.EVENT and self.due_at is None:
            raise ValueError("у важной даты обязательно должна быть дата")
        return self


class ItemPatch(BaseModel):
    """Набор изменений записи; None означает «не трогать»."""

    subject_id: int | None = None
    category: str | None = None
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    clear_description: bool = False
    links: list[str] | None = None
    due_at: datetime | None = None
    clear_due_at: bool = False
    reminder_offsets: list[int] | None = None
    reset_reminder_offsets: bool = False
    reminders_muted: bool | None = None
