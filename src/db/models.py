from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import settings
from src.domain.enums import ItemKind, MemberRole
from src.domain.models import (
    DEFAULT_EVENT_OFFSETS,
    DEFAULT_REMINDER_HOUR,
    DEFAULT_TASK_OFFSETS,
    DESCRIPTION_MAX,
    TITLE_MAX,
)


class Base(DeclarativeBase):
    pass


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    invite_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=settings.default_timezone
    )
    digest_day: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    digest_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=19)
    digest_last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    default_task_offsets: Mapped[list[int]] = mapped_column(
        JSON, nullable=False, default=lambda: list(DEFAULT_TASK_OFFSETS)
    )
    default_event_offsets: Mapped[list[int]] = mapped_column(
        JSON, nullable=False, default=lambda: list(DEFAULT_EVENT_OFFSETS)
    )
    reminder_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_REMINDER_HOUR
    )
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    active_group_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "groups.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_users_active_group_id_groups",
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole, native_enum=False, length=16), nullable=False, default=MemberRole.MEMBER
    )
    personal_reminders_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("group_id", "name", name="uq_subjects_group_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("group_id", "short_id", name="uq_items_group_short_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    short_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[ItemKind] = mapped_column(
        Enum(ItemKind, native_enum=False, length=16), nullable=False
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(TITLE_MAX), nullable=False)
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION_MAX))
    links: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reminder_offsets: Mapped[list[int] | None] = mapped_column(JSON)
    reminders_muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class ReminderLog(Base):
    __tablename__ = "reminder_log"
    __table_args__ = (
        UniqueConstraint("item_id", "user_id", "offset_minutes", name="uq_reminder_log_entry"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
