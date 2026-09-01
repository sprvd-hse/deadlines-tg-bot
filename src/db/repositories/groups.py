from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import models as orm
from src.domain.enums import MemberRole
from src.domain.models import Group, GroupReminderSettings, Member, User


def to_domain(row: orm.Group) -> Group:
    """Преобразует ORM-группу в доменную модель вместе с настройками напоминаний."""
    return Group(
        id=row.id,
        name=row.name,
        invite_code=row.invite_code,
        timezone=row.timezone,
        digest_day=row.digest_day,
        digest_hour=row.digest_hour,
        chat_id=row.chat_id,
        digest_pinned_message_id=row.digest_pinned_message_id,
        digest_last_sent_at=row.digest_last_sent_at,
        reminders=GroupReminderSettings(
            task_offsets=list(row.default_task_offsets),
            event_offsets=list(row.default_event_offsets),
            reminder_hour=row.reminder_hour,
        ),
        created_by_user_id=row.created_by_user_id,
    )


class GroupRepository:
    """Доступ к группам, участникам и настройкам группы."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, group_id: int) -> Group | None:
        """Возвращает группу по id."""
        row = await self._session.get(orm.Group, group_id)
        return to_domain(row) if row else None

    async def get_by_invite_code(self, code: str) -> Group | None:
        """Возвращает группу по invite-коду."""
        row = (
            await self._session.execute(select(orm.Group).where(orm.Group.invite_code == code))
        ).scalar_one_or_none()
        return to_domain(row) if row else None

    async def get_by_chat_id(self, chat_id: int) -> Group | None:
        """Возвращает группу, привязанную к чату."""
        row = (
            await self._session.execute(select(orm.Group).where(orm.Group.chat_id == chat_id))
        ).scalar_one_or_none()
        return to_domain(row) if row else None

    async def list_all(self) -> list[Group]:
        """Возвращает все группы (для планировщика дайджестов)."""
        rows = await self._session.execute(select(orm.Group))
        return [to_domain(r) for r in rows.scalars()]

    async def invite_code_exists(self, code: str) -> bool:
        """Занят ли invite-код."""
        found = (
            await self._session.execute(select(orm.Group.id).where(orm.Group.invite_code == code))
        ).scalar_one_or_none()
        return found is not None

    async def create(self, *, name: str, invite_code: str, timezone: str, creator_id: int) -> Group:
        """Создаёт группу и добавляет создателя участником."""
        row = orm.Group(
            name=name,
            invite_code=invite_code,
            timezone=timezone,
            created_by_user_id=creator_id,
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add(
            orm.GroupMember(group_id=row.id, user_id=creator_id, role=MemberRole.CREATOR)
        )
        await self._session.flush()
        return to_domain(row)

    async def update_settings(self, group_id: int, **values) -> None:
        """Обновляет поля группы."""
        if values:
            await self._session.execute(
                update(orm.Group).where(orm.Group.id == group_id).values(**values)
            )

    async def list_members(self, group_id: int) -> list[Member]:
        """Участники группы: создатель первым, дальше по имени."""
        rows = (
            await self._session.execute(
                select(orm.User, orm.GroupMember)
                .join(orm.GroupMember, orm.GroupMember.user_id == orm.User.id)
                .where(orm.GroupMember.group_id == group_id)
                .order_by(orm.GroupMember.role, orm.User.full_name)
            )
        ).all()
        return [
            Member(
                user=User.model_validate(user),
                group_id=member.group_id,
                role=member.role,
                personal_reminders_enabled=member.personal_reminders_enabled,
            )
            for user, member in rows
        ]

    async def get_member(self, *, group_id: int, user_id: int) -> Member | None:
        """Возвращает участие пользователя в группе."""
        row = (
            await self._session.execute(
                select(orm.User, orm.GroupMember)
                .join(orm.GroupMember, orm.GroupMember.user_id == orm.User.id)
                .where(orm.GroupMember.group_id == group_id, orm.GroupMember.user_id == user_id)
            )
        ).first()
        if row is None:
            return None
        user, member = row
        return Member(
            user=User.model_validate(user),
            group_id=member.group_id,
            role=member.role,
            personal_reminders_enabled=member.personal_reminders_enabled,
        )

    async def list_user_groups(self, user_id: int) -> list[Member]:
        """Группы пользователя вместе с его ролью в каждой."""
        rows = (
            await self._session.execute(
                select(orm.GroupMember)
                .join(orm.Group, orm.Group.id == orm.GroupMember.group_id)
                .where(orm.GroupMember.user_id == user_id)
                .order_by(orm.Group.name)
            )
        ).scalars()
        user = await self._session.get(orm.User, user_id)
        assert user is not None
        domain_user = User.model_validate(user)
        return [
            Member(
                user=domain_user,
                group_id=m.group_id,
                role=m.role,
                personal_reminders_enabled=m.personal_reminders_enabled,
            )
            for m in rows
        ]

    async def add_member(self, *, group_id: int, user_id: int) -> None:
        """Добавляет участника в группу."""
        self._session.add(
            orm.GroupMember(group_id=group_id, user_id=user_id, role=MemberRole.MEMBER)
        )
        await self._session.flush()

    async def remove_member(self, *, group_id: int, user_id: int) -> None:
        """Убирает участника из группы."""
        await self._session.execute(
            delete(orm.GroupMember).where(
                orm.GroupMember.group_id == group_id, orm.GroupMember.user_id == user_id
            )
        )

    async def count_members(self, group_id: int) -> int:
        """Число участников группы."""
        rows = await self._session.execute(
            select(orm.GroupMember.id).where(orm.GroupMember.group_id == group_id)
        )
        return len(rows.all())

    async def set_role(self, *, group_id: int, user_id: int, role: MemberRole) -> None:
        """Меняет роль участника."""
        await self._session.execute(
            update(orm.GroupMember)
            .where(orm.GroupMember.group_id == group_id, orm.GroupMember.user_id == user_id)
            .values(role=role)
        )

    async def set_personal_reminders(self, *, group_id: int, user_id: int, enabled: bool) -> None:
        """Включает или выключает личные напоминания участника."""
        await self._session.execute(
            update(orm.GroupMember)
            .where(orm.GroupMember.group_id == group_id, orm.GroupMember.user_id == user_id)
            .values(personal_reminders_enabled=enabled)
        )

    async def delete_group(self, group_id: int) -> None:
        """Удаляет группу целиком.

        Порядок обязателен: Item.subject_id имеет ondelete=RESTRICT, поэтому записи
        нужно удалить раньше предметов, а active_group_id участников — сбросить до
        удаления самой группы.
        """
        item_ids = list(
            (
                await self._session.execute(
                    select(orm.Item.id).where(orm.Item.group_id == group_id)
                )
            ).scalars()
        )
        if item_ids:
            await self._session.execute(
                delete(orm.ReminderLog).where(orm.ReminderLog.item_id.in_(item_ids))
            )
            await self._session.execute(
                delete(orm.ItemCompletion).where(orm.ItemCompletion.item_id.in_(item_ids))
            )
        await self._session.execute(delete(orm.Item).where(orm.Item.group_id == group_id))
        await self._session.execute(delete(orm.Subject).where(orm.Subject.group_id == group_id))
        await self._session.execute(
            delete(orm.GroupMember).where(orm.GroupMember.group_id == group_id)
        )
        await self._session.execute(
            update(orm.User)
            .where(orm.User.active_group_id == group_id)
            .values(active_group_id=None)
        )
        await self._session.execute(delete(orm.Group).where(orm.Group.id == group_id))
