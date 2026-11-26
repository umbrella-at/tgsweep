"""Deleting everything confirmed in batches, stopping and going on, and how the search reports its progress."""

from __future__ import annotations

from datetime import timedelta

import pytest

from telethon import errors
from telethon.tl import functions

from fake import ME, NOW, FakeClient
from tgsweep.cleanup import (
    ApplyProgress,
    Policy,
    ScanStats,
    apply_in_batches,
    build_plan,
    keep_empty_scans,
)
from tgsweep.journal import Journal

pytestmark = pytest.mark.anyio

OLD = NOW - timedelta(days=5)
OTHER = 2000
FIRST, SECOND = -1000000000100, -1000000000101


def two_chats(first=7, second=6):
    client = FakeClient()
    a = client.add_channel(100, "First")
    b = client.add_channel(101, "Second")
    ids_a = [client.add_message(a, ME, "m", date=OLD).id for _ in range(first)]
    ids_b = [client.add_message(b, ME, "m", date=OLD).id for _ in range(second)]
    return client, a, b, ids_a, ids_b


async def plan_for(client, policy, state=None, **kw):
    return await build_plan(client, ME, policy, retention_days=2, now=NOW, reaction_state={} if state is None else state, **kw)


async def run(client, plan, targets, policy, sleeper, state=None, **kw):
    return await apply_in_batches(
        client, plan, targets, policy, me_id=ME, journal=Journal(None),
        reaction_state={} if state is None else state, sleep=sleeper, **kw,
    )


class CountingReactions(FakeClient):
    """Counts SendReaction requests; the ids in fail_once fail on their first try."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reactions_sent = 0
        self.fail_once: set[int] = set()

    async def __call__(self, request):
        if isinstance(request, functions.messages.SendReactionRequest):
            self.reactions_sent += 1
            if request.msg_id in self.fail_once:
                self.fail_once.discard(request.msg_id)
                raise errors.MessageIdInvalidError(request=None)
        return await super().__call__(request)


def deleted_ids(client):
    return [i for _, ids in client.delete_calls for i in ids]


# --- batches -----------------------------------------------------------------


async def test_everything_confirmed_goes_in_batches_in_one_call(sleeper):
    client, a, b, ids_a, ids_b = two_chats()
    policy = Policy(max_messages_per_run=5, include_reactions=False)
    plan = await plan_for(client, policy)
    outcome = await run(client, plan, plan.targets, policy, sleeper)
    assert [len(ids) for _, ids in client.delete_calls] == [5, 2, 3, 3]
    assert client.ids_in(a) == [] and client.ids_in(b) == []
    assert outcome.remaining == [] and outcome.left == 0 and not outcome.report.stopped
    # One row per chat, the batches added up.
    assert [(c.title, c.deleted) for c in outcome.report.chats] == [("First", 7), ("Second", 6)]
    assert outcome.report.messages == 13


async def test_the_plan_on_screen_stays_whole(sleeper):
    client, a, b, ids_a, ids_b = two_chats()
    policy = Policy(max_messages_per_run=5, include_reactions=False)
    plan = await plan_for(client, policy)
    await run(client, plan, plan.targets, policy, sleeper)
    assert [len(t.message_ids) for t in plan.targets] == [7, 6]


async def test_progress_counts_up_to_the_total(sleeper):
    client, a, b, ids_a, ids_b = two_chats()
    policy = Policy(max_messages_per_run=5, include_reactions=False)
    plan = await plan_for(client, policy)
    seen: list[ApplyProgress] = []
    await run(client, plan, plan.targets, policy, sleeper, progress=seen.append)
    assert seen[0] == ApplyProgress(0, 13)
    assert [(p.done, p.chat) for p in seen[1:]] == [(5, "First"), (7, "First"), (10, "Second"), (13, "Second")]
    assert {p.total for p in seen} == {13}


async def test_stop_keeps_what_is_left_and_going_on_finishes_it(sleeper):
    client, a, b, ids_a, ids_b = two_chats()
    policy = Policy(max_messages_per_run=5, include_reactions=False)
    plan = await plan_for(client, policy)
    stop = {"now": False}
    real = client.delete_messages

    async def delete_then_stop(*args, **kw):
        stop["now"] = True
        return await real(*args, **kw)

    client.delete_messages = delete_then_stop
    outcome = await run(client, plan, plan.targets, policy, sleeper, should_stop=lambda: stop["now"])
    assert outcome.report.stopped and outcome.report.messages == 5
    assert [(t.title, t.message_ids) for t in outcome.remaining] == [("First", ids_a[5:]), ("Second", ids_b)]
    assert outcome.left == 8

    client.delete_messages = real
    rest = await run(client, plan, outcome.remaining, policy, sleeper)
    assert client.ids_in(a) == [] and client.ids_in(b) == []
    assert sorted(deleted_ids(client)) == sorted(ids_a + ids_b)  # nothing sent twice
    assert rest.remaining == [] and not rest.report.stopped


async def test_stop_inside_a_big_chat_keeps_exactly_its_tail(sleeper):
    client, a, b, ids_a, ids_b = two_chats(first=250, second=0)
    policy = Policy(include_reactions=False)
    plan = await plan_for(client, policy)
    real = client.delete_messages
    calls = []

    async def delete_then_stop(*args, **kw):
        calls.append(1)
        return await real(*args, **kw)

    client.delete_messages = delete_then_stop
    outcome = await run(client, plan, plan.targets, policy, sleeper, should_stop=lambda: bool(calls))
    assert [t.message_ids for t in outcome.remaining] == [ids_a[100:]]


async def test_reaction_stop_keeps_the_rest_and_the_scan_position(sleeper):
    client = CountingReactions()
    group = client.add_channel(100, "G")
    reacted = [client.add_message(group, OTHER, "x", date=OLD, my_reaction=True).id for _ in range(5)]
    state: dict[str, int] = {}
    policy = Policy()
    plan = await plan_for(client, policy, state)
    scan_to = plan.targets[0].reaction_scan_to
    outcome = await run(client, plan, plan.targets, policy, sleeper, state, should_stop=lambda: client.reactions_sent >= 2)
    assert outcome.report.reactions == 2
    assert [t.reaction_ids for t in outcome.remaining] == [reacted[2:]]
    assert state == {}  # not moved: three of my reactions are still before the scan position

    rest = await run(client, plan, outcome.remaining, policy, sleeper, state)
    assert rest.report.reactions == 3
    assert state == {str(FIRST): scan_to}


async def test_waits_show_in_progress_and_pauses_do_not(sleeper):
    client, a, b, ids_a, ids_b = two_chats(first=3, second=0)
    client.flood_once["delete_messages"] = 30
    policy = Policy(include_reactions=False)
    plan = await plan_for(client, policy)
    seen: list[ApplyProgress] = []
    await run(client, plan, plan.targets, policy, sleeper, progress=seen.append)
    assert [p.wait_s for p in seen] == [0.0, 31, 0.0, 0.0]
    assert seen[1].done == 0 and seen[-1].done == 3
    assert sleeper.calls == [31]  # one chunk: no pause after it


async def test_pause_between_requests_is_not_a_wait(sleeper):
    client, a, b, ids_a, ids_b = two_chats(first=150, second=0)
    policy = Policy(include_reactions=False)
    plan = await plan_for(client, policy)
    seen: list[ApplyProgress] = []
    await run(client, plan, plan.targets, policy, sleeper, pause=4.0, progress=seen.append)
    assert sleeper.calls == [4.0]
    assert all(p.wait_s == 0 for p in seen)


async def test_duplicate_ids_are_sent_once(sleeper):
    client, a, b, ids_a, ids_b = two_chats(first=3, second=0)
    policy = Policy(max_messages_per_run=2, include_reactions=False)
    plan = await plan_for(client, policy)
    plan.targets[0].message_ids = ids_a + ids_a
    seen: list[ApplyProgress] = []
    outcome = await run(client, plan, plan.targets, policy, sleeper, progress=seen.append)
    assert deleted_ids(client) == ids_a and outcome.remaining == []
    assert [len(ids) for _, ids in client.delete_calls] == [2, 1]
    assert outcome.report.missing == 0 and (seen[-1].done, seen[-1].total) == (3, 3)


# --- search: progress, stats and remembered positions --------------------------


async def test_search_reports_each_step():
    client, a, b, ids_a, ids_b = two_chats(first=2, second=1)
    seen = []
    await plan_for(client, Policy(), progress=seen.append)
    steps = [(p.phase, p.chat, p.chats, p.title, p.read, p.found) for p in seen]
    assert steps[0] == ("dialogs", 0, 0, "", 0, 0)
    assert ("messages", 1, 2, "First", 0, 0) in steps
    assert ("messages", 1, 2, "First", 2, 2) in steps
    assert ("reactions", 1, 2, "First", 2, 2) in steps
    assert ("messages", 2, 2, "Second", 1, 3) in steps


async def test_search_counts_where_time_went():
    client, a, b, ids_a, ids_b = two_chats(first=2, second=0)
    ticks = iter([0.0, 5.0, 5.0, 7.0, 7.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    plan = await plan_for(client, Policy(), clock=lambda: next(ticks))
    s = plan.stats
    assert (s.seconds_dialogs, s.seconds_messages, s.seconds_reactions, s.seconds) == (5.0, 2.0, 3.0, 10.0)
    assert (s.dialogs, s.chats, s.own_found, s.history_read) == (4, 2, 2, 2)  # Saved Messages and 777000 are listed, then skipped


async def test_search_counts_and_shows_flood_waits(sleeper):
    client, a, b, ids_a, ids_b = two_chats(first=3, second=0)
    client.flood_after["iter_messages"] = (1, 20)
    seen = []
    plan = await plan_for(client, Policy(), sleep=sleeper, progress=seen.append)
    assert (plan.stats.flood_waits, plan.stats.flood_wait_s) == (1, 21)
    assert sleeper.calls == [21]
    waiting = [i for i, p in enumerate(seen) if p.phase == "waiting"]
    assert len(waiting) == 1 and seen[waiting[0]].wait_s == 21
    after = seen[waiting[0] + 1]
    assert (after.phase, after.wait_s) == ("messages", 0.0)
    assert sorted(plan.targets[0].message_ids) == ids_a


def test_stats_log_line():
    s = ScanStats(dialogs=40, chats=12, own_found=3400, history_read=51000, flood_waits=2, flood_wait_s=95,
                  seconds_dialogs=3, seconds_messages=40, seconds_reactions=500)
    assert s.as_log() == (
        "543s: dialogs 40 (3s), own messages 3400 in 12 chats (40s), reaction scan read 51000 (500s), "
        "flood waits 2 (95s)"
    )


async def test_quiet_chats_keep_their_scan_position_and_the_next_search_skips_read_history():
    client = FakeClient()
    liked = client.add_channel(100, "Liked")
    quiet = client.add_channel(101, "Quiet")
    reacted = client.add_message(liked, OTHER, "x", date=OLD, my_reaction=True).id
    for _ in range(4):
        client.add_message(quiet, OTHER, "y", date=OLD)
    state: dict[str, int] = {}
    first = await plan_for(client, Policy(), state)
    keep_empty_scans(first, state)
    quiet_target = next(t for t in first.targets if t.title == "Quiet")
    assert state == {str(SECOND): quiet_target.reaction_scan_to}  # the chat with my reaction is not moved

    again = await plan_for(client, Policy(), state)
    assert next(t for t in again.targets if t.title == "Liked").reaction_ids == [reacted]
    assert again.stats.history_read == 1  # only the chat that still has a reaction is read again


# --- a failed reaction survives a stop and going on -----------------------------


async def test_failed_reaction_is_found_again_after_stop_and_going_on(sleeper):
    client = CountingReactions()
    group = client.add_channel(100, "G")
    reacted = [client.add_message(group, OTHER, "x", date=OLD, my_reaction=True).id for _ in range(5)]
    client.fail_once = {reacted[0]}
    state: dict[str, int] = {}
    policy = Policy()
    plan = await plan_for(client, policy, state)
    outcome = await run(client, plan, plan.targets, policy, sleeper, state, should_stop=lambda: client.reactions_sent >= 2)
    assert [t.reaction_ids for t in outcome.remaining] == [reacted[2:]]
    assert [t.scan_held for t in outcome.remaining] == [True]

    rest = await run(client, plan, outcome.remaining, policy, sleeper, state)
    assert rest.report.reactions == 3
    assert state == {}  # not moved past the reaction that failed
    again = await plan_for(client, Policy(), state)
    assert again.targets[0].reaction_ids == [reacted[0]]


async def test_failed_reaction_is_found_again_when_only_messages_were_left(sleeper):
    client = CountingReactions()
    group = client.add_channel(100, "G")
    mine = [client.add_message(group, ME, "m", date=OLD).id for _ in range(6)]
    liked = client.add_message(group, OTHER, "x", date=OLD, my_reaction=True).id
    client.fail_once = {liked}
    state: dict[str, int] = {}
    policy = Policy(max_messages_per_run=4)
    plan = await plan_for(client, policy, state)
    # The first batch takes 4 messages and the reaction, which fails; the stop comes before the second.
    outcome = await run(client, plan, plan.targets, policy, sleeper, state, should_stop=lambda: client.reactions_sent >= 1)
    assert [(t.message_ids, t.reaction_ids, t.scan_held) for t in outcome.remaining] == [(mine[4:], [], True)]

    await run(client, plan, outcome.remaining, policy, sleeper, state)
    assert client.ids_in(group) == [liked]
    assert state == {}
    again = await plan_for(client, Policy(), state)
    assert again.targets[0].reaction_ids == [liked]


async def test_a_later_batch_of_a_held_chat_does_not_move_its_scan(sleeper):
    """Inside one run: once a reaction failed, later batches of that chat leave the position alone."""
    client = CountingReactions()
    group = client.add_channel(100, "G")
    reacted = [client.add_message(group, OTHER, "x", date=OLD, my_reaction=True).id for _ in range(4)]
    client.fail_once = {reacted[0]}
    state: dict[str, int] = {}
    seen: list[dict[str, int]] = []
    policy = Policy(max_reactions_per_run=2)
    plan = await plan_for(client, policy, state)
    await run(client, plan, plan.targets, policy, sleeper, state, save_state=lambda: seen.append(dict(state)))
    assert seen == [{}, {}]
    assert (await plan_for(client, Policy(), state)).targets[0].reaction_ids == [reacted[0]]


async def test_progress_counts_every_reaction(sleeper):
    client = FakeClient()
    group = client.add_channel(100, "G")
    for _ in range(3):
        client.add_message(group, OTHER, "x", date=OLD, my_reaction=True)
    policy = Policy()
    plan = await plan_for(client, policy)
    seen: list[ApplyProgress] = []
    await run(client, plan, plan.targets, policy, sleeper, progress=seen.append)
    assert [(p.done, p.total) for p in seen] == [(0, 3), (1, 3), (2, 3), (3, 3)]


async def test_a_zero_reaction_batch_cannot_loop(sleeper, monkeypatch):
    """Reactions confirmed under one policy, applied under a zero batch: the run ends and says what is left."""
    from tgsweep import cleanup

    client = FakeClient()
    group = client.add_channel(100, "G")
    liked = client.add_message(group, OTHER, "x", date=OLD, my_reaction=True).id
    plan = await plan_for(client, Policy())
    real, tries = cleanup.narrowed, []

    def counted(*args):
        # An empty batch never awaits anything, so a loop over it would hang the test instead of failing it.
        tries.append(1)
        assert len(tries) < 5, "the same empty batch again and again"
        return real(*args)

    monkeypatch.setattr(cleanup, "narrowed", counted)
    outcome = await run(client, plan, plan.targets, Policy(max_reactions_per_run=0), sleeper)
    assert len(tries) == 1
    assert [t.reaction_ids for t in outcome.remaining] == [[liked]] and not outcome.report.stopped


async def test_a_stop_ends_the_run_even_if_asked_only_once(sleeper):
    """The stop is final: the batches do not start again when the flag is read as clear later."""
    client, a, b, ids_a, ids_b = two_chats()
    policy = Policy(max_messages_per_run=5, include_reactions=False)
    plan = await plan_for(client, policy)
    asked = {"stop": False}
    real = client.delete_messages

    async def delete_then_ask(*args, **kw):
        asked["stop"] = True
        return await real(*args, **kw)

    def once():
        stop, asked["stop"] = asked["stop"], False
        return stop

    client.delete_messages = delete_then_ask
    outcome = await run(client, plan, plan.targets, policy, sleeper, should_stop=once)
    assert outcome.report.stopped and deleted_ids(client) == ids_a[:5]
    assert outcome.left == 8
