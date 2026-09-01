import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.repositories.groups import GroupRepository
from src.db.repositories.items import ItemRepository
from src.db.repositories.subjects import SubjectRepository
from src.db.repositories.users import UserRepository
from src.domain.enums import MemberRole
from src.domain.models import Group, Member, Subject, User
from src.utils import generate_invite_code

logger = logging.getLogger(__name__)

JoinStatus = Literal["ok", "not_found", "already_member", "rate_limited"]
TransferStatus = Literal["ok", "not_member", "same_user"]
LeaveStatus = Literal["left", "group_deleted", "need_transfer", "not_member"]
SubjectStatus = Literal["ok", "duplicate", "not_found", "has_items", "needs_confirm"]

_INVITE_RETRIES = 5
_JOIN_RATE_WINDOW = 60.0
_JOIN_RATE_MAX = 5
_join_attempts: dict[int, list[float]] = {}


def check_join_rate_limit(tg_user_id: int) -> bool:
    """Пропускает не больше пяти попыток входа по коду в минуту на пользователя."""
    now = time.monotonic()
    window = _join_attempts.setdefault(tg_user_id, [])
    window[:] = [t for t in window if now - t < _JOIN_RATE_WINDOW]
    if len(window) >= _JOIN_RATE_MAX:
        return False
    window.append(now)
    if len(_join_attempts) > 1024:
        for uid, entries in list(_join_attempts.items()):
            if uid != tg_user_id and (not entries or now - entries[-1] >= _JOIN_RATE_WINDOW):
                _join_attempts.pop(uid, None)
    return True


@dataclass(frozen=True, slots=True)
class GroupContext:
    """Пользователь вместе с его активной группой и участием в ней."""

    user: User
    group: Group | None
    member: Member | None

    @property
    def has_group(self) -> bool:
        """Есть ли у пользователя активная группа."""
        return self.group is not None

    @property
    def is_creator(self) -> bool:
        """Является ли пользователь создателем активной группы."""
        return self.member is not None and self.member.is_creator

    def activated(self) -> "ActiveContext | None":
        """Возвращает контекст с гарантированно непустой группой."""
        if self.group is None or self.member is None:
            return None
        return ActiveContext(user=self.user, group=self.group, member=self.member)


@dataclass(frozen=True, slots=True)
class ActiveContext:
    """Контекст пользователя, у которого точно есть активная группа."""

    user: User
    group: Group
    member: Member

    @property
    def is_creator(self) -> bool:
        """Является ли пользователь создателем активной группы."""
        return self.member.is_creator


class GroupManager:
    """Жизненный цикл групп, участников и предметов."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._groups = GroupRepository(session)
        self._users = UserRepository(session)
        self._subjects = SubjectRepository(session)
        self._items = ItemRepository(session)

    async def context(
        self, *, tg_user_id: int, tg_username: str | None, full_name: str
    ) -> GroupContext:
        """Загружает пользователя (создавая при необходимости) и его активную группу."""
        user = await self._users.get_or_create(
            tg_user_id=tg_user_id, tg_username=tg_username, full_name=full_name
        )
        if user.active_group_id is None:
            return GroupContext(user=user, group=None, member=None)
        group = await self._groups.get(user.active_group_id)
        if group is None:
            await self._users.set_active_group(user_id=user.id, group_id=None)
            return GroupContext(user=user, group=None, member=None)
        member = await self._groups.get_member(group_id=group.id, user_id=user.id)
        if member is None:
            await self._users.set_active_group(user_id=user.id, group_id=None)
            return GroupContext(user=user, group=None, member=None)
        return GroupContext(user=user, group=group, member=member)

    async def get_group(self, group_id: int) -> Group | None:
        """Возвращает группу по id."""
        return await self._groups.get(group_id)

    async def group_by_chat(self, chat_id: int) -> Group | None:
        """Возвращает группу, привязанную к чату."""
        return await self._groups.get_by_chat_id(chat_id)

    async def create_group(self, *, creator: User, name: str, timezone: str | None = None) -> Group:
        """Создаёт группу и делает её активной у создателя."""
        code = await self._unique_invite_code()
        group = await self._groups.create(
            name=name.strip(),
            invite_code=code,
            timezone=timezone or settings.default_timezone,
            creator_id=creator.id,
        )
        await self._users.set_active_group(user_id=creator.id, group_id=group.id)
        return group

    async def join_group(self, *, user: User, invite_code: str) -> tuple[Group | None, JoinStatus]:
        """Присоединяет пользователя к группе по коду и делает её активной."""
        group = await self._groups.get_by_invite_code(invite_code)
        if group is None:
            return None, "not_found"
        existing = await self._groups.get_member(group_id=group.id, user_id=user.id)
        await self._users.set_active_group(user_id=user.id, group_id=group.id)
        if existing is not None:
            return group, "already_member"
        await self._groups.add_member(group_id=group.id, user_id=user.id)
        return group, "ok"

    async def user_groups(self, user_id: int) -> list[tuple[Group, MemberRole]]:
        """Группы пользователя вместе с ролью в каждой."""
        result: list[tuple[Group, MemberRole]] = []
        for member in await self._groups.list_user_groups(user_id):
            group = await self._groups.get(member.group_id)
            if group is not None:
                result.append((group, member.role))
        return result

    async def switch_active(self, *, user: User, group_id: int) -> Group | None:
        """Делает группу активной, если пользователь в ней состоит."""
        member = await self._groups.get_member(group_id=group_id, user_id=user.id)
        if member is None:
            return None
        await self._users.set_active_group(user_id=user.id, group_id=group_id)
        return await self._groups.get(group_id)

    async def members(self, group_id: int) -> list[Member]:
        """Участники группы."""
        return await self._groups.list_members(group_id)

    async def update_settings(self, group_id: int, **values) -> Group | None:
        """Обновляет настройки группы и возвращает свежую версию."""
        await self._groups.update_settings(group_id, **values)
        return await self._groups.get(group_id)

    async def regenerate_invite_code(self, group_id: int) -> str:
        """Выдаёт группе новый invite-код."""
        code = await self._unique_invite_code()
        await self._groups.update_settings(group_id, invite_code=code)
        return code

    async def set_personal_reminders(self, *, group_id: int, user_id: int, enabled: bool) -> None:
        """Включает или выключает личные напоминания участника."""
        await self._groups.set_personal_reminders(
            group_id=group_id, user_id=user_id, enabled=enabled
        )

    async def transfer_ownership(
        self, *, group_id: int, current_creator_id: int, new_creator_id: int
    ) -> TransferStatus:
        """Передаёт роль создателя другому участнику."""
        if current_creator_id == new_creator_id:
            return "same_user"
        if await self._groups.get_member(group_id=group_id, user_id=new_creator_id) is None:
            return "not_member"
        await self._groups.set_role(
            group_id=group_id, user_id=current_creator_id, role=MemberRole.MEMBER
        )
        await self._groups.set_role(
            group_id=group_id, user_id=new_creator_id, role=MemberRole.CREATOR
        )
        return "ok"

    async def leave_group(self, *, group_id: int, user_id: int) -> LeaveStatus:
        """Выводит участника из группы; последнего участника — вместе с группой."""
        member = await self._groups.get_member(group_id=group_id, user_id=user_id)
        if member is None:
            return "not_member"
        total = await self._groups.count_members(group_id)
        if total == 1:
            await self._groups.delete_group(group_id)
            return "group_deleted"
        if member.is_creator:
            return "need_transfer"
        await self._groups.remove_member(group_id=group_id, user_id=user_id)
        await self._users.set_active_group(user_id=user_id, group_id=None)
        return "left"

    async def delete_group(self, group_id: int) -> None:
        """Удаляет группу со всем содержимым."""
        await self._groups.delete_group(group_id)

    async def subjects(self, group_id: int) -> list[Subject]:
        """Предметы группы."""
        return await self._subjects.list_for_group(group_id)

    async def add_subject(
        self, *, group_id: int, name: str
    ) -> tuple[Subject | None, SubjectStatus]:
        """Создаёт предмет, если такого имени ещё нет."""
        if await self._subjects.find_by_name(group_id=group_id, name=name) is not None:
            return None, "duplicate"
        return await self._subjects.create(group_id=group_id, name=name), "ok"

    async def delete_subject(
        self, subject_id: int, *, now: datetime | None = None, confirmed: bool = False
    ) -> tuple[SubjectStatus, int]:
        """Удаляет предмет и возвращает статус вместе с числом прошедших записей.

        Предмет с будущими дедлайнами не удаляется вовсе. Если остались только
        прошедшие записи, первый вызов просит подтверждения, а с `confirmed`
        удаляет их вместе с предметом. Скрытые записи вычищаются всегда: они
        невидимы пользователю, но блокировали бы удаление по внешнему ключу.
        """
        moment = now or datetime.now(tz=UTC)
        subject = await self._subjects.get(subject_id)
        if subject is None:
            return "not_found", 0
        if await self._subjects.has_upcoming_items(subject_id, now=moment):
            return "has_items", 0
        passed = await self._subjects.count_passed_items(subject_id, now=moment)
        if passed and not confirmed:
            return "needs_confirm", passed
        purged = await self._items.purge_for_subject(subject_id, archived_only=not passed)
        if purged:
            logger.info("Предмет %s: удалено %s записей", subject_id, purged)
        await self._subjects.delete(subject_id)
        return "ok", passed

    async def _unique_invite_code(self) -> str:
        for _ in range(_INVITE_RETRIES):
            code = generate_invite_code()
            if not await self._groups.invite_code_exists(code):
                return code
        raise RuntimeError("Не удалось сгенерировать уникальный invite-код")
