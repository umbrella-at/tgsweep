"""Cleanup of the owner's own messages, comments and reactions.

The caller builds a plan, the owner picks chats, and only then apply_in_batches (or apply_plan
for one batch) runs. delete_own() is the single place that calls delete_messages; a static test
enforces that.
"""

from __future__ import annotations

import asyncio
import dataclasses
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from telethon import errors, utils
from telethon.tl import functions, types

from . import guards
from .errors import ConfigError
from .journal import Journal
from .net import SESSION_LOST_ERRORS, flood_retry, linked_chat

Sleep = Callable[[float], Awaitable[None]]
ShouldStop = Callable[[], bool]
# Called with the number of items just handled: a chunk of messages, or one reaction.
Progress = Callable[[int], None]


def _never() -> bool:
    return False


PLAN_TTL = timedelta(minutes=15)
# A plan the daily check sends: the owner may see it hours later, and nothing goes without their ticks.
OFFER_TTL = timedelta(hours=12)
DELETE_CHUNK = 100


class PlanExpired(Exception):
    """The plan is older than PLAN_TTL when deletion was about to start."""


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    older_than_days: int = Field(default=2, ge=0)
    # Kept only so that old policy files still load: private chats are never cleaned.
    include_dms: bool = False
    include_comments: bool = True
    include_reactions: bool = True
    reactions_window_days: int = Field(default=90, ge=1)
    reactions_scan_cap: int = Field(default=20000, ge=100)
    include: list[int] = Field(default_factory=list)
    exclude: list[int] = Field(default_factory=list)
    # Test mode: when not empty, nothing outside these chat ids is ever touched.
    only: list[int] = Field(default_factory=list)
    # Per-run limits keep a first cleanup of years of history from becoming a burst of requests:
    # the rest is picked up by the next runs.
    # Batch sizes: apply_in_batches goes through everything confirmed, this many at a time.
    max_messages_per_run: int = Field(default=1000, ge=1)
    max_reactions_per_run: int = Field(default=300, ge=0)
    pause_s: float = Field(default=4.0, ge=0)  # between delete requests (a chunk of up to 100 or one reaction)

    @model_validator(mode="after")
    def _zero_only_in_test_mode(self) -> "Policy":
        if self.include_dms:
            raise ValueError("include_dms: private chats are never cleaned, remove the line or set it to false")
        if self.older_than_days == 0 and not self.only:
            raise ValueError("older_than_days: 0 is allowed only together with the 'only' test list")
        return self

    def effective_retention(self, bot_choice: int) -> int:
        """In test mode the policy value wins over the retention the owner picked."""
        return self.older_than_days if self.only else bot_choice


def load_policy(path: Path) -> Policy:
    if not path.exists():
        return Policy()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Policy(**data)
    except (yaml.YAMLError, TypeError, ValidationError) as exc:
        raise ConfigError(f"{path} is invalid: {exc}") from None


# --- selection ---------------------------------------------------------------


def skip_reason(entity, me_id: int, policy: Policy) -> str | None:
    """None if the chat is a cleanup target, otherwise the reason it is skipped."""
    if guards.is_saved_messages(entity, me_id) or guards.is_service_chat(entity) or guards.is_secret_chat(entity):
        return "forbidden"
    chat_id = guards.peer_id(entity)
    if policy.only and chat_id not in policy.only:
        return "not_in_only"
    if chat_id in policy.exclude:
        return "excluded"
    kind = guards.chat_kind(entity)
    if kind in ("dm", "bot", "monoforum"):
        return "dm"
    if kind == "other":
        return "unsupported"
    included = chat_id in policy.include
    if kind == "broadcast" and not included:
        return "broadcast"
    if guards.is_creator(entity) and not included:
        return "creator"
    return None


# --- plan --------------------------------------------------------------------


@dataclass
class ScanStats:
    """How long the search took and where the time went. Counts only, no titles or texts."""

    dialogs: int = 0
    chats: int = 0
    own_found: int = 0
    history_read: int = 0  # messages read by the reaction scan
    flood_waits: int = 0  # waits Telegram asked for that reached tgsweep (short ones Telethon sleeps itself)
    flood_wait_s: float = 0.0
    seconds_dialogs: float = 0.0
    seconds_messages: float = 0.0
    seconds_reactions: float = 0.0

    @property
    def seconds(self) -> float:
        return self.seconds_dialogs + self.seconds_messages + self.seconds_reactions

    def as_log(self) -> str:
        return (
            f"{self.seconds:.0f}s: dialogs {self.dialogs} ({self.seconds_dialogs:.0f}s), "
            f"own messages {self.own_found} in {self.chats} chats ({self.seconds_messages:.0f}s), "
            f"reaction scan read {self.history_read} ({self.seconds_reactions:.0f}s), "
            f"flood waits {self.flood_waits} ({self.flood_wait_s:.0f}s)"
        )


@dataclass
class ScanProgress:
    """What the search is doing right now, for a status line."""

    phase: str  # dialogs, messages, reactions, waiting
    chat: int = 0  # 1-based index of the chat being scanned
    chats: int = 0
    title: str = ""
    read: int = 0  # dialogs listed, or messages read in this chat and phase
    found: int = 0  # own messages found so far, all chats
    wait_s: float = 0.0


@dataclass
class Target:
    entity: object
    chat_id: int
    title: str
    kind: str  # dm, bot, group, megagroup, broadcast, comments
    message_ids: list[int] = field(default_factory=list)
    reaction_ids: list[int] = field(default_factory=list)
    reaction_scan_to: int | None = None
    # A reaction in this chat could not be removed earlier in this deletion: the scan position stays
    # where it was, so the next search finds that reaction again. Carried over when a stopped run goes on.
    scan_held: bool = False


@dataclass
class Plan:
    plan_id: str
    created_at: datetime
    retention_days: int
    cutoff: datetime
    targets: list[Target]
    skipped: list[tuple[int, str, str]] = field(default_factory=list)
    deferred_messages: int = 0  # over the per-run limit, left for the next runs
    deferred_reactions: int = 0
    ttl: timedelta = PLAN_TTL
    stats: ScanStats = field(default_factory=lambda: ScanStats())

    @property
    def expires_at(self) -> datetime:
        return self.created_at + self.ttl

    def expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    @property
    def messages(self) -> int:
        return sum(len(t.message_ids) for t in self.targets if t.kind != "comments")

    @property
    def comments(self) -> int:
        return sum(len(t.message_ids) for t in self.targets if t.kind == "comments")

    @property
    def reactions(self) -> int:
        return sum(len(t.reaction_ids) for t in self.targets)

    @property
    def total(self) -> int:
        return self.messages + self.comments + self.reactions


async def collect_own_ids(
    client, entity, cutoff: datetime, *, sleep: Sleep, by_out: bool = False, max_retries: int = 10,
    on_read: Callable[[int], None] | None = None,
) -> list[int]:
    """Ids of my regular messages older than cutoff. After FloodWait the search resumes below the oldest id seen.

    Only ids are kept, so a chat with a hundred thousand of my messages fits in a small memory limit.
    Channel posts carry no sender, so for a channel (by_out) the whole older history is read and
    only posts with the outgoing flag are kept instead of asking the server to filter by sender.
    """
    ids: set[int] = set()
    last: int | None = None
    attempt = 0
    while True:
        kwargs: dict = {"offset_date": cutoff}
        if not by_out:
            kwargs["from_user"] = "me"
        if last is not None:
            kwargs["offset_id"] = last
        try:
            async for msg in client.iter_messages(entity, **kwargs):
                last = msg.id if last is None else min(last, msg.id)
                if isinstance(msg, types.Message) and msg.date < cutoff and (msg.out or not by_out):
                    ids.add(msg.id)
                    if on_read is not None:
                        on_read(len(ids))
            return sorted(ids)
        except errors.FloodWaitError as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            await sleep(exc.seconds + 1)


def _has_my_reaction(reactions) -> bool:
    return bool(reactions and any(rc.chosen_order is not None for rc in (reactions.results or [])))


async def scan_reactions(
    client,
    entity,
    *,
    start_after: int | None,
    window_start: datetime,
    cutoff: datetime,
    cap: int,
    sleep: Sleep,
    max_retries: int = 10,
    on_read: Callable[[int], None] | None = None,
) -> tuple[list[int], int | None]:
    """Find messages older than cutoff that carry my reaction. Returns (ids, last scanned id).

    Streams the history in ascending order and keeps only ids; after FloodWait it resumes from the last id seen.
    """
    mine: set[int] = set()
    need_full: list[int] = []
    last = start_after
    scanned = 0
    attempt = 0
    while True:
        kwargs: dict = {"reverse": True}
        if last:
            kwargs["min_id"] = last
        else:
            kwargs["offset_date"] = window_start
        try:
            async for msg in client.iter_messages(entity, **kwargs):
                if msg.date >= cutoff or scanned >= cap:
                    break
                scanned += 1
                last = msg.id
                if on_read is not None:
                    on_read(scanned)
                reactions = getattr(msg, "reactions", None)
                if _has_my_reaction(reactions):
                    mine.add(msg.id)
                elif reactions is not None and getattr(reactions, "min", False) and reactions.results:
                    need_full.append(msg.id)
            break
        except errors.FloodWaitError as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            await sleep(exc.seconds + 1)
    for start in range(0, len(need_full), 100):
        chunk = need_full[start : start + 100]
        result = await flood_retry(
            lambda: client(functions.messages.GetMessagesReactionsRequest(peer=entity, id=chunk)), sleep=sleep
        )
        for upd in getattr(result, "updates", []) or []:
            if isinstance(upd, types.UpdateMessageReactions) and _has_my_reaction(upd.reactions):
                mine.add(upd.msg_id)
    return sorted(mine), last


async def build_plan(
    client,
    me_id: int,
    policy: Policy,
    *,
    retention_days: int,
    now: datetime,
    reaction_state: dict[str, int],
    sleep: Sleep = asyncio.sleep,
    progress: Callable[[ScanProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Plan:
    """progress gets a ScanProgress on every step of the search; the plan carries ScanStats."""
    cutoff = now - timedelta(days=retention_days)
    window_start = now - timedelta(days=policy.reactions_window_days)
    targets: dict[int, Target] = {}
    skipped: list[tuple[int, str, str]] = []
    stats = ScanStats()
    current = ScanProgress("dialogs")

    def tell(**changes) -> None:
        nonlocal current
        current = dataclasses.replace(current, **changes)
        if progress is not None:
            progress(current)

    async def waited(seconds: float) -> None:
        """Every FloodWait that reaches tgsweep sleeps through here: counted, and shown while it lasts."""
        stats.flood_waits += 1
        stats.flood_wait_s += seconds
        phase = current.phase
        tell(phase="waiting", wait_s=seconds)
        await sleep(seconds)
        tell(phase=phase, wait_s=0.0)

    started = clock()
    tell()
    async for dialog in client.iter_dialogs():
        stats.dialogs += 1
        tell(read=stats.dialogs)
        entity = dialog.entity
        chat_id = guards.peer_id(entity)
        kind = guards.chat_kind(entity)
        reason = skip_reason(entity, me_id, policy)
        # The discussion group decides for itself through skip_reason, including the 'only' list.
        if kind == "broadcast" and policy.include_comments and reason not in ("excluded", "forbidden"):
            _, linked = await linked_chat(client, entity, sleep=waited)
            if linked is not None:
                linked_id = guards.peer_id(linked)
                linked_reason = skip_reason(linked, me_id, policy)
                if linked_reason is None and linked_id not in targets:
                    targets[linked_id] = Target(linked, linked_id, guards.display_title(entity), "comments")
                elif linked_reason is not None:
                    skipped.append((linked_id, guards.display_title(linked), linked_reason))
        if reason is not None:
            skipped.append((chat_id, guards.display_title(entity), reason))
            continue
        existing = targets.get(chat_id)
        if existing is not None:
            existing.kind = kind
            existing.title = guards.display_title(entity)
        else:
            targets[chat_id] = Target(entity, chat_id, guards.display_title(entity), kind)

    stats.seconds_dialogs = clock() - started
    stats.chats = len(targets)
    for index, target in enumerate(targets.values(), start=1):
        tell(phase="messages", chat=index, chats=len(targets), title=target.title, read=0)
        began = clock()
        found_before = stats.own_found
        target.message_ids = await collect_own_ids(
            client, target.entity, cutoff, sleep=waited, by_out=target.kind == "broadcast",
            on_read=lambda n: tell(read=n, found=found_before + n),
        )
        stats.own_found += len(target.message_ids)
        stats.seconds_messages += clock() - began
        # A zero reaction batch means reactions stay: they are not even searched, so none is confirmed in vain.
        if (
            policy.include_reactions
            and policy.max_reactions_per_run > 0
            and target.kind in ("group", "megagroup", "dm", "bot", "monoforum")
        ):
            tell(phase="reactions", read=0, found=stats.own_found)
            began = clock()
            read_before = stats.history_read

            def on_scanned(n: int) -> None:
                stats.history_read = read_before + n
                tell(read=n)

            target.reaction_ids, target.reaction_scan_to = await scan_reactions(
                client,
                target.entity,
                start_after=reaction_state.get(str(target.chat_id)),
                window_start=window_start,
                cutoff=cutoff,
                cap=policy.reactions_scan_cap,
                sleep=waited,
                on_read=on_scanned,
            )
            stats.seconds_reactions += clock() - began

    # No per-run limit here: the owner picks chats first, and the limit applies to the picked ones (narrowed).
    return Plan(
        plan_id=secrets.token_urlsafe(8),
        created_at=now,
        retention_days=retention_days,
        cutoff=cutoff,
        targets=[t for t in targets.values() if t.message_ids or t.reaction_ids or t.reaction_scan_to],
        skipped=skipped,
        stats=stats,
    )


def keep_empty_scans(plan: Plan, reaction_state: dict[str, int]) -> None:
    """Chats whose reaction scan found nothing keep its position, ticked or not: the next search starts there.

    A chat where reactions were found keeps its old position until they are removed.
    """
    for t in plan.targets:
        if t.reaction_scan_to and not t.reaction_ids:
            reaction_state[str(t.chat_id)] = t.reaction_scan_to


def narrowed(plan: Plan, targets: list[Target], policy: Policy) -> Plan:
    """The plan for these targets only, trimmed to the per-run limits.

    Targets are copied, so trimming never shortens the plan the owner is still looking at.
    """
    copies = [dataclasses.replace(t, message_ids=list(t.message_ids), reaction_ids=list(t.reaction_ids)) for t in targets]
    deferred_m, deferred_r = cap_targets(copies, policy.max_messages_per_run, policy.max_reactions_per_run)
    return dataclasses.replace(plan, targets=copies, deferred_messages=deferred_m, deferred_reactions=deferred_r)


def cap_targets(targets: list[Target], max_messages: int, max_reactions: int) -> tuple[int, int]:
    """Trim the plan to the per-run limits, oldest first in each chat. Returns what was left for later runs.

    Messages are collected again on every run, so trimming them is enough. Reactions are found by a scan
    that remembers its position: when some are left out, the position moves back to just before the first
    one left out, so the next run finds them again.
    """
    room, deferred_m = max_messages, 0
    for t in targets:
        keep = t.message_ids[:room]
        deferred_m += len(t.message_ids) - len(keep)
        t.message_ids = keep
        room -= len(keep)
    room, deferred_r = max_reactions, 0
    for t in targets:
        if len(t.reaction_ids) > room:
            first_left_out = t.reaction_ids[room]
            deferred_r += len(t.reaction_ids) - room
            t.reaction_ids = t.reaction_ids[:room]
            t.reaction_scan_to = first_left_out - 1
        room -= len(t.reaction_ids)
    return deferred_m, deferred_r


# --- deletion ----------------------------------------------------------------


@dataclass
class DeleteReport:
    deleted: list[int] = field(default_factory=list)
    refused: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    stopped: bool = False


async def delete_own(
    client,
    entity,
    ids,
    *,
    me_id: int,
    journal: Journal,
    sleep: Sleep = asyncio.sleep,
    chunk_size: int = DELETE_CHUNK,
    pause: float = 1.0,
    run_id: str | None = None,
    should_stop: ShouldStop = _never,
    progress: Progress | None = None,
    wait: Sleep | None = None,
) -> DeleteReport:
    """Delete only my own regular messages in this exact chat.

    Every id is fetched again from this chat. In private chats and basic groups
    message ids are global for the account and DeleteMessagesRequest carries no
    peer, so a message that does not belong to this chat is refused even if
    Telegram returned it. Service messages (joins, pins, forum topics) are refused.
    Each id is journaled as 'deleting' before the request, so a crash leaves a trace.
    sleep makes the pause between requests, wait sleeps through FloodWait (sleep when not given).
    """
    guards.assert_readable(entity, me_id)  # groups and channels only: never private chats
    wait = wait or sleep
    chat_id = utils.get_peer_id(entity)
    unique = list(dict.fromkeys(int(i) for i in ids))
    report = DeleteReport()

    def note(msg_id: int, result: str, error: str | None = None, date=None) -> None:
        journal.write(
            chat_id=chat_id,
            msg_id=msg_id,
            date=date.isoformat() if date else None,
            action="delete",
            result=result,
            error=error,
            run_id=run_id,
        )

    for start in range(0, len(unique), chunk_size):
        if should_stop():
            report.stopped = True
            break
        chunk = unique[start : start + chunk_size]
        try:
            fetched = await flood_retry(lambda: client.get_messages(entity, ids=chunk), sleep=wait)
        except SESSION_LOST_ERRORS:
            raise
        except errors.RPCError as exc:
            # The chat itself is out of reach (left, banned, made private): the rest of it fails, other chats go on.
            for msg_id in unique[start:]:
                report.failed.append(msg_id)
                note(msg_id, "failed", exc.__class__.__name__)
            if progress is not None:
                progress(len(unique) - start)
            break
        # Match by id: Telegram may omit invalid ids, so positions are not reliable.
        by_id = {m.id: m for m in fetched if m is not None and not isinstance(m, types.MessageEmpty)}
        own: list[int] = []
        dates: dict[int, datetime] = {}
        for msg_id in chunk:
            msg = by_id.get(msg_id)
            if msg is None:
                report.missing.append(msg_id)
                note(msg_id, "missing")
                continue
            if msg.peer_id is None or utils.get_peer_id(msg.peer_id) != chat_id:
                report.refused.append(msg_id)
                note(msg_id, "refused", "other_chat")
                continue
            if not isinstance(msg, types.Message):
                report.refused.append(msg_id)
                note(msg_id, "refused", "service", msg.date)
                continue
            if not (msg.out or msg.sender_id == me_id):
                report.refused.append(msg_id)
                note(msg_id, "refused", "not_own", msg.date)
                continue
            own.append(msg_id)
            dates[msg_id] = msg.date
        if own:
            for msg_id in own:
                note(msg_id, "deleting", None, dates[msg_id])
            try:
                await flood_retry(lambda: client.delete_messages(entity, own, revoke=True), sleep=wait)
            except SESSION_LOST_ERRORS:
                raise
            except errors.RPCError as exc:
                for msg_id in own:
                    report.failed.append(msg_id)
                    note(msg_id, "failed", exc.__class__.__name__, dates[msg_id])
            else:
                for msg_id in own:
                    report.deleted.append(msg_id)
                    note(msg_id, "deleted", None, dates[msg_id])
        if progress is not None:
            progress(len(chunk))
        if start + chunk_size < len(unique):
            await sleep(pause)
    return report


# --- apply -------------------------------------------------------------------


@dataclass
class ChatResult:
    chat_id: int
    title: str
    kind: str
    deleted: int = 0
    refused: int = 0
    missing: int = 0
    failed: int = 0
    unreacted: int = 0
    unreact_failed: int = 0


@dataclass
class ApplyReport:
    plan_id: str
    chats: list[ChatResult] = field(default_factory=list)
    stopped: bool = False

    @property
    def messages(self) -> int:
        return sum(c.deleted for c in self.chats if c.kind != "comments")

    @property
    def comments(self) -> int:
        return sum(c.deleted for c in self.chats if c.kind == "comments")

    @property
    def reactions(self) -> int:
        return sum(c.unreacted for c in self.chats)

    @property
    def refused(self) -> int:
        return sum(c.refused for c in self.chats)

    @property
    def missing(self) -> int:
        return sum(c.missing for c in self.chats)

    @property
    def failed(self) -> int:
        return sum(c.failed + c.unreact_failed for c in self.chats)

    def as_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "messages": self.messages,
            "comments": self.comments,
            "reactions": self.reactions,
            "refused": self.refused,
            "missing": self.missing,
            "failed": self.failed,
            "stopped": self.stopped,
        }


async def remove_my_reaction(client, entity, msg_id: int, *, sleep: Sleep) -> None:
    """An empty reaction list removes only my own reactions on the message."""
    await flood_retry(
        lambda: client(functions.messages.SendReactionRequest(peer=entity, msg_id=msg_id, reaction=[])), sleep=sleep
    )


async def apply_plan(
    client,
    plan: Plan,
    *,
    me_id: int,
    journal: Journal,
    reaction_state: dict[str, int],
    sleep: Sleep = asyncio.sleep,
    pause: float = 1.0,
    should_stop: ShouldStop = _never,
    progress: Callable[[Target, int], None] | None = None,
    wait: Sleep | None = None,
) -> ApplyReport:
    """One batch. Session loss propagates; a stop request ends the run between requests and keeps the report.

    progress(target, n) follows every handled chunk of messages and every reaction. The report has one
    row per target in plan order, up to the target where a stop came.
    """
    report = ApplyReport(plan.plan_id)
    wait = wait or sleep

    def handled(target: Target) -> Progress | None:
        return None if progress is None else (lambda n: progress(target, n))

    for target in plan.targets:
        guards.assert_readable(target.entity, me_id)  # reactions too: a private chat is never touched
    for target in plan.targets:
        if should_stop():
            report.stopped = True
            break
        result = ChatResult(target.chat_id, target.title, target.kind)
        report.chats.append(result)
        if target.message_ids:
            rep = await delete_own(
                client,
                target.entity,
                target.message_ids,
                me_id=me_id,
                journal=journal,
                sleep=sleep,
                pause=pause,
                run_id=plan.plan_id,
                should_stop=should_stop,
                progress=handled(target),
                wait=wait,
            )
            result.deleted, result.refused = len(rep.deleted), len(rep.refused)
            result.missing, result.failed = len(rep.missing), len(rep.failed)
            if rep.stopped:
                report.stopped = True
                break
        for msg_id in target.reaction_ids:
            if should_stop():
                report.stopped = True
                break
            try:
                await remove_my_reaction(client, target.entity, msg_id, sleep=wait)
            except SESSION_LOST_ERRORS:
                raise
            except errors.RPCError as exc:
                result.unreact_failed += 1
                journal.write(
                    chat_id=target.chat_id, msg_id=msg_id, action="unreact", result="failed",
                    error=exc.__class__.__name__, run_id=plan.plan_id,
                )
            else:
                result.unreacted += 1
                journal.write(chat_id=target.chat_id, msg_id=msg_id, action="unreact", result="done", run_id=plan.plan_id)
            if progress is not None:
                progress(target, 1)
            await sleep(pause)
        if report.stopped:
            break
        # Move the scan position only when every reaction was removed, otherwise they would be lost for good.
        if target.reaction_scan_to and result.unreact_failed == 0 and not target.scan_held:
            reaction_state[str(target.chat_id)] = target.reaction_scan_to
    return report


@dataclass
class ApplyProgress:
    """Where the deletion is, for a status line. Messages, comments and reactions count alike."""

    done: int
    total: int
    chat: str = ""
    wait_s: float = 0.0  # Telegram asked to wait this long and tgsweep is waiting now


@dataclass
class BatchOutcome:
    report: ApplyReport  # every batch together, one row per chat
    remaining: list[Target]  # confirmed but not reached because of a stop, oldest ids first

    @property
    def left(self) -> int:
        return work(self.remaining)


def work(targets: list[Target]) -> int:
    return sum(len(t.message_ids) + len(t.reaction_ids) for t in targets)


def merge_reports(plan_id: str, reports: list[ApplyReport]) -> ApplyReport:
    merged = ApplyReport(plan_id, stopped=any(r.stopped for r in reports))
    rows: dict[int, ChatResult] = {}
    for report in reports:
        for c in report.chats:
            row = rows.get(c.chat_id)
            if row is None:
                rows[c.chat_id] = row = ChatResult(c.chat_id, c.title, c.kind)
                merged.chats.append(row)
            row.deleted += c.deleted
            row.refused += c.refused
            row.missing += c.missing
            row.failed += c.failed
            row.unreacted += c.unreacted
            row.unreact_failed += c.unreact_failed
    return merged


async def apply_in_batches(
    client,
    plan: Plan,
    chosen: list[Target],
    policy: Policy,
    *,
    me_id: int,
    journal: Journal,
    reaction_state: dict[str, int],
    save_state: Callable[[], None] = lambda: None,
    sleep: Sleep = asyncio.sleep,
    pause: float | None = None,
    should_stop: ShouldStop = _never,
    progress: Callable[[ApplyProgress], None] | None = None,
) -> BatchOutcome:
    """Everything confirmed in one go: the per-run limits only cut it into batches, which follow each other.

    apply_plan moves a chat's reaction scan position when its batch removed every reaction. A later
    batch of the same chat would then move it past a reaction that failed earlier, so a chat with a
    failure is held (Target.scan_held) for the rest of this run and for a run that goes on with
    `remaining`: its position stays where it was, and the next search finds that reaction again.
    A position moved by an earlier batch is safe: reaction ids are sorted, and a cut batch moves it only
    to just before the first reaction left out (cap_targets).
    save_state runs after every batch, also when one fails, so the positions on disk follow the deletions.
    """
    pause = policy.pause_s if pause is None else pause
    remaining = [
        dataclasses.replace(t, message_ids=list(dict.fromkeys(t.message_ids)), reaction_ids=list(dict.fromkeys(t.reaction_ids)))
        for t in chosen
    ]
    current = ApplyProgress(0, work(remaining))
    failed: set[str] = set()
    reports: list[ApplyReport] = []

    def tell(**changes) -> None:
        nonlocal current
        current = dataclasses.replace(current, **changes)
        if progress is not None:
            progress(current)

    def on_handled(target: Target, n: int) -> None:
        title = f"{target.title} (комментарии)" if target.kind == "comments" else target.title
        tell(done=current.done + n, chat=title)

    async def waited(seconds: float) -> None:
        tell(wait_s=seconds)
        try:
            await sleep(seconds)
        finally:
            tell(wait_s=0.0)

    tell()
    while remaining:
        batch = narrowed(plan, remaining, policy)
        report = None
        try:
            report = await apply_plan(
                client, batch, me_id=me_id, journal=journal, reaction_state=reaction_state, sleep=sleep,
                pause=pause, should_stop=should_stop, progress=on_handled, wait=waited,
            )
        finally:
            if report is not None:
                failed.update(str(c.chat_id) for c in report.chats if c.unreact_failed)
            save_state()
        reports.append(report)
        handled_now = 0
        for i, rest in enumerate(remaining):
            row = report.chats[i] if i < len(report.chats) else None
            if row is None:
                continue
            messages = row.deleted + row.refused + row.missing + row.failed
            reactions = row.unreacted + row.unreact_failed
            rest.message_ids = rest.message_ids[messages:]
            rest.reaction_ids = rest.reaction_ids[reactions:]
            handled_now += messages + reactions
        for rest in remaining:
            if str(rest.chat_id) in failed:
                rest.scan_held = True
        remaining = [t for t in remaining if t.message_ids or t.reaction_ids]
        if report.stopped or handled_now == 0:  # a zero limit would otherwise loop for ever
            break
    return BatchOutcome(merge_reports(plan.plan_id, reports), remaining)
