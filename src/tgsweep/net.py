"""Telegram helpers for every tgsweep caller: FloodWait retries, session loss, discussion groups."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from telethon import errors, functions
from telethon.errors import FloodWaitError

T = TypeVar("T")
Sleep = Callable[[float], Awaitable[None]]

# The user session is gone: never treat these as a per-message failure.
SESSION_LOST_ERRORS = (
    errors.AuthKeyDuplicatedError,
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)


async def flood_retry(factory: Callable[[], Awaitable[T]], *, sleep: Sleep = asyncio.sleep, max_retries: int = 5) -> T:
    """Run a request, sleeping through FloodWaitError up to max_retries times."""
    attempt = 0
    while True:
        try:
            return await factory()
        except FloodWaitError as exc:
            if attempt >= max_retries:
                raise
            attempt += 1
            await sleep(exc.seconds + 1)


async def linked_chat(client, entity, *, sleep: Sleep = asyncio.sleep):
    """Return (full channel result, linked discussion chat entity or None).

    FloodWait is waited out (and re-raised after retries), so comments are never skipped silently.
    """
    try:
        full = await flood_retry(lambda: client(functions.channels.GetFullChannelRequest(channel=entity)), sleep=sleep)
    except errors.FloodWaitError:
        raise
    except errors.RPCError:
        return None, None
    linked_id = getattr(full.full_chat, "linked_chat_id", None)
    if not linked_id:
        return full, None
    linked = next((c for c in full.chats if getattr(c, "id", None) == linked_id), None)
    return full, linked
