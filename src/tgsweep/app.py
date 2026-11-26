"""The laptop run: pick how old, pick chats, confirm by typing the count, delete, report.

Everything the user sees goes through a UI object, so tests drive the whole run with a script.
Nothing is deleted before the typed confirmation, and only in the chats the user ticked.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import signal
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator, Protocol

from . import cleanup
from .cleanup import ApplyReport, Plan, Policy, Target
from .home import Home
from .journal import Journal
from .net import Sleep

# (label, retention in days); 0 means every message, today's included.
RETENTION_CHOICES: list[tuple[str, int]] = [
    ("Все, даже сегодняшние", 0),
    ("Старше 1 дня", 1),
    ("Старше 2 дней", 2),
    ("Старше 7 дней", 7),
    ("Старше 30 дней", 30),
]

SKIP_NOTES = {
    "dm": "личные чаты и боты: tgsweep их не открывает",
    "creator": "чаты, которые вы создали: пропущены, чтобы не опустошить свою же группу",
    "broadcast": "каналы: свои посты в каналах удаляйте в самом Telegram, комментарии к постам в плане есть",
    "unsupported": "чаты неизвестного типа",
}


class UI(Protocol):
    """say/ask/ask_secret are plain terminal I/O for sign-in; the questions of the run are async."""

    def say(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...

    def ask_secret(self, prompt: str) -> str: ...

    async def choose(self, question: str, choices: list[tuple[str, int]]) -> int | None:
        """The value of the chosen item, None when the user backs out."""

    async def pick(self, question: str, rows: list[str]) -> list[int] | None:
        """Indexes of the ticked rows, none ticked at the start. None when the user backs out."""

    async def type_in(self, prompt: str) -> str | None:
        """A typed line, None when the user backs out."""

    async def yes_no(self, question: str, default: bool) -> bool: ...


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def counts(messages: int, comments: int, reactions: int) -> str:
    parts = []
    if messages:
        parts.append(plural(messages, "сообщение", "сообщения", "сообщений"))
    if comments:
        parts.append(plural(comments, "комментарий", "комментария", "комментариев"))
    if reactions:
        parts.append(plural(reactions, "реакция", "реакции", "реакций"))
    return ", ".join(parts) or "ничего"


def target_counts(t: Target) -> tuple[int, int, int]:
    n = len(t.message_ids)
    return (0, n, len(t.reaction_ids)) if t.kind == "comments" else (n, 0, len(t.reaction_ids))


def row(t: Target) -> str:
    title = f"{t.title} (комментарии к постам)" if t.kind == "comments" else t.title
    return f"{title}: {counts(*target_counts(t))}"


def estimate_minutes(targets: list[Target], pause_s: float, chunk: int = cleanup.DELETE_CHUNK) -> int:
    requests = sum(math.ceil(len(t.message_ids) / chunk) + len(t.reaction_ids) for t in targets)
    return max(1, math.ceil(requests * pause_s / 60))


class Stopper:
    """Ctrl+C during deletion asks to stop after the current request; a second Ctrl+C stops at once."""

    def __init__(self) -> None:
        self.requested = False

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
            say("\nОстанавливаюсь после текущего запроса. Нажмите Ctrl+C ещё раз, чтобы прервать сразу.")

        signal.signal(signal.SIGINT, handler)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous)


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
        ui.say("Это не вход в ваш аккаунт Telegram. Выйдите (tgsweep --logout) и войдите заново.")
        return 1
    ui.say(f"Аккаунт: {_name(me)}")

    retention = await ui.choose("Какие ваши сообщения удалить?", RETENTION_CHOICES)
    if retention is None:
        ui.say("Ничего не удалено.")
        return 0

    ui.say("Ищу ваши сообщения в группах и комментарии к постам. В больших чатах это может занять несколько минут.")
    reaction_state = home.load_state()

    def on_chat(done: int, total: int, title: str) -> None:
        ui.say(f"  {done + 1}/{total}: {title}")

    plan = await cleanup.build_plan(
        client, me.id, policy, retention_days=retention, now=now(), reaction_state=reaction_state,
        sleep=sleep, on_chat=on_chat,
    )
    _keep_empty_scans(plan, reaction_state)
    home.save_state(reaction_state)
    _say_skipped(ui, plan)

    found = [t for t in plan.targets if t.message_ids or t.reaction_ids]
    if not found:
        if retention == 0:
            ui.say("Удалять нечего: ваших сообщений в группах нет.")
        else:
            ui.say(
                f"Удалять нечего: ваших сообщений старше {plural(retention, 'дня', 'дней', 'дней')} нет. "
                "Чтобы удалить и свежие, выберите «Все, даже сегодняшние»."
            )
        return 0

    picked = await ui.pick("Отметьте чаты, где удалить ваши сообщения:", [row(t) for t in found])
    if not picked:
        ui.say("Ни один чат не отмечен, ничего не удалено.")
        return 0
    chosen = [found[i] for i in sorted(set(picked))]
    m, c, r = (sum(x) for x in zip(*(target_counts(t) for t in chosen)))
    total = m + c + r
    ui.say(
        f"Будет удалено: {counts(m, c, r)} в {plural(len(chosen), 'чате', 'чатах', 'чатах')}. "
        f"Вернуть их будет нельзя. Это займёт примерно {plural(estimate_minutes(chosen, policy.pause_s), 'минуту', 'минуты', 'минут')}."
    )
    typed = await ui.type_in(f"Чтобы подтвердить, введите число {total}:")
    if (typed or "").strip() != str(total):
        ui.say("Число не совпало, ничего не удалено.")
        return 0

    journal = Journal(home.journal_file)
    reports = await _delete_in_batches(client, plan, chosen, policy, me.id, journal, reaction_state, home, sleep, stopper, ui)
    _say_report(ui, reports, home)
    return 0


async def _delete_in_batches(
    client, plan: Plan, chosen: list[Target], policy: Policy, me_id: int, journal: Journal,
    reaction_state: dict[str, int], home: Home, sleep: Sleep, stopper: Stopper, ui: UI,
) -> list[ApplyReport]:
    """The typed count covers everything ticked; the per-run limits only cut it into batches.

    apply_plan moves a chat's reaction scan position when its batch removed every reaction. A later
    batch of the same chat would then move it past a reaction that failed earlier, so a chat with a
    failure goes back to the position this run started from, and the next run finds that reaction again.
    """
    remaining = [dataclasses.replace(t, message_ids=list(t.message_ids), reaction_ids=list(t.reaction_ids)) for t in chosen]
    total = sum(sum(target_counts(t)) for t in remaining)
    started_at = {str(t.chat_id): reaction_state.get(str(t.chat_id)) for t in chosen}
    failed: set[str] = set()
    done = 0
    reports: list[ApplyReport] = []

    def keep_failed_findable() -> None:
        for key in failed:
            if started_at[key] is None:
                reaction_state.pop(key, None)
            else:
                reaction_state[key] = started_at[key]
        home.save_state(reaction_state)

    with stopper.armed(ui.say):
        while remaining:
            batch = cleanup.narrowed(plan, remaining, policy)
            report = None
            try:
                report = await cleanup.apply_plan(
                    client, batch, me_id=me_id, journal=journal, reaction_state=reaction_state,
                    sleep=sleep, pause=policy.pause_s, should_stop=stopper,
                )
            finally:
                if report is not None:
                    failed.update(str(c.chat_id) for c in report.chats if c.unreact_failed)
                keep_failed_findable()
            reports.append(report)
            sent_now = sum(sum(target_counts(t)) for t in batch.targets)
            done += sent_now
            if report.stopped or sent_now == 0:  # a zero limit would otherwise loop for ever
                break
            for rest, sent in zip(remaining, batch.targets):
                rest.message_ids = rest.message_ids[len(sent.message_ids):]
                rest.reaction_ids = rest.reaction_ids[len(sent.reaction_ids):]
            remaining = [t for t in remaining if t.message_ids or t.reaction_ids]
            if remaining:
                ui.say(f"Обработано {done} из {total}, продолжаю.")
    return reports


def _keep_empty_scans(plan: Plan, reaction_state: dict[str, int]) -> None:
    """A chat whose reaction scan found nothing keeps its position, so the next run starts from there."""
    for t in plan.targets:
        if t.reaction_scan_to and not t.reaction_ids:
            reaction_state[str(t.chat_id)] = t.reaction_scan_to


def _say_skipped(ui: UI, plan: Plan) -> None:
    by_reason: dict[str, int] = {}
    for _, _, reason in plan.skipped:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    lines = [f"  {SKIP_NOTES[r]}: {n}" for r, n in by_reason.items() if r in SKIP_NOTES]
    if lines:
        ui.say("Не трогаю:\n" + "\n".join(lines))


def _say_report(ui: UI, reports: list[ApplyReport], home: Home) -> None:
    msgs = sum(r.messages for r in reports)
    comments = sum(r.comments for r in reports)
    reactions = sum(r.reactions for r in reports)
    lines = [f"Готово. Удалено сообщений: {msgs}, комментариев: {comments}, снято реакций: {reactions}."]
    missing = sum(r.missing for r in reports)
    refused = sum(r.refused for r in reports)
    failed = sum(r.failed for r in reports)
    if missing:
        lines.append(f"Уже удалены раньше: {missing}.")
    if refused:
        lines.append(f"Не тронуты, потому что оказались не вашими: {refused}.")
    if failed:
        lines.append(f"Не удалось удалить: {failed}. Запустите tgsweep ещё раз позже.")
    if any(r.stopped for r in reports):
        lines.append("Остановлено до конца. Оставшееся найдётся при следующем запуске.")
    lines.append(f"Журнал без текстов сообщений: {home.journal_file}")
    ui.say("\n".join(lines))


def _name(me) -> str:
    name = " ".join(p for p in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if p)
    username = getattr(me, "username", None)
    return f"{name} (@{username})" if username else name or str(me.id)
