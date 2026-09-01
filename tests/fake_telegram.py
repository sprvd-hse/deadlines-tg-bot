import datetime as dt
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update, User

CHAT_ID = 500
USER_ID = 777


class FakeSession(BaseSession):
    """Сессия-заглушка: не ходит в сеть, запоминает вызовы API."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._message_id = 1000

    async def close(self) -> None:
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - не используется
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        name = type(method).__name__
        payload = method.model_dump(exclude_none=True)
        self.calls.append((name, payload))
        if name in {"SendMessage", "EditMessageText"}:
            self._message_id += 1
            return Message(
                message_id=self._message_id,
                date=dt.datetime.now(tz=dt.UTC),
                chat=Chat(id=payload.get("chat_id", CHAT_ID), type="private"),
                text=payload.get("text", ""),
            )
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="bot", username="testbot")
        return True

    @property
    def texts(self) -> list[str]:
        """Тексты всех отправленных и отредактированных сообщений."""
        return [
            payload.get("text", "")
            for name, payload in self.calls
            if name in {"SendMessage", "EditMessageText"}
        ]

    @property
    def last_text(self) -> str:
        """Текст последнего показанного экрана."""
        return self.texts[-1] if self.texts else ""

    @property
    def last_markup(self) -> Any:
        """Клавиатура последнего экрана."""
        for name, payload in reversed(self.calls):
            if name in {"SendMessage", "EditMessageText"} and payload.get("reply_markup"):
                return payload["reply_markup"]
        return None

    def buttons(self) -> list[Any]:
        """Плоский список инлайн-кнопок последнего экрана."""
        markup = self.last_markup
        if markup is None:
            return []
        rows = markup["inline_keyboard"] if isinstance(markup, dict) else markup.inline_keyboard
        return [button for row in rows for button in row]

    def find_button(self, text_fragment: str):
        """Ищет кнопку по фрагменту подписи."""
        for button in self.buttons():
            label = button["text"] if isinstance(button, dict) else button.text
            if text_fragment in label:
                return button
        raise AssertionError(
            f"Кнопка «{text_fragment}» не найдена среди: "
            f"{[b['text'] if isinstance(b, dict) else b.text for b in self.buttons()]}"
        )

    @staticmethod
    def data_of(button) -> str:
        """callback_data кнопки."""
        return button["callback_data"] if isinstance(button, dict) else button.callback_data

    def clear(self) -> None:
        """Сбрасывает историю вызовов."""
        self.calls.clear()


def make_bot() -> tuple[Bot, FakeSession]:
    """Бот поверх сессии-заглушки."""
    session = FakeSession()
    return Bot(token="42:testtoken", session=session), session


def message_update(text: str, *, update_id: int = 1) -> Update:
    """Апдейт с текстовым сообщением от пользователя."""
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=dt.datetime.now(tz=dt.UTC),
            chat=Chat(id=CHAT_ID, type="private"),
            from_user=User(id=USER_ID, is_bot=False, first_name="Аня", username="anya"),
            text=text,
        ),
    )


def callback_update(data: str, *, update_id: int = 1) -> Update:
    """Апдейт с нажатием инлайн-кнопки."""
    from aiogram.types import CallbackQuery

    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=USER_ID, is_bot=False, first_name="Аня", username="anya"),
            chat_instance="1",
            data=data,
            message=Message(
                message_id=900,
                date=dt.datetime.now(tz=dt.UTC),
                chat=Chat(id=CHAT_ID, type="private"),
                text="экран",
            ),
        ),
    )
