"""Edge cases found in review: channel direct messages, service messages, lost sessions, flood waits, stops."""

from __future__ import annotations

from datetime import timedelta

import pytest
from telethon import errors
from telethon.tl import functions, types

from fake import ME, NOW, FakeClient
from tgsweep.cleanup import Policy, apply_plan, build_plan, delete_own
from tgsweep.journal import Journal

pytestmark = pytest.mark.anyio

OLD = NOW - timedelta(days=5)
SECOND = 2000


# --- channel direct messages (monoforum) are private ---------------------


def monoforum(client: FakeClient):
    entity = client.add_channel(700, "Channel DMs")
    entity.monoforum = True
    return entity


async def test_monoforum_not_cleaned_by_default():
    client = FakeClient()
    entity = monoforum(client)
    client.add_message(entity, ME, "to the channel", date=OLD)
    plan = await build_plan(client, ME, Policy(), retention_days=2, now=NOW, reaction_state={})
    assert plan.targets == []


# --- service messages are never deleted ----------------------------------


async def test_service_messages_refused(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    joined = client.add_service(group, ME, date=OLD)
    report = await delete_own(client, group, [joined.id], me_id=ME, journal=Journal(None), sleep=sleeper)
    assert report.refused == [joined.id]
    assert client.delete_calls == []
    plan = await build_plan(client, ME, Policy(include_reactions=False), retention_days=2, now=NOW, reaction_state={})
    assert plan.targets == []


# --- ids silently dropped by Telegram -------------------------------------


async def test_omitted_ids_do_not_shift_the_chunk(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    a = client.add_message(group, SECOND, "theirs")
    b = client.add_message(group, ME, "mine")
    c = client.add_message(group, ME, "mine too")
    client.omit_ids = {a.id}
    report = await delete_own(client, group, [a.id, b.id, c.id], me_id=ME, journal=Journal(None), sleep=sleeper)
    assert report.missing == [a.id]
    assert sorted(report.deleted) == [b.id, c.id]
    assert client.ids_in(group) == [a.id]


# --- a lost session is not a per-message failure --------------------------


async def test_session_loss_during_delete_propagates(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    mine = client.add_message(group, ME, "m")
    client.delete_errors[mine.id] = errors.AuthKeyUnregisteredError(request=None)
    with pytest.raises(errors.AuthKeyUnregisteredError):
        await delete_own(client, group, [mine.id], me_id=ME, journal=Journal(None), sleep=sleeper)


# --- reaction scan position -----------------------------------------------


async def test_reaction_scan_kept_when_unreact_fails(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    client.add_message(group, SECOND, "x", date=OLD, my_reaction=True)
    state: dict[str, int] = {}
    plan = await build_plan(client, ME, Policy(), retention_days=2, now=NOW, reaction_state=state)
    client.request_errors[functions.messages.SendReactionRequest] = errors.MessageIdInvalidError(request=None)
    report = await apply_plan(client, plan, me_id=ME, journal=Journal(None), reaction_state=state, sleep=sleeper)
    assert report.failed == 1
    assert state == {}


async def test_reaction_session_loss_propagates(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    client.add_message(group, SECOND, "x", date=OLD, my_reaction=True)
    state: dict[str, int] = {}
    plan = await build_plan(client, ME, Policy(), retention_days=2, now=NOW, reaction_state=state)
    client.request_errors[functions.messages.SendReactionRequest] = errors.AuthKeyUnregisteredError(request=None)
    with pytest.raises(errors.AuthKeyUnregisteredError):
        await apply_plan(client, plan, me_id=ME, journal=Journal(None), reaction_state=state, sleep=sleeper)
    assert state == {}


# --- FloodWait on the discussion group lookup -------------------------------


async def test_floodwait_on_linked_chat_is_waited(sleeper):
    client = FakeClient()
    channel = client.add_channel(300, "C", broadcast=True)
    discussion = client.add_channel(301, "C chat", dialog=False)
    client.link(channel, discussion)
    client.add_message(discussion, ME, "comment", date=OLD)
    client.flood_once["GetFullChannelRequest"] = 120
    plan = await build_plan(client, ME, Policy(), retention_days=2, now=NOW, reaction_state={}, sleep=sleeper)
    assert plan.comments == 1
    assert sleeper.calls == [121]


# --- stop between chunks on shutdown ----------------------------------------


async def test_stop_request_ends_between_chunks(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    mine = [client.add_message(group, ME, "m", date=OLD).id for _ in range(150)]
    plan = await build_plan(client, ME, Policy(include_reactions=False), retention_days=2, now=NOW, reaction_state={})
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2  # target check, first chunk check, then stop before the second chunk

    journal = Journal(None)
    report = await apply_plan(client, plan, me_id=ME, journal=journal, reaction_state={}, sleep=sleeper,
                              should_stop=should_stop)
    assert report.stopped is True
    assert report.messages == 100
    assert len(client.ids_in(group)) == 50
    deleting = [r for r in journal.records if r["result"] == "deleting"]
    assert len(deleting) == 100


# --- Telethon fields and chats that went away -------------------------------


def test_monoforum_type_field_exists_in_telethon():
    import inspect

    assert "monoforum" in inspect.signature(types.Channel.__init__).parameters


async def test_unreachable_chat_fails_its_ids_and_returns(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    ids = [client.add_message(group, ME, "m", date=OLD).id for _ in range(3)]

    async def private(*args, **kwargs):
        raise errors.ChannelPrivateError(request=None)

    client.get_messages = private
    rep = await delete_own(client, group, ids, me_id=ME, journal=Journal(None), sleep=sleeper)
    assert rep.failed == ids and rep.deleted == []
    assert client.delete_calls == []


async def test_session_loss_while_fetching_propagates(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    ids = [client.add_message(group, ME, "m", date=OLD).id]

    async def revoked(*args, **kwargs):
        raise errors.AuthKeyUnregisteredError(request=None)

    client.get_messages = revoked
    with pytest.raises(errors.AuthKeyUnregisteredError):
        await delete_own(client, group, ids, me_id=ME, journal=Journal(None), sleep=sleeper)
