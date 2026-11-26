"""Acceptance 1 and 7: delete_own deletes exactly my messages in exactly this chat."""

from __future__ import annotations

import json
import sys

import pytest
from telethon import errors

from fake import ME, FakeClient
from tgsweep import guards
from tgsweep.cleanup import delete_own
from tgsweep.journal import Journal

pytestmark = pytest.mark.anyio

OTHER = 2000


def _group(client: FakeClient):
    group = client.add_channel(500, "Test group")
    mine = [client.add_message(group, ME, f"my secret text {i}").id for i in range(10)]
    others = [client.add_message(group, OTHER, f"their text {i}").id for i in range(10)]
    return group, mine, others


async def test_ten_mine_ten_others(tmp_path, sleeper):
    client = FakeClient()
    group, mine, others = _group(client)
    journal = Journal(tmp_path / "actions.jsonl")

    report = await delete_own(client, group, mine + others, me_id=ME, journal=journal, sleep=sleeper)

    assert sorted(report.deleted) == sorted(mine)
    assert sorted(report.refused) == sorted(others)
    assert client.ids_in(group) == sorted(others)
    deleted_ids = [i for _, ids in client.delete_calls for i in ids]
    assert sorted(deleted_ids) == sorted(mine)


async def test_journal_has_no_texts(tmp_path, sleeper):
    client = FakeClient()
    group, mine, others = _group(client)
    path = tmp_path / "actions.jsonl"
    await delete_own(client, group, mine + others, me_id=ME, journal=Journal(path), sleep=sleeper)

    raw = path.read_text(encoding="utf-8")
    assert "secret text" not in raw and "their text" not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == 30
    assert {r["result"] for r in records} == {"deleting", "deleted", "refused"}
    order = [r["result"] for r in records if r["msg_id"] == mine[0]]
    assert order == ["deleting", "deleted"]
    assert all("text" not in r for r in records)
    if sys.platform != "win32":  # Windows has no POSIX modes
        assert oct(path.stat().st_mode & 0o777) == "0o600"


async def test_admin_flag_does_not_matter_out_false_and_other_sender(sleeper, tmp_path):
    """A message by someone else is refused even in a chat where I am admin."""
    client = FakeClient()
    group = client.add_channel(501, "Where I am admin", admin_rights=None)
    foreign = client.add_message(group, OTHER, "x", out=False)
    report = await delete_own(client, group, [foreign.id], me_id=ME, journal=Journal(None), sleep=sleeper)
    assert report.refused == [foreign.id]
    assert client.delete_calls == []


async def test_global_ids_from_other_basic_group_are_refused(sleeper):
    """Basic groups share one id space; a foreign chat's id must never be deleted."""
    client = FakeClient(validate_peer=False)
    group_a = client.add_group(10, "A")
    group_b = client.add_group(11, "B")
    my_msg_in_b = client.add_message(group_b, ME, "mine but in B")

    report = await delete_own(client, group_a, [my_msg_in_b.id], me_id=ME, journal=Journal(None), sleep=sleeper)

    assert report.refused == [my_msg_in_b.id]
    assert report.deleted == []
    assert client.ids_in(group_b) == [my_msg_in_b.id]
    assert client.delete_calls == []


async def test_global_ids_with_telethon_peer_validation_are_missing(sleeper):
    client = FakeClient(validate_peer=True)
    group_a = client.add_group(10, "A")
    group_b = client.add_group(11, "B")
    my_msg_in_b = client.add_message(group_b, ME, "mine but in B")

    report = await delete_own(client, group_a, [my_msg_in_b.id], me_id=ME, journal=Journal(None), sleep=sleeper)

    assert report.missing == [my_msg_in_b.id]
    assert client.ids_in(group_b) == [my_msg_in_b.id]


@pytest.mark.parametrize("bot", [False, True])
async def test_private_chats_are_never_touched(bot, sleeper):
    client = FakeClient()
    peer = client.add_user(3000, "Friend", bot=bot)
    mine = client.add_message(peer, ME, "hi")
    with pytest.raises(guards.ForbiddenChatError, match="Private chats"):
        await delete_own(client, peer, [mine.id], me_id=ME, journal=Journal(None), sleep=sleeper)
    assert client.delete_calls == []
    assert client.ids_in(peer) == [mine.id]


@pytest.mark.parametrize("which", ["saved", "service"])
async def test_forbidden_chats_raise(which, sleeper):
    client = FakeClient()
    entity = client.me if which == "saved" else client.service_user
    msg = client.add_message(entity, ME, "x")
    with pytest.raises(guards.ForbiddenChatError):
        await delete_own(client, entity, [msg.id], me_id=ME, journal=Journal(None), sleep=sleeper)
    assert client.delete_calls == []


async def test_chunks_of_100_with_pause(sleeper):
    client = FakeClient()
    group = client.add_channel(502, "Big")
    mine = [client.add_message(group, ME, "m").id for _ in range(250)]
    report = await delete_own(client, group, mine, me_id=ME, journal=Journal(None), sleep=sleeper, pause=1.0)
    assert len(report.deleted) == 250
    assert [len(ids) for _, ids in client.delete_calls] == [100, 100, 50]
    assert sleeper.calls == [1.0, 1.0]


async def test_floodwait_sleeps_and_finishes(sleeper):
    client = FakeClient()
    group, mine, others = _group(client)
    client.flood_once["get_messages"] = 7
    client.flood_once["delete_messages"] = 30
    report = await delete_own(client, group, mine, me_id=ME, journal=Journal(None), sleep=sleeper)
    assert sorted(report.deleted) == sorted(mine)
    assert sleeper.calls == [8, 31]
    assert client.ids_in(group) == sorted(others)


async def test_delete_forbidden_is_reported_and_next_chunk_continues(sleeper):
    client = FakeClient()
    group = client.add_channel(503, "Restricted")
    first = [client.add_message(group, ME, "m").id for _ in range(100)]
    second = [client.add_message(group, ME, "m").id for _ in range(5)]
    client.delete_errors[first[0]] = errors.MessageDeleteForbiddenError(request=None)
    report = await delete_own(client, group, first + second, me_id=ME, journal=Journal(None), sleep=sleeper)
    assert sorted(report.failed) == sorted(first)
    assert sorted(report.deleted) == sorted(second)


async def test_missing_ids_are_reported(sleeper):
    client = FakeClient()
    group = client.add_channel(504, "G")
    msg = client.add_message(group, ME, "m")
    report = await delete_own(client, group, [msg.id, 999], me_id=ME, journal=Journal(None), sleep=sleeper)
    assert report.deleted == [msg.id]
    assert report.missing == [999]
