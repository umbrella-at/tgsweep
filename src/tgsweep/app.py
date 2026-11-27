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
    "dm": "личные чаты и боты (их не открываю)",
    "creator": "чаты, которые вы создали (пропускаю, чтобы не опустошить вашу же группу)",
    "broadcast": "каналы (свои посты удаляйте в самом Telegram, комментарии к постам ищу отдельно)",
    "unsupported": "чаты неизвестного типа",
}


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


def duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


def scan_line(p: ScanProgress) -> str:
    """What the search is doing, in one line. The chat title goes last: a narrow screen cuts it, not the numbers."""
    if p.phase == "dialogs":
        return f"Получаю список чатов: {p.read}"
    if p.phase == "waiting":
        doing = f"Telegram попросил подождать {duration(p.wait_s)}, жду"
    elif p.phase == "reactions":
        doing = f"ищу ваши реакции, просмотрено {p.read}"
    else:
        doing = f"ищу ваши сообщения, найдено всего {p.found}"
    return f"Чат {p.chat} из {p.chats} · {doing} · {p.title}"


def apply_line(p: ApplyProgress) -> str:
    """What the deletion is doing, in one line, the chat title last."""
    percent = p.done * 100 // p.total if p.total else 100
    line = f"Удаляю: {p.done} из {p.total} ({percent}%)"
    if p.wait_s:
        line += f" · Telegram попросил подождать {duration(p.wait_s)}, жду"
    if p.chat:
        line += f" · {p.chat}"
    return line


def estimate_minutes(targets: list[Target], pause_s: float, chunk: int = cleanup.DELETE_CHUNK) -> int:
    requests = sum(math.ceil(len(t.message_ids) / chunk) + len(t.reaction_ids) for t in targets)
    return max(1, math.ceil(requests * pause_s / 60))


STOP_NOTE = "Останавливаюсь после текущего запроса. Нажмите Ctrl+C ещё раз, чтобы прервать сразу."


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
                self._note = asyncio.get_running_loop().call_soon_threadsafe(say, STOP_NOTE)
            except RuntimeError:
                say(STOP_NOTE)

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
        ui.say("Это не вход в ваш аккаунт Telegram. Выйдите (tgsweep --logout) и войдите заново.")
        return 1
    ui.say(f"Аккаунт: {_name(me)}")

    retention = await ui.choose("Какие ваши сообщения удалить?", RETENTION_CHOICES)
    if retention is None:
        ui.say("Ничего не удалено.")
        return 0

    ui.say("Ищу ваши сообщения в группах и комментарии к постам. В больших чатах это может занять несколько минут.")
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
    ui.say(f"Поиск занял {duration(plan.stats.seconds)}, просмотрено {plural(plan.stats.chats, 'чат', 'чата', 'чатов')}.")
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
    lines = [f"  {SKIP_NOTES[r]}: {n}" for r, n in by_reason.items() if r in SKIP_NOTES]
    if lines:
        ui.say("Не трогаю:\n" + "\n".join(lines))


def _say_report(ui: UI, report: ApplyReport, home: Home) -> None:
    lines = [
        f"Готово. Удалено сообщений: {report.messages}, комментариев: {report.comments}, "
        f"снято реакций: {report.reactions}."
    ]
    missing, refused, failed = report.missing, report.refused, report.failed
    if missing:
        lines.append(f"Уже удалены раньше: {missing}.")
    if refused:
        lines.append(f"Не тронуты, потому что оказались не вашими: {refused}.")
    if failed:
        lines.append(f"Не удалось удалить: {failed}. Запустите tgsweep ещё раз позже.")
    if report.stopped:
        lines.append("Остановлено до конца. Оставшееся найдётся при следующем запуске.")
    lines.append(f"Журнал без текстов сообщений: {home.journal_file}")
    ui.say("\n".join(lines))


def _name(me) -> str:
    name = " ".join(p for p in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if p)
    username = getattr(me, "username", None)
    return f"{name} (@{username})" if username else name or str(me.id)
