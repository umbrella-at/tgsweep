"""Hardcoded chat rules. Nothing here is configurable on purpose."""

from __future__ import annotations

from telethon import utils
from telethon.tl import types

SERVICE_NOTIFICATIONS_ID = 777000


class ForbiddenChatError(Exception):
    """The chat is off-limits by a hardcoded rule."""


def peer_id(entity) -> int:
    return utils.get_peer_id(entity)


def is_saved_messages(entity, me_id: int) -> bool:
    if isinstance(entity, (types.InputPeerSelf, types.InputUserSelf)):
        return True
    return isinstance(entity, types.User) and (bool(entity.is_self) or entity.id == me_id)


def is_service_chat(entity) -> bool:
    if isinstance(entity, types.User):
        return entity.id == SERVICE_NOTIFICATIONS_ID
    if isinstance(entity, (types.PeerUser, types.InputPeerUser)):
        return entity.user_id == SERVICE_NOTIFICATIONS_ID
    return False


def is_secret_chat(entity) -> bool:
    return type(entity).__name__.startswith(("Encrypted", "InputEncrypted"))


# The only chats ever opened, for reading or for deleting: groups and channels.
# Everything else is private: people, bots (BotFather holds bot tokens), channel direct
# messages (monoforum), secret chats, Saved Messages, 777000 and anything unknown.
OPEN_KINDS = frozenset({"group", "megagroup", "broadcast"})


def assert_readable(entity, me_id: int) -> None:
    """Only groups and channels pass. The single gate for every read path and for deletion."""
    if is_saved_messages(entity, me_id):
        raise ForbiddenChatError("Saved Messages are never opened")
    if is_service_chat(entity):
        raise ForbiddenChatError("Telegram service notifications (777000) are never opened")
    if is_secret_chat(entity):
        raise ForbiddenChatError("Secret chats are never opened")
    if chat_kind(entity) not in OPEN_KINDS:
        raise ForbiddenChatError("Private chats (people, bots, channel direct messages) are never opened")


def chat_kind(entity) -> str:
    """One of: dm, bot, group, megagroup, broadcast, monoforum, other.

    A monoforum holds the direct messages between subscribers and a channel:
    it is a private conversation even though it is technically a supergroup.
    """
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "dm"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        if getattr(entity, "monoforum", False):
            return "monoforum"
        if entity.broadcast:
            return "broadcast"
        return "megagroup"
    return "other"


def is_creator(entity) -> bool:
    return bool(getattr(entity, "creator", False))


def is_admin(entity) -> bool:
    return is_creator(entity) or getattr(entity, "admin_rights", None) is not None


def display_title(entity) -> str:
    return utils.get_display_name(entity) or str(getattr(entity, "id", "?"))
