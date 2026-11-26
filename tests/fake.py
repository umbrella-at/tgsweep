"""In-memory stand-in for a Telethon user client.

It mirrors the Telegram behaviour that matters for safety: in private chats and
basic groups message ids are global for the account, DeleteMessages for them
carries no peer, and admins can delete other people's messages. Telethon's
get_messages(entity, ids) hides messages of other chats for non-channels; that
can be switched off with validate_peer=False to test tgsweep's own check.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telethon import errors, utils
from telethon.tl import functions, types

ME = 1000
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def flood(seconds: int) -> errors.FloodWaitError:
    return errors.FloodWaitError(request=None, capture=seconds)


@dataclass
class FakeChat:
    entity: object
    messages: dict[int, types.Message] = field(default_factory=dict)
    admins: list[types.User] = field(default_factory=list)
    linked: object | None = None

    @property
    def is_channel(self) -> bool:
        return isinstance(self.entity, types.Channel)


class FakeClient:
    def __init__(self, me_id: int = ME, *, validate_peer: bool = True):
        self.me = types.User(id=me_id, is_self=True, first_name="Me", access_hash=1, bot=False, username="me_user")
        self.validate_peer = validate_peer
        self.chats: dict[int, FakeChat] = {}
        self.dialog_ids: list[int] = []
        self.users: dict[int, types.User] = {me_id: self.me}
        self._global_ids = itertools.count(1)
        self._channel_ids: dict[int, itertools.count] = {}
        self.flood_once: dict[str, int] = {}
        self.flood_after: dict[str, tuple[int, int]] = {}
        self.fail_after: dict[str, int] = {}
        self.delete_errors: dict[int, Exception] = {}
        self.request_errors: dict[type, Exception] = {}
        self.calls: list[tuple[str, dict]] = []
        self.delete_calls: list[tuple[int, list[int]]] = []
        self.requests: list[object] = []
        self.authorizations: list[types.Authorization] = []
        self.logged_out = False
        self.connected = False
        self.authorized = True
        # Chats this session has no access hash for yet: only their public username resolves them.
        self.uncached_ids: set[int] = set()
        self.omit_ids: set[int] = set()  # ids Telegram silently drops from channels.GetMessages
        # Saved Messages and the service chat are always in the dialog list.
        self.saved = self.me
        self.service_user = types.User(id=777000, first_name="Telegram", access_hash=2, verified=True, support=True)
        self.users[777000] = self.service_user
        self._register(self.me, dialog=True)
        self._register(self.service_user, dialog=True)

    # --- building the world ----------------------------------------------

    def _register(self, entity, *, dialog: bool) -> FakeChat:
        pid = utils.get_peer_id(entity)
        chat = FakeChat(entity)
        self.chats[pid] = chat
        if dialog:
            self.dialog_ids.append(pid)
        if isinstance(entity, types.Channel):
            self._channel_ids[pid] = itertools.count(1)
        return chat

    def add_user(self, user_id: int, name: str = "User", *, bot: bool = False, dialog: bool = True) -> types.User:
        user = types.User(id=user_id, first_name=name, access_hash=user_id * 7, bot=bot)
        self.users[user_id] = user
        if dialog:
            self._register(user, dialog=True)
        return user

    def add_channel(
        self,
        channel_id: int,
        title: str,
        *,
        broadcast: bool = False,
        creator: bool = False,
        username: str | None = None,
        dialog: bool = True,
        admin_rights=None,
    ) -> types.Channel:
        entity = types.Channel(
            id=channel_id,
            title=title,
            photo=types.ChatPhotoEmpty(),
            date=NOW - timedelta(days=1000),
            creator=creator,
            broadcast=broadcast,
            megagroup=not broadcast,
            access_hash=channel_id * 11,
            username=username,
            admin_rights=admin_rights,
        )
        self._register(entity, dialog=dialog)
        return entity

    def add_group(self, chat_id: int, title: str, *, creator: bool = False) -> types.Chat:
        entity = types.Chat(
            id=chat_id,
            title=title,
            photo=types.ChatPhotoEmpty(),
            participants_count=3,
            date=NOW - timedelta(days=1000),
            version=1,
            creator=creator,
        )
        self._register(entity, dialog=True)
        return entity

    def link(self, channel: types.Channel, discussion: types.Channel) -> None:
        self.chats[utils.get_peer_id(channel)].linked = discussion

    def set_admins(self, entity, users: list[types.User]) -> None:
        self.chats[utils.get_peer_id(entity)].admins = list(users)

    def _peer(self, entity):
        if isinstance(entity, types.Channel):
            return types.PeerChannel(entity.id)
        if isinstance(entity, types.Chat):
            return types.PeerChat(entity.id)
        return types.PeerUser(entity.id)

    def add_message(
        self,
        entity,
        sender_id: int,
        text: str = "",
        *,
        date: datetime | None = None,
        out: bool | None = None,
        my_reaction: bool = False,
        other_reaction: bool = False,
        min_reactions: bool = False,
    ) -> types.Message:
        pid = utils.get_peer_id(entity)
        chat = self.chats[pid]
        msg_id = next(self._channel_ids[pid]) if chat.is_channel else next(self._global_ids)
        reactions = None
        if my_reaction or other_reaction:
            results = [
                types.ReactionCount(
                    reaction=types.ReactionEmoji(emoticon="👍"),
                    count=1 + int(other_reaction and my_reaction),
                    chosen_order=None if (min_reactions or not my_reaction) else 0,
                )
            ]
            reactions = types.MessageReactions(results=results, min=min_reactions or None)
        # A channel post has no sender: Telegram sets post=True, leaves from_id empty and marks
        # only the posts made from this account as outgoing.
        post = bool(getattr(entity, "broadcast", False))
        msg = types.Message(
            id=msg_id,
            peer_id=self._peer(entity),
            date=date or NOW - timedelta(days=10),
            message=text,
            out=(sender_id == self.me.id) if out is None else out,
            from_id=None if post else types.PeerUser(sender_id),
            post=post or None,
            reactions=reactions,
        )
        msg._fake_my_reaction = my_reaction  # ground truth for GetMessagesReactions
        chat.messages[msg_id] = msg
        return msg

    def add_service(self, entity, sender_id: int, *, date: datetime | None = None) -> types.MessageService:
        """A service message such as 'joined the group' or 'topic created', with out=True when it is mine."""
        pid = utils.get_peer_id(entity)
        chat = self.chats[pid]
        msg_id = next(self._channel_ids[pid]) if chat.is_channel else next(self._global_ids)
        msg = types.MessageService(
            id=msg_id,
            peer_id=self._peer(entity),
            date=date or NOW - timedelta(days=10),
            action=types.MessageActionChatJoinedByLink(inviter_id=1),
            out=sender_id == self.me.id,
            from_id=types.PeerUser(sender_id),
        )
        chat.messages[msg_id] = msg
        return msg

    def chat_of(self, entity) -> FakeChat:
        return self.chats[utils.get_peer_id(entity)]

    def ids_in(self, entity) -> list[int]:
        return sorted(self.chat_of(entity).messages)

    # --- fault injection -------------------------------------------------

    def _maybe_fail(self, name: str) -> None:
        seconds = self.flood_once.pop(name, None)
        if seconds is not None:
            raise flood(seconds)

    # --- connection ------------------------------------------------------

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me

    async def log_out(self):
        self.logged_out = True
        return True

    async def start(self, phone=None, password=None, code_callback=None, **kwargs):
        self.connected = True
        return self

    # --- entities --------------------------------------------------------

    async def get_entity(self, ref):
        self.calls.append(("get_entity", {"ref": ref}))
        if ref in ("me", "self"):
            return self.me
        if isinstance(ref, int):
            if ref in self.uncached_ids:
                raise ValueError(f"Could not find the input entity for {ref}")
            if ref in self.chats:
                return self.chats[ref].entity
            if ref in self.users:
                return self.users[ref]
            raise ValueError(f"Could not find the input entity for {ref}")
        name = str(ref).lstrip("@").lower()
        for chat in self.chats.values():
            if (getattr(chat.entity, "username", None) or "").lower() == name:
                return chat.entity
        raise ValueError(f"No user has {ref!r} as username")

    async def iter_dialogs(self, *args, **kwargs):
        for pid in list(self.dialog_ids):
            yield SimpleNamespace(entity=self.chats[pid].entity, id=pid)

    async def iter_participants(self, entity, limit=None, *, filter=None, **kwargs):
        chat = self.chat_of(entity)
        for user in chat.admins:
            yield user

    # --- messages --------------------------------------------------------

    def _sender(self, ref) -> int | None:
        if ref is None:
            return None
        if ref == "me":
            return self.me.id
        return int(ref)

    async def iter_messages(
        self,
        entity,
        limit=None,
        *,
        offset_date=None,
        offset_id=0,
        max_id=0,
        min_id=0,
        add_offset=0,
        search=None,
        filter=None,
        from_user=None,
        reverse=False,
        **kwargs,
    ):
        self.calls.append(
            ("iter_messages", {"entity": utils.get_peer_id(entity), "offset_date": offset_date, "offset_id": offset_id,
                               "min_id": min_id, "max_id": max_id, "reverse": reverse, "from_user": from_user,
                               "search": search, "limit": limit})
        )
        self._maybe_fail("iter_messages")
        chat = self.chat_of(entity)
        sender = self._sender(from_user)
        msgs = sorted(chat.messages.values(), key=lambda m: m.id, reverse=not reverse)
        if search:
            msgs = [m for m in msgs if search.lower() in (m.message or "").lower()]
        if sender is not None:
            msgs = [m for m in msgs if m.sender_id == sender]
        if reverse:
            start = max(offset_id or 0, min_id or 0)
            if start:
                msgs = [m for m in msgs if m.id > start]
            elif offset_date:
                msgs = [m for m in msgs if m.date >= offset_date]
            if max_id:
                msgs = [m for m in msgs if m.id < max_id]
        else:
            if offset_id:
                msgs = [m for m in msgs if m.id < offset_id]
            if offset_date:
                msgs = [m for m in msgs if m.date < offset_date]
            if min_id:
                msgs = [m for m in msgs if m.id > min_id]
            if max_id:
                msgs = [m for m in msgs if m.id < max_id]
        if limit is not None:
            msgs = msgs[: int(limit)]
        flood_at = self.flood_after.pop("iter_messages", None)
        fail_at = self.fail_after.pop("iter_messages", None)
        for n, msg in enumerate(msgs):
            if flood_at is not None and n == flood_at[0]:
                raise flood(flood_at[1])
            if fail_at is not None and n == fail_at:
                raise ConnectionError("network dropped")
            yield msg

    def _find_global(self, msg_id: int):
        for chat in self.chats.values():
            if not chat.is_channel and msg_id in chat.messages:
                return chat, chat.messages[msg_id]
        return None, None

    async def get_messages(self, entity, *args, ids=None, **kwargs):
        self.calls.append(("get_messages", {"entity": utils.get_peer_id(entity), "ids": list(ids or [])}))
        self._maybe_fail("get_messages")
        chat = self.chat_of(entity)
        out = []
        for msg_id in ids:
            if msg_id in self.omit_ids:
                continue
            if chat.is_channel:
                out.append(chat.messages.get(msg_id))
                continue
            owner, msg = self._find_global(msg_id)
            if msg is None:
                out.append(None)
            elif self.validate_peer and owner is not chat:
                out.append(None)
            else:
                out.append(msg)
        return out

    async def delete_messages(self, entity, message_ids, *, revoke=True):
        ids = list(message_ids)
        self.calls.append(("delete_messages", {"entity": utils.get_peer_id(entity), "ids": ids, "revoke": revoke}))
        self._maybe_fail("delete_messages")
        for msg_id in ids:
            if msg_id in self.delete_errors:
                raise self.delete_errors[msg_id]
        self.delete_calls.append((utils.get_peer_id(entity), ids))
        chat = self.chat_of(entity)
        for msg_id in ids:
            if chat.is_channel:
                chat.messages.pop(msg_id, None)
            else:
                owner, msg = self._find_global(msg_id)
                if owner is not None:
                    owner.messages.pop(msg_id, None)
        return [types.messages.AffectedMessages(pts=1, pts_count=len(ids))]

    # --- raw requests ----------------------------------------------------

    async def __call__(self, request):
        self.requests.append(request)
        name = type(request).__name__
        self._maybe_fail(name)
        if type(request) in self.request_errors:
            raise self.request_errors[type(request)]
        if isinstance(request, functions.channels.GetFullChannelRequest):
            chat = self.chat_of(request.channel)
            linked = chat.linked
            full = SimpleNamespace(linked_chat_id=linked.id if linked is not None else None)
            return SimpleNamespace(full_chat=full, chats=[chat.entity] + ([linked] if linked is not None else []))
        if isinstance(request, functions.messages.GetFullChatRequest):
            chat = self.chats[-request.chat_id]
            parts = [types.ChatParticipantAdmin(user_id=u.id, inviter_id=ME, date=NOW) for u in chat.admins]
            return SimpleNamespace(full_chat=SimpleNamespace(participants=types.ChatParticipants(chat_id=request.chat_id, participants=parts, version=1)))
        if isinstance(request, functions.messages.SendReactionRequest):
            if request.reaction not in ([], None):
                raise AssertionError("tgsweep must never set a reaction")
            msg = self.chat_of(request.peer).messages.get(request.msg_id)
            if msg is not None and msg.reactions:
                msg._fake_my_reaction = False
                for rc in msg.reactions.results:
                    if rc.chosen_order is not None:
                        rc.chosen_order = None
                        rc.count -= 1
            return types.Updates(updates=[], users=[], chats=[], date=NOW, seq=0)
        if isinstance(request, functions.messages.GetMessagesReactionsRequest):
            chat = self.chat_of(request.peer)
            updates = []
            for msg_id in request.id:
                msg = chat.messages.get(msg_id)
                if msg is None:
                    continue
                order = 0 if getattr(msg, "_fake_my_reaction", False) else None
                full = types.MessageReactions(
                    results=[types.ReactionCount(reaction=types.ReactionEmoji(emoticon="👍"), count=1, chosen_order=order)]
                )
                updates.append(types.UpdateMessageReactions(peer=msg.peer_id, msg_id=msg_id, reactions=full))
            return types.Updates(updates=updates, users=[], chats=[], date=NOW, seq=0)
        if isinstance(request, functions.account.GetAuthorizationsRequest):
            return types.account.Authorizations(authorization_ttl_days=180, authorizations=list(self.authorizations))
        if isinstance(request, functions.account.ResetAuthorizationRequest):
            self.authorizations = [a for a in self.authorizations if a.hash != request.hash]
            return True
        raise AssertionError(f"unexpected request {name}")
