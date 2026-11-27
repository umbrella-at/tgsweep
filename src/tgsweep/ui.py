"""The terminal UI: arrow keys and checkboxes, on macOS, Linux and Windows alike.

The questions run on the same asyncio loop as the Telegram client (questionary's async API):
the synchronous one would start a second loop and fail inside the running one.
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import logging
import math
import re
import shutil
import sys
import time
import unicodedata
from typing import Any, Callable, Iterator, Sequence, TextIO

import questionary
from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.shortcuts import PromptSession
from questionary.constants import NO, YES
from questionary.prompts.common import InquirerControl
from wcwidth import wcswidth, wcwidth

from .i18n import t

# The letters on the same keys in the Russian layout. A terminal sends the letter, not the key,
# so with the Russian layout on, the key marked A sends «ф» and questionary's «a» does nothing.
LATIN_KEYS = "qwertyuiopasdfghjklzxcvbnm"
RUSSIAN_KEYS = "йцукенгшщзфывапролдячсмить"

SPINNER = "|/-\\"  # plain ASCII: every console font has it
REDRAW_S = 0.25
# The terminal echoes ^C after the line; the next redraw wipes these extra columns too.
ECHO_ROOM = 2
# Colour and cursor codes take no room on the screen.
ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# A long line breaks at spaces, never inside these: a command with its options, an address. The
# program's folder joins them at run time (on macOS its name has a space: Application Support).
COMMAND = re.compile(r"tgsweep(?: --lang (?:ru|en|auto)| --[a-z]+(?:-[a-z]+)*)+")
ADDRESS = re.compile(r"https?://\S+")
QR_MARKS = frozenset("▀▄█")
# The start of a line: its indent and a list marker, «  1. » or «- ».
LEAD = re.compile(r"( *)(\d+\. |- )?")


def printable(text: str) -> str:
    """Control characters become spaces and format characters (bidi marks) go, so one line stays one line."""
    return "".join(
        " " if unicodedata.category(ch) == "Cc" else ch
        for ch in text
        if ch == "\u200d" or unicodedata.category(ch) != "Cf"  # the joiner holds emoji sequences together
    )


def columns_of(text: str) -> int:
    """Screen columns the text takes: emoji with a variation selector and joined emoji count as the terminal draws them."""
    width = wcswidth(text)
    return width if width >= 0 else len(text)


def _units(text: str) -> list[str]:
    """Characters with the marks, selectors and joined parts that follow them: a cut never splits an emoji."""
    units: list[str] = []
    for ch in text:
        if units and (wcwidth(ch) == 0 or units[-1].endswith("\u200d")):
            units[-1] += ch
        else:
            units.append(ch)
    return units


def line_widths(text: str) -> list[int]:
    """Screen columns of each line of the text; colour codes take none."""
    return [columns_of(ESCAPE.sub("", line)) for line in text.split("\n")]


def screen_rows(widths: list[int], width: int) -> int:
    """Rows the lines take on a screen this wide: a long line wraps to as many rows as it needs, an empty one takes one."""
    width = max(1, width)
    return sum(max(1, math.ceil(columns / width)) for columns in widths)


def fit(text: str, width: int) -> str:
    """Cut the text to the screen width in columns, ending with an ellipsis when cut."""
    if columns_of(text) <= width:
        return text
    out = ""
    for unit in _units(text):
        if columns_of(out + unit) + 1 > width:
            break
        out += unit
    return out + "…"


def _whole_pieces(line: str, whole: Sequence[str]) -> list[tuple[int, int]]:
    spans = [m.span() for pattern in (COMMAND, ADDRESS) for m in pattern.finditer(line)]
    for path in whole:
        if path:
            spans += [m.span() for m in re.finditer(re.escape(path) + r"\S*", line)]
    return spans


def _line_breaks(line: str, width: int, whole: Sequence[str] = ()) -> tuple[list[tuple[int, int]], str]:
    """Where a line wider than the screen breaks: the runs of spaces that become line ends, and the
    indent of the rows after the first.

    Words go on a row while they fit, counted in screen columns. A word or a piece kept whole that is
    wider than the screen stays whole, the terminal wraps it. Lines with escape codes and QR code
    rows never break: their columns are not what they seem.
    """
    if width < 1 or ESCAPE.search(line) or QR_MARKS & set(line) or columns_of(line) <= width:
        return [], ""
    lead = LEAD.match(line)
    indent, marker = lead.group(1), lead.group(2) or ""
    # Under the text after a list marker; one step in from an indented line; a plain line stays plain.
    hang = " " * len(indent + marker) if marker else (indent + "  " if indent else "")
    keep = _whole_pieces(line, whole)
    gaps = [
        m.span() for m in re.finditer(r" +", line)
        if m.start() >= lead.end() and m.end() < len(line) and not any(a < m.start() and m.end() < b for a, b in keep)
    ]
    starts = [0] + [end for _, end in gaps]
    ends = [start for start, _ in gaps] + [len(line)]
    used = columns_of(line[starts[0]:ends[0]])
    breaks = []
    for i, (start, end) in enumerate(gaps):
        word = columns_of(line[starts[i + 1]:ends[i + 1]])
        if used + (end - start) + word <= width:
            used += (end - start) + word
        else:
            breaks.append((start, end))
            used = len(hang) + word
    return breaks, hang


def wrap(text: str, width: int, whole: Sequence[str] = ()) -> str:
    """The text with each line broken at spaces to fit the width in screen columns.

    whole: pieces that keep their spaces (the program's folder), with any path that starts with them.
    """
    lines = []
    for line in text.split("\n"):
        breaks, hang = _line_breaks(line, width, whole)
        parts, last = [], 0
        for start, end in breaks:
            parts.append(line[last:start])
            last = end
        parts.append(line[last:])
        lines.append(("\n" + hang).join(parts))
    return "\n".join(lines)


def wrap_fragments(fragments: list[tuple], width: int, whole: Sequence[str] = ()) -> list[tuple]:
    """Styled pieces of a question line, broken by the same rules as wrap(): the question, its hint
    and the answer are read as the one line the screen shows."""
    plain = "".join(fragment[1] for fragment in fragments)
    cuts: dict[int, tuple[int, str]] = {}
    offset = 0
    for line in plain.split("\n"):
        breaks, hang = _line_breaks(line, width, whole)
        for start, end in breaks:
            cuts[offset + start] = (offset + end, hang)
        offset += len(line) + 1
    if not cuts:
        return fragments
    out, i, skip_to = [], 0, 0
    for style, text, *rest in fragments:
        piece = []
        for ch in text:
            if i in cuts:
                skip_to, hang = cuts[i]
                piece.append("\n" + hang)
            elif i >= skip_to:
                piece.append(ch)
            i += 1
        out.append((style, "".join(piece), *rest))
    return out


def clock_text(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


class StatusLine:
    """One line at the bottom that redraws itself with a spinner and a timer, so a long step never looks frozen.

    It uses only carriage returns and spaces, no escape codes, so it works in any console. Lines
    printed while it is shown go above it.
    """

    def __init__(
        self,
        out: TextIO | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        columns: Callable[[], int] = lambda: shutil.get_terminal_size((80, 24)).columns,
        live: bool | None = None,
    ) -> None:
        self._out = out
        self.clock = clock
        self.columns = columns
        self._live = live
        self.text: str | None = None
        self.started = 0.0
        self.frame = 0
        self.drawn = 0  # columns the last drawing took
        self._task: asyncio.Task | None = None
        self.broken = False  # the console refused a write: the line is off, the run goes on

    @property
    def out(self) -> TextIO:
        return self._out or sys.stdout

    @property
    def live(self) -> bool:
        if self._live is not None:
            return self._live
        isatty = getattr(self.out, "isatty", None)
        return bool(isatty and isatty())

    def show(self, text: str | None) -> None:
        if text is None:
            self.stop()
            return
        if self.broken or not self.live:
            return
        if self.text is None:
            self.started = self.clock()
            self.frame = 0
            try:
                self._task = asyncio.get_running_loop().create_task(self._spin())
            except RuntimeError:  # no running loop: redraw only on updates
                self._task = None
        self.text = printable(text)
        self.draw()

    def line(self) -> str:
        """The timer comes first: a narrow screen cuts the end of the line, never the part that moves."""
        return f"{SPINNER[self.frame % len(SPINNER)]} {clock_text(self.clock() - self.started)} {self.text}"

    def _write(self, data: str) -> None:
        try:
            self.out.write(data)
            self.out.flush()
        except (OSError, ValueError):  # a closed stream, or a console code page without these characters
            self.broken = True
            self.text = None
            if self._task is not None:
                self._task.cancel()
                self._task = None

    def draw(self) -> None:
        if self.text is None or self.broken:
            return
        room = max(10, self.columns() - 1)
        shown = fit(self.line(), max(10, room - ECHO_ROOM))
        width = columns_of(shown)
        # Written twice: the spaces wipe a longer previous line, the second copy leaves the cursor at its end.
        pad = max(0, min(self.drawn + ECHO_ROOM, room) - width)
        self._write("\r" + shown + " " * pad + "\r" + shown)
        self.drawn = width

    def clear(self) -> None:
        if self.drawn and not self.broken:
            self._write("\r" + " " * min(self.drawn + ECHO_ROOM, max(10, self.columns() - 1)) + "\r")
        self.drawn = 0

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.clear()
        self.text = None

    async def _spin(self) -> None:
        while True:
            await asyncio.sleep(REDRAW_S)
            self.frame += 1
            self.draw()


def choose_hint() -> str:
    return t("choose_hint")


def pick_hint() -> str:
    return t("pick_hint")


def with_russian_keys(question: questionary.Question) -> questionary.Question:
    """Every letter shortcut of the question also works on the Russian layout: «ф» like a, «н» like y, «т» like n."""
    russian = dict(zip(LATIN_KEYS + LATIN_KEYS.upper(), RUSSIAN_KEYS + RUSSIAN_KEYS.upper()))
    bindings = question.application.key_bindings
    extra = KeyBindings()
    for binding in bindings.bindings:
        if all(key in russian for key in binding.keys):  # letters only: arrows, Enter and the catch-all stay as they are
            keys = [russian[key] for key in binding.keys]
            extra.add(*keys, filter=binding.filter, eager=binding.eager)(binding.handler)
    question.application.key_bindings = merge_key_bindings([bindings, extra])
    return question


def _question_session(question: questionary.Question) -> PromptSession:
    """The PromptSession that draws the question line. questionary keeps it only inside the layout:
    the window above the input shows text from a closure over the session's own method."""
    found: list[PromptSession] = []
    for window in question.application.layout.find_all_windows():
        for cell in getattr(getattr(window.content, "text", None), "__closure__", None) or ():
            try:
                owner = getattr(cell.cell_contents, "__self__", None)
            except ValueError:  # an empty cell
                continue
            # A checkbox has a second session for its error line; only the question line has a function for a message.
            if isinstance(owner, PromptSession) and callable(owner.message) and owner not in found:
                found.append(owner)
    if len(found) != 1:
        raise LookupError(f"questionary: {len(found)} question lines instead of one")
    return found[0]


def with_own_line(
    question: questionary.Question,
    answer: Callable[[str], str] | None = None,
    whole: Sequence[str] = (),
) -> questionary.Question:
    """The question line is broken by words for the screen width, and the answer shown after it is
    tgsweep's: answer gets questionary's answer text and returns the one to show.

    LookupError when questionary is built differently from the version this was written for.
    """
    session = _question_session(question)
    tokens = session.message

    def line() -> list[tuple]:
        fragments = [
            (style, answer(text) if answer and style == "class:answer" else text, *rest)
            for style, text, *rest in to_formatted_text(tokens())
        ]
        return wrap_fragments(fragments, get_app().output.get_size().columns - 1, whole)

    session.message = line
    return question


def picked_answer(question: questionary.Question) -> Callable[[str], str]:
    """After a checkbox: how many chats are ticked. One chat keeps questionary's [title]."""
    found = [w.content for w in question.application.layout.find_all_windows() if isinstance(w.content, InquirerControl)]
    if len(found) != 1:
        raise LookupError(f"questionary: {len(found)} lists of choices instead of one")
    choices = found[0]

    def answer(text: str) -> str:
        count = len(choices.selected_options)
        if count == 0:
            return t("picked_nothing")
        return text if count == 1 else t("picked_many", count=count)

    return answer


def yes_no_answer(question: questionary.Question) -> Callable[[str], str]:
    def answer(text: str) -> str:
        return {YES: t("answer_yes"), NO: t("answer_no")}.get(text, text)

    return answer


class Transient:
    """Lines that leave the screen when they are no longer needed, like the QR code after the sign-in.

    show() opens a block with its text; everything said after it, library warnings included, joins
    the block; clear() erases the block and closes it. Only escape codes can erase, so without them
    (output that is not a terminal, a Windows console without VT) clear() leaves the lines where they are.
    """

    def __init__(self, ui: TerminalUI) -> None:
        self.ui = ui

    @property
    def colors(self) -> bool:
        return self.ui.ansi

    def show(self, text: str) -> None:
        self.clear()  # never two blocks on the screen
        self.ui.block = []
        self.ui.say(text)

    def clear(self) -> None:
        widths, self.ui.block = self.ui.block, None
        if not widths or not self.ui.ansi:
            return
        # Counted now, not when printed: a terminal rewraps the lines when its window is resized.
        rows = screen_rows(widths, self.ui.status.columns())
        status = self.ui.status
        status.clear()
        # Up to the first row of the block (a terminal stops at its top row), then erase to the end.
        status.out.write(f"\x1b[{rows}F\x1b[J")
        status.out.flush()
        status.draw()


class TerminalUI:
    def __init__(
        self, *, status: StatusLine | None = None, ansi: bool = False, whole: Sequence[str] = (), **io: Any
    ) -> None:
        """ansi: escape codes may go to the output, so lines can be coloured and erased.
        whole: paths a line never breaks inside, nor inside any path that starts with them.
        io: prompt_toolkit input= and output=, for tests."""
        self.io = io
        self.status = status or StatusLine()
        self.ansi = ansi
        self.whole = tuple(whole)
        self.block: list[int] | None = None  # line widths of the open transient block, None when there is none

    def say(self, text: str) -> None:
        """A line above the status line, which comes back right under it.

        On a terminal long lines break between words; any other output gets the text as it is.
        """
        self.status.clear()
        if self.status.live:
            text = wrap(text, self.status.columns() - 1, self.whole)
        self.status.out.write(text + "\n")
        self.status.out.flush()
        if self.block is not None:
            self.block += line_widths(text)
        self.status.draw()

    @contextlib.contextmanager
    def transient(self) -> Iterator[Transient]:
        """A place for lines that must go away: whatever is still shown when it closes is erased."""
        block = Transient(self)
        try:
            yield block
        finally:
            block.clear()

    def progress(self, text: str | None) -> None:
        self.status.show(text)

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def ask_secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    async def _answer(
        self,
        question: questionary.Question,
        answer: Callable[[questionary.Question], Callable[[str], str]] | None = None,
    ) -> Any:
        """None when the user presses Ctrl+C or Ctrl+D: backing out of a question deletes nothing."""
        try:
            question = with_own_line(question, answer(question) if answer else None, self.whole)
        except LookupError:  # a questionary built differently: its own words and line breaks, the question still works
            pass
        try:
            return await with_russian_keys(question).unsafe_ask_async(patch_stdout=False)
        except (KeyboardInterrupt, EOFError):
            return None

    async def choose(self, question: str, choices: list[tuple[str, int]]) -> int | None:
        return await self._answer(questionary.select(
            question,
            choices=[questionary.Choice(title=label, value=value) for label, value in choices],
            instruction=choose_hint(),
            **self.io,
        ))

    async def pick(self, question: str, rows: list[str]) -> list[int] | None:
        return await self._answer(questionary.checkbox(
            question,
            choices=[questionary.Choice(title=text, value=i, checked=False) for i, text in enumerate(rows)],
            instruction=pick_hint(),
            **self.io,
        ), picked_answer)

    async def type_in(self, prompt: str) -> str | None:
        return await self._answer(questionary.text(prompt, **self.io))

    async def yes_no(self, question: str, default: bool) -> bool:
        answer = await self._answer(questionary.confirm(question, default=default, **self.io), yes_no_answer)
        return default if answer is None else answer


class SayHandler(logging.Handler):
    """Library warnings (Telethon reconnecting) go through say, above the status line, not into its middle."""

    def __init__(self, ui: TerminalUI) -> None:
        super().__init__(logging.WARNING)
        self.ui = ui

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.ui.say(f"Telegram: {record.getMessage()}")
        except Exception:
            self.handleError(record)
