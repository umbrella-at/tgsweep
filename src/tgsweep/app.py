"""The laptop run: pick how old, pick chats, confirm by typing the count, delete, report.

Everything the user sees goes through a UI object, so tests drive the whole run with a script.
Nothing is deleted before the typed confirmation, and only in the chats the user ticked.
"""

from __future__ import annotations

import asyncio
import math
import signal
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator, Protocol

from . import cleanup
from .cleanup import ApplyProgress, ApplyReport, Plan, Policy, ScanProgress, Target
from .home import Home
from .i18n import plural, t
from .journal import Journal
from .net import Sleep

# (text key, retention in days); 0 means every message, today's included.
RETENTION_CHOICES: list[tuple[str, int]] = [
    ("retention_all", 0),
    ("retention_1", 1),
    ("retention_2", 2),
    ("retention_7", 7),
    ("retention_30", 30),
]

# Why a chat was left alone -> text key.
SKIP_NOTES = {
    "dm": "skip_dm",
    "creator": "skip_creator",
    "broadcast": "skip_broadcast",
    "unsupported": "skip_unsupported",
}


def retention_choices() -> list[tuple[str, int]]:
    return [(t(key), days) for key, days in RETENTION_CHOICES]


def skip_note(reason: str) -> str:
    return t(SKIP_NOTES[reason])


class UI(Protocol):
    """say/ask/ask_secret are plain terminal I/O for sign-in; the questions of the run are async."""

    def say(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...

    def ask_secret(self, prompt: str) -> str: ...

    def progress(self, text: str | None) -> None:
        """A status line that keeps replacing itself while the search or the deletion runs; None removes it."""

    async def choose(self, question: str, choices: list[tuple[str, int]]) -> int | None:
        """The value of the chosen item, None when the user backs out."""

    async def pick(self, question: str, rows: list[str]) -> list[int] | None:
        """Indexes of the ticked rows, none ticked at the start. None when the user backs out."""

    async def type_in(self, prompt: str) -> str | None:
        """A typed line, None when the user backs out."""

    async def yes_no(self, question: str, default: bool) -> bool: ...


def counts(messages: int, comments: int, reactions: int) -> str:
    parts = []
    if messages:
        parts.append(plural(messages, "message"))
    if comments:
        parts.append(plural(comments, "comment"))
    if reactions:
        parts.append(plural(reactions, "reaction"))
    return ", ".join(parts) or t("nothing")


def target_counts(target: Target) -> tuple[int, int, int]:
    n = len(target.message_ids)
    return (0, n, len(target.reaction_ids)) if target.kind == "comments" else (n, 0, len(target.reaction_ids))


def row(target: Target) -> str:
    title = t("title_comments_on_posts", title=target.title) if target.kind == "comments" else target.title
    return f"{title}: {counts(*target_counts(target))}"


def duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return t("seconds", seconds=seconds)
    minutes = round(seconds / 60)
    if minutes < 60:
        return t("minutes", minutes=minutes)
    hours, rest = divmod(minutes, 60)
    return t("hours_minutes", hours=hours, rest=rest) if rest else t("hours", hours=hours)


def scan_line(p: ScanProgress) -> str:
    """What the search is doing, in one line. The chat title goes last: a narrow screen cuts it, not the numbers."""
    if p.phase == "dialogs":
        return t("scan_dialogs", read=p.read)
    if p.phase == "waiting":
        doing = t("waiting", duration=duration(p.wait_s))
    elif p.phase == "reactions":
        doing = t("scan_reactions", read=p.read)
    else:
        doing = t("scan_messages", found=p.found)
    return t("scan_chat", chat=p.chat, chats=p.chats, doing=doing, title=p.title)


def apply_line(p: ApplyProgress) -> str:
    """What the deletion is doing, in one line, the chat title last."""
    percent = p.done * 100 // p.total if p.total else 100
    line = t("deleting", done=p.done, total=p.total, percent=percent)
    if p.wait_s:
        line += " · " + t("waiting", duration=duration(p.wait_s))
    if p.chat:
        line += f" · {p.chat}"
    return line


def estimate_minutes(targets: list[Target], pause_s: float, chunk: int = cleanup.DELETE_CHUNK) -> int:
    requests = sum(math.ceil(len(x.message_ids) / chunk) + len(x.reaction_ids) for x in targets)
    return max(1, math.ceil(requests * pause_s / 60))


class Stopper:
    """Ctrl+C during deletion asks to stop after the current request; a second Ctrl+C stops at once."""

    def __init__(self) -> None:
        self.requested = False
        self._note: asyncio.Handle | None = None

    def __call__(self) -> bool:
        return self.requested

    @contextmanager
    def armed(self, say: Callable[[str], None]) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        previous = signal.getsignal(signal.SIGINT)

        def handler(signum, frame):
            if self.requested:
                raise KeyboardInterrupt
            self.requested = True
            # The handler may interrupt a write to the terminal (the status line redraws all the time),
            # and writing from inside it would then fail as a reentrant call: say it from the loop instead.
            try:
                self._note = asyncio.get_running_loop().call_soon_threadsafe(say, t("stop_note"))
            except RuntimeError:
                say(t("stop_note"))

        signal.signal(signal.SIGINT, handler)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous)
            # Deletion is over: a note still waiting would land inside the next question.
            if self._note is not None:
                self._note.cancel()
                self._note = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def sweep(
    client,
    ui: UI,
    home: Home,
    *,
    policy: Policy | None = None,
    now: Callable[[], datetime] = _utcnow,
    sleep: Sleep = asyncio.sleep,
    stopper: Stopper | None = None,
) -> int:
    """One interactive run. Returns the process exit code."""
    policy = policy or Policy()
    stopper = stopper or Stopper()
    me = await client.get_me()
    if me is None or me.bot:
        ui.say(t("not_personal"))
        return 1
    ui.say(t("account", name=_name(me)))

    retention = await ui.choose(t("which_messages"), retention_choices())
    if retention is None:
        ui.say(t("nothing_deleted"))
        return 0

    ui.say(t("searching"))
    reaction_state = home.load_state()
    try:
        plan = await cleanup.build_plan(
            client, me.id, policy, retention_days=retention, now=now(), reaction_state=reaction_state,
            sleep=sleep, progress=lambda p: ui.progress(scan_line(p)),
        )
    finally:
        ui.progress(None)
    cleanup.keep_empty_scans(plan, reaction_state)
    home.save_state(reaction_state)
    ui.say(t("search_took", duration=duration(plan.stats.seconds), chats=plural(plan.stats.chats, "chat")))
    _say_skipped(ui, plan)

    found = [target for target in plan.targets if target.message_ids or target.reaction_ids]
    if not found:
        if retention == 0:
            ui.say(t("nothing_found"))
        else:
            ui.say(t("nothing_older", days=plural(retention, "day_older")))
        return 0

    picked = await ui.pick(t("pick_chats"), [row(target) for target in found])
    if not picked:
        ui.say(t("none_picked"))
        return 0
    chosen = [found[i] for i in sorted(set(picked))]
    m, c, r = (sum(x) for x in zip(*(target_counts(target) for target in chosen)))
    total = m + c + r
    ui.say(t(
        "will_delete",
        counts=counts(m, c, r),
        chats=plural(len(chosen), "chat_in"),
        minutes=plural(estimate_minutes(chosen, policy.pause_s), "minute"),
    ))
    typed = await ui.type_in(t("confirm_number", total=total))
    if (typed or "").strip() != str(total):
        ui.say(t("number_mismatch"))
        return 0

    journal = Journal(home.journal_file)
    with stopper.armed(ui.say):
        try:
            outcome = await cleanup.apply_in_batches(
                client, plan, chosen, policy, me_id=me.id, journal=journal, reaction_state=reaction_state,
                save_state=lambda: home.save_state(reaction_state), sleep=sleep, should_stop=stopper,
                progress=lambda p: ui.progress(apply_line(p)),
            )
        finally:
            ui.progress(None)
    _say_report(ui, outcome.report, home)
    return 0


def _say_skipped(ui: UI, plan: Plan) -> None:
    by_reason: dict[str, int] = {}
    for _, _, reason in plan.skipped:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    lines = [f"  {skip_note(r)}: {n}" for r, n in by_reason.items() if r in SKIP_NOTES]
    if lines:
        ui.say(t("skipped_header") + "\n" + "\n".join(lines))


def _say_report(ui: UI, report: ApplyReport, home: Home) -> None:
    lines = [t("report_done", messages=report.messages, comments=report.comments, reactions=report.reactions)]
    missing, refused, failed = report.missing, report.refused, report.failed
    if missing:
        lines.append(t("report_missing", missing=missing))
    if refused:
        lines.append(t("report_refused", refused=refused))
    if failed:
        lines.append(t("report_failed", failed=failed))
    if report.stopped:
        lines.append(t("report_stopped"))
    lines.append(t("report_journal", path=home.journal_file))
    ui.say("\n".join(lines))


def _name(me) -> str:
    name = " ".join(p for p in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if p)
    username = getattr(me, "username", None)
    return f"{name} (@{username})" if username else name or str(me.id)
