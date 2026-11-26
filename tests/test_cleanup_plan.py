"""Acceptance 2 and an offline version of 11: what cleanup selects and what apply does."""

from __future__ import annotations

from datetime import timedelta

import pytest

from fake import ME, NOW, FakeClient
from tgsweep.cleanup import Plan, Policy, apply_plan, build_plan, narrowed, skip_reason
from tgsweep.journal import Journal

pytestmark = pytest.mark.anyio

SECOND = 2000
OLD = NOW - timedelta(days=5)
FRESH = NOW - timedelta(hours=3)


def world():
    client = FakeClient()
    group = client.add_channel(100, "Regular group")
    basic = client.add_group(200, "Basic group")
    channel = client.add_channel(300, "News channel", broadcast=True)
    own_group = client.add_channel(400, "My own group", creator=True)
    friend = client.add_user(3000, "Friend")
    excluded = client.add_channel(500, "Excluded group")
    for entity in (group, basic, channel, own_group, friend, excluded, client.me, client.service_user):
        client.add_message(entity, ME, "old mine", date=OLD)
    return client, dict(group=group, basic=basic, channel=channel, own_group=own_group, friend=friend, excluded=excluded)


async def plan_for(client, policy, retention=2, state=None):
    return await build_plan(client, ME, policy, retention_days=retention, now=NOW, reaction_state=state or {})


async def test_skip_rules_default_policy():
    client, w = world()
    plan = await plan_for(client, Policy(exclude=[-1000000000500]))
    targets = {t.chat_id for t in plan.targets}
    assert targets == {-1000000000100, -200}
    reasons = {chat_id: reason for chat_id, _, reason in plan.skipped}
    assert reasons[ME] == "forbidden"
    assert reasons[777000] == "forbidden"
    assert reasons[-1000000000300] == "broadcast"
    assert reasons[-1000000000400] == "creator"
    assert reasons[3000] == "dm"
    assert reasons[-1000000000500] == "excluded"


async def test_include_opens_channels_and_creator_chats_but_not_forbidden():
    client, w = world()
    policy = Policy(include=[-1000000000300, -1000000000400, ME, 777000, 3000])
    plan = await plan_for(client, policy)
    targets = {t.chat_id for t in plan.targets}
    assert {-1000000000300, -1000000000400} <= targets
    # A private chat stays out even when listed in include.
    assert ME not in targets and 777000 not in targets and 3000 not in targets


def test_skip_reason_forbidden_beats_include():
    client = FakeClient()
    policy = Policy(include=[ME, 777000])
    assert skip_reason(client.me, ME, policy) == "forbidden"
    assert skip_reason(client.service_user, ME, policy) == "forbidden"


async def test_retention_keeps_fresh_messages():
    client = FakeClient()
    group = client.add_channel(100, "G")
    old = client.add_message(group, ME, "old", date=OLD)
    client.add_message(group, ME, "fresh", date=FRESH)
    plan = await plan_for(client, Policy(), retention=2)
    assert plan.targets[0].message_ids == [old.id]
    plan7 = await plan_for(client, Policy(), retention=7)
    assert plan7.targets == []


async def test_comments_in_linked_discussion_group():
    client = FakeClient()
    channel = client.add_channel(300, "Channel", broadcast=True)
    discussion = client.add_channel(301, "Channel chat", dialog=False)
    client.link(channel, discussion)
    comments = [client.add_message(discussion, ME, "comment", date=OLD).id for _ in range(3)]
    client.add_message(discussion, SECOND, "other comment", date=OLD)
    plan = await plan_for(client, Policy())
    assert plan.comments == 3
    assert plan.messages == 0
    target = next(t for t in plan.targets if t.kind == "comments")
    assert target.message_ids == comments


async def test_comments_skipped_in_my_own_discussion_group_unless_included():
    client = FakeClient()
    channel = client.add_channel(300, "My channel", broadcast=True, creator=True)
    discussion = client.add_channel(301, "My channel chat", creator=True, dialog=False)
    client.link(channel, discussion)
    client.add_message(discussion, ME, "comment", date=OLD)
    assert (await plan_for(client, Policy())).comments == 0
    assert (await plan_for(client, Policy(include=[-1000000000301]))).comments == 1


async def test_reactions_found_and_broadcast_not_scanned():
    client = FakeClient()
    group = client.add_channel(100, "G")
    channel = client.add_channel(300, "C", broadcast=True)
    reacted = [client.add_message(group, SECOND, "x", date=OLD, my_reaction=True).id for _ in range(5)]
    client.add_message(group, SECOND, "y", date=OLD, other_reaction=True)
    client.add_message(group, SECOND, "z", date=FRESH, my_reaction=True)
    client.add_message(channel, SECOND, "post", date=OLD, my_reaction=True)
    plan = await plan_for(client, Policy(include=[-1000000000300]))
    by_id = {t.chat_id: t for t in plan.targets}
    assert by_id[-1000000000100].reaction_ids == reacted
    assert -1000000000300 not in by_id or by_id[-1000000000300].reaction_ids == []


async def test_min_reactions_are_resolved_with_get_messages_reactions():
    client = FakeClient()
    group = client.add_channel(100, "G")
    msg = client.add_message(group, SECOND, "x", date=OLD, my_reaction=True, min_reactions=True)
    plan = await plan_for(client, Policy())
    assert plan.targets[0].reaction_ids == [msg.id]


async def test_live_scenario_offline(sleeper):
    """Offline version of acceptance 11: 20 others, 20 mine, 5 reactions, 3 comments."""
    client = FakeClient()
    group = client.add_channel(100, "TEST_GROUP")
    channel = client.add_channel(300, "TEST_CHANNEL", broadcast=True)
    discussion = client.add_channel(301, "TEST_CHANNEL chat", dialog=False)
    client.link(channel, discussion)
    theirs = [
        client.add_message(group, SECOND, f"second {i}", date=OLD, my_reaction=i < 5).id for i in range(20)
    ]
    for i in range(20):
        client.add_message(group, ME, f"main {i}", date=OLD)
    reacted = theirs[:5]
    for _ in range(3):
        client.add_message(discussion, ME, "comment", date=OLD)

    state: dict[str, int] = {}
    plan = await plan_for(client, Policy(), state=state)
    assert (plan.messages, plan.comments, plan.reactions) == (20, 3, 5)

    report = await apply_plan(client, plan, me_id=ME, journal=Journal(None), reaction_state=state, sleep=sleeper)
    assert (report.messages, report.comments, report.reactions) == (20, 3, 5)
    assert client.ids_in(group) == sorted(theirs)
    assert client.ids_in(discussion) == []
    for msg_id in reacted:
        rc = client.chat_of(group).messages[msg_id].reactions.results[0]
        assert rc.chosen_order is None
    search_mine = [m async for m in client.iter_messages(group, from_user="me")]
    assert search_mine == []
    assert state[str(-1000000000100)] == 40


async def test_reaction_scan_is_incremental(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    first = client.add_message(group, SECOND, "x", date=OLD, my_reaction=True)
    state: dict[str, int] = {}
    plan = await plan_for(client, Policy(), state=state)
    await apply_plan(client, plan, me_id=ME, journal=Journal(None), reaction_state=state, sleep=sleeper)
    assert state[str(-1000000000100)] == first.id

    later = client.add_message(group, SECOND, "y", date=OLD + timedelta(hours=1), my_reaction=True)
    client.calls.clear()
    plan2 = await plan_for(client, Policy(), state=state)
    assert plan2.targets[0].reaction_ids == [later.id]
    scans = [c for name, c in client.calls if name == "iter_messages" and c["from_user"] is None]
    assert scans and all(c["min_id"] == first.id for c in scans)


async def test_floodwait_during_plan(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    ids = [client.add_message(group, ME, "m", date=OLD).id for _ in range(5)]
    client.flood_after["iter_messages"] = (2, 12)
    plan = await build_plan(client, ME, Policy(include_reactions=False), retention_days=2, now=NOW,
                            reaction_state={}, sleep=sleeper)
    assert plan.targets[0].message_ids == ids
    assert sleeper.calls == [13]


async def test_only_list_limits_cleanup_to_test_chats():
    client, w = world()
    test_group = client.add_channel(900, "TEST_GROUP", creator=True)
    channel = client.add_channel(901, "TEST_CHANNEL", broadcast=True, creator=True)
    discussion = client.add_channel(902, "TEST_CHANNEL chat", creator=True, dialog=False)
    client.link(channel, discussion)
    client.add_message(test_group, ME, "m", date=OLD)
    client.add_message(discussion, ME, "c", date=OLD)
    stand = [-1000000000900, -1000000000901, -1000000000902]
    plan = await plan_for(client, Policy(only=stand, include=stand))
    assert {t.chat_id for t in plan.targets} == {-1000000000900, -1000000000902}
    assert (plan.messages, plan.comments) == (1, 1)
    reasons = {chat_id: reason for chat_id, _, reason in plan.skipped}
    assert reasons[-1000000000100] == "not_in_only"
    assert reasons[ME] == "forbidden"


def test_zero_retention_only_in_test_mode():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Policy(older_than_days=0)
    p = Policy(older_than_days=0, only=[-1000000000900])
    assert p.effective_retention(7) == 0
    assert Policy(older_than_days=2).effective_retention(7) == 7


async def test_included_channel_cleans_only_my_posts(sleeper):
    """Channel posts have no sender: mine are found by the outgoing flag, a co-admin's post stays."""
    client = FakeClient()
    channel = client.add_channel(300, "Shared channel", broadcast=True)
    mine = [client.add_message(channel, ME, "my post", date=OLD).id for _ in range(2)]
    coadmin = client.add_message(channel, SECOND, "co-admin post", date=OLD)
    fresh = client.add_message(channel, ME, "fresh post", date=FRESH)
    assert coadmin.from_id is None and coadmin.post is True and coadmin.out is False

    plan = await plan_for(client, Policy(include=[-1000000000300]))
    assert plan.messages == 2
    assert plan.targets[0].message_ids == mine
    scans = [c[1] for c in client.calls if c[0] == "iter_messages" and c[1]["entity"] == -1000000000300]
    assert scans and all(c["from_user"] is None for c in scans)

    report = await apply_plan(client, plan, me_id=ME, journal=Journal(None), reaction_state={}, sleep=sleeper)
    assert report.messages == 2
    assert client.ids_in(channel) == sorted([coadmin.id, fresh.id])


async def test_channel_post_from_other_admin_is_refused_by_delete_own(sleeper):
    from tgsweep.cleanup import delete_own

    client = FakeClient()
    channel = client.add_channel(300, "Shared channel", broadcast=True)
    coadmin = client.add_message(channel, SECOND, "co-admin post")
    report = await delete_own(client, channel, [coadmin.id], me_id=ME, journal=Journal(None), sleep=sleeper)
    assert report.refused == [coadmin.id]
    assert client.delete_calls == []


# --- per-run limits ---------------------------------------------------------------


def test_default_limits_match_the_plan():
    p = Policy()
    assert (p.max_messages_per_run, p.max_reactions_per_run, p.pause_s) == (1000, 300, 4.0)


async def test_message_limit_keeps_the_oldest_and_defers_the_rest():
    client = FakeClient()
    first = client.add_channel(100, "First")
    second = client.add_channel(101, "Second")
    first_ids = [client.add_message(first, ME, "m", date=OLD).id for _ in range(7)]
    second_ids = [client.add_message(second, ME, "m", date=OLD).id for _ in range(6)]
    policy = Policy(max_messages_per_run=10, include_reactions=False)
    whole = await plan_for(client, policy)
    plan = narrowed(whole, whole.targets, policy)
    assert whole.messages == 13  # the plan itself stays whole: the limit applies after picking
    by_id = {t.chat_id: t.message_ids for t in plan.targets}
    assert plan.messages == 10 and plan.deferred_messages == 3
    assert by_id[-1000000000100] == first_ids
    assert by_id[-1000000000101] == second_ids[:3]


async def test_reaction_limit_rewinds_the_scan_so_the_rest_comes_next_run(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    reacted = [client.add_message(group, SECOND, "x", date=OLD, my_reaction=True).id for _ in range(5)]
    state: dict[str, int] = {}
    policy = Policy(max_reactions_per_run=2)

    plan = narrowed(p := await plan_for(client, policy, state=state), p.targets, policy)
    assert plan.targets[0].reaction_ids == reacted[:2]
    assert plan.deferred_reactions == 3
    await apply_plan(client, plan, me_id=ME, journal=Journal(None), reaction_state=state, sleep=sleeper)
    assert state == {"-1000000000100": reacted[2] - 1}

    second = narrowed(p := await plan_for(client, policy, state=state), p.targets, policy)
    assert second.targets[0].reaction_ids == reacted[2:4]
    await apply_plan(client, second, me_id=ME, journal=Journal(None), reaction_state=state, sleep=sleeper)
    third = narrowed(p := await plan_for(client, policy, state=state), p.targets, policy)
    assert third.targets[0].reaction_ids == reacted[4:]
    assert third.deferred_reactions == 0


async def test_zero_reaction_batch_means_reactions_are_not_searched(sleeper):
    """Reactions that would never be removed are not offered: the owner would confirm them in vain."""
    client = FakeClient()
    group = client.add_channel(100, "G")
    client.add_message(group, SECOND, "x", date=OLD, my_reaction=True)
    state: dict[str, int] = {}
    plan = await plan_for(client, Policy(max_reactions_per_run=0), state=state)
    assert plan.reactions == 0 and plan.targets == []
    assert not any(c[0] == "iter_messages" and c[1]["reverse"] for c in client.calls)  # no reaction scan
    assert state == {}


def test_plan_expires_fifteen_minutes_after_it_was_made():
    plan = Plan(plan_id="p", created_at=NOW, retention_days=0, cutoff=NOW, targets=[])
    assert not plan.expired(NOW + timedelta(minutes=14, seconds=59))
    assert plan.expired(NOW + timedelta(minutes=15))


def big_and_small():
    client = FakeClient()
    big = client.add_channel(100, "Big")
    small = client.add_channel(200, "Small")
    for _ in range(5):
        client.add_message(big, ME, "mine", date=OLD)
    for _ in range(2):
        client.add_message(small, ME, "mine", date=OLD)
    return client


async def test_plan_offers_every_chat_in_full_whatever_the_limit():
    """The limit applies to the ticked chats only: it must not hide a chat behind one the owner keeps."""
    plan = await plan_for(big_and_small(), Policy(max_messages_per_run=3))
    assert sorted((t.title, len(t.message_ids)) for t in plan.targets) == [("Big", 5), ("Small", 2)]


async def test_narrowed_trims_copies_and_leaves_the_plan_whole():
    plan = await plan_for(big_and_small(), Policy(max_messages_per_run=3))
    big = next(t for t in plan.targets if t.title == "Big")
    chosen = narrowed(plan, [big], Policy(max_messages_per_run=3))
    assert len(chosen.targets[0].message_ids) == 3 and chosen.deferred_messages == 2
    assert len(big.message_ids) == 5

