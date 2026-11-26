"""Private chats are never opened by cleanup, whatever the policy says.

Private means people, bots (BotFather holds bot tokens), channel direct messages (monoforum),
secret chats, Saved Messages and 777000. Only groups and channels pass guards.assert_readable.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from telethon import utils

from fake import ME, NOW, FakeClient
from tgsweep import guards
from tgsweep.cleanup import Plan, Policy, Target, apply_plan, load_policy
from tgsweep.errors import ConfigError
from tgsweep.journal import Journal

OLD = NOW - timedelta(days=5)
FRIEND = 3000
BOTFATHER = 3001


def account_with_private_chats():
    client = FakeClient()
    friend = client.add_user(FRIEND, "Friend Secretname")
    client.add_message(friend, FRIEND, "my wallet seed is ...", date=OLD)
    client.add_message(friend, ME, "mine in the dm", date=OLD)
    botfather = client.add_user(BOTFATHER, "BotFather", bot=True)
    client.add_message(botfather, BOTFATHER, "token 123:ABC", date=OLD)
    group = client.add_channel(100, "Work group")
    client.add_message(group, ME, "in a group", date=OLD)
    return client, friend, botfather, group


def read_calls(client, peer_id):
    return [c for c in client.calls if c[0] in ("iter_messages", "get_messages") and c[1].get("entity") == peer_id]


def test_policy_cannot_turn_private_chat_cleanup_on(tmp_path):
    with pytest.raises(ValueError, match="private chats are never cleaned"):
        Policy(include_dms=True)
    path = tmp_path / "policy.yaml"
    path.write_text("include_dms: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="private chats are never cleaned"):
        load_policy(path)
    path.write_text("include_dms: false\n", encoding="utf-8")  # old files with the line still load
    assert load_policy(path).include_dms is False


@pytest.mark.anyio
async def test_a_plan_with_a_private_chat_is_refused_before_anything_is_deleted(sleeper):
    """Defence in depth: even a plan that somehow lists a private chat deletes nothing at all."""
    client, friend, botfather, group = account_with_private_chats()
    mine_in_group = client.ids_in(group)
    mine_in_dm = client.ids_in(friend)
    plan = Plan(
        plan_id="p",
        created_at=NOW,
        retention_days=2,
        cutoff=NOW,
        targets=[
            Target(entity=group, chat_id=utils.get_peer_id(group), title="Work group", kind="megagroup",
                   message_ids=mine_in_group),
            Target(entity=friend, chat_id=FRIEND, title="Friend", kind="dm", message_ids=mine_in_dm,
                   reaction_ids=mine_in_dm),
        ],
    )
    with pytest.raises(guards.ForbiddenChatError, match="Private chats"):
        await apply_plan(client, plan, me_id=ME, journal=Journal(None), reaction_state={}, sleep=sleeper)
    assert client.delete_calls == [] and client.requests == []
    assert client.ids_in(group) == mine_in_group


def test_only_groups_and_channels_pass_the_gate():
    assert guards.OPEN_KINDS == frozenset({"group", "megagroup", "broadcast"})
