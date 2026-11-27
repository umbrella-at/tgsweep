"""The QR code leaves the screen once it is no longer needed, checked on a terminal emulator (pyte).

The emulator gets exactly what TerminalUI writes. A terminal driver turns "\\n" into "\\r\\n" (onlcr),
so the emulator runs in LNM mode, where a line feed also returns the carriage, as on a real terminal.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import sys

import pyte
import pytest
from telethon import errors

from tgsweep import cli, signin
from tgsweep.i18n import set_lang
from tgsweep.ui import StatusLine, TerminalUI

QR_MARKS = set("▀▄█")
SIGNED_IN = {"ru": "Вход выполнен.", "en": "Signed in."}
PASSWORD_PROMPT = "Облачный пароль: "


def url(n: int) -> str:
    """A sign-in link as long as Telegram's (about 60 characters): a 32-byte token in base64."""
    return "tg://login?token=" + base64.urlsafe_b64encode(bytes([n + 1]) * 32).decode().rstrip("=")


def code_lines(n: int) -> list[str]:
    return signin.render_qr(url(n), color=False).splitlines()


def wrapped(text: str, columns: int) -> list[str]:
    """The rows a terminal this wide splits the line into (the texts here are one column per character)."""
    return [text[i:i + columns].rstrip() for i in range(0, len(text), columns)]


def has_rows(rows: list[str], part: list[str]) -> bool:
    return any(rows[i:i + len(part)] == part for i in range(len(rows) - len(part) + 1))


class Terminal:
    """A terminal window of the given size: the output stream of a TerminalUI."""

    def __init__(self, columns: int, lines: int) -> None:
        self.screen = pyte.HistoryScreen(columns, lines, history=1000)
        self.screen.set_mode(pyte.modes.LNM)
        self.stream = pyte.Stream(self.screen)
        self.written = ""

    def write(self, data: str) -> int:
        self.written += data
        self.stream.feed(data)
        return len(data)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    def ui(self) -> TerminalUI:
        return TerminalUI(status=StatusLine(out=self, columns=lambda: self.screen.columns), ansi=True)

    def rows(self) -> list[str]:
        """The visible rows, trailing spaces cut."""
        return [row.rstrip() for row in self.screen.display]

    def text(self) -> list[str]:
        """The visible rows down to the last one with something on it: an empty row left inside counts."""
        rows = self.rows()
        while rows and not rows[-1]:
            rows.pop()
        return rows

    def scrolled(self) -> list[str]:
        """Rows that went up into the scrollback."""
        return ["".join(line[x].data for x in range(self.screen.columns)).rstrip() for line in self.screen.history.top]

    def code_rows(self) -> list[int]:
        """Visible rows with QR cells: block characters, or spaces on a coloured background."""
        found = []
        for y in range(self.screen.lines):
            line = self.screen.buffer[y]
            cells = [line[x] for x in range(self.screen.columns)]
            if any(c.data in QR_MARKS or (c.data == " " and c.bg != "default") for c in cells):
                found.append(y)
        return found

    def shown_code(self) -> list[str]:
        rows = self.code_rows()
        assert not rows or rows == list(range(rows[0], rows[0] + len(rows))), f"the code is torn: rows {rows}"
        return [self.rows()[y] for y in rows]


class QR:
    """Telegram's QR login: each wait ends with the next outcome, None being the scan."""

    def __init__(self, outcomes, on_wait) -> None:
        self.outcomes = list(outcomes)
        self.on_wait = on_wait
        self.n = 0

    @property
    def url(self) -> str:
        return url(self.n)

    async def wait(self, timeout=None) -> None:
        self.on_wait(self.n)
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome

    async def recreate(self) -> None:
        self.n += 1


class Client:
    def __init__(self, qr: QR) -> None:
        self.qr = qr
        self.passwords: list[str] = []

    async def qr_login(self):
        return self.qr

    async def is_user_authorized(self):
        return False

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        self.passwords.append(password)


def sign_in(ui: TerminalUI, outcomes, on_wait=lambda n: None) -> Client:
    """What cli.run does for a QR sign-in, from the line above the code to the end."""
    client = Client(QR(outcomes, on_wait))
    ui.say(cli.login_help())
    asyncio.run(cli.qr_sign_in(client, ui))
    return client


@pytest.fixture
def telethon_log():
    lib = logging.getLogger("telethon")
    saved = lib.handlers[:], lib.level, lib.propagate
    yield logging.getLogger("telethon.network.mtprotosender")
    lib.handlers[:], lib.level, lib.propagate = saved


SIZES = [
    # columns, lines, the whole block fits on the screen, the line above the code is still on the screen after it
    pytest.param(80, 24, True, False, id="80x24"),
    pytest.param(120, 40, True, True, id="120x40"),
    pytest.param(60, 20, False, False, id="60x20"),
    # Narrower than a code line with its colour codes (41 + 12 characters): only the 41 columns count.
    pytest.param(50, 30, True, True, id="50x30"),
]


def test_the_measured_code_is_21_rows_of_41_columns():
    """The sizes below are about this code: 21 rows, with the empty line and the hint a whole 80x24 window."""
    lines = code_lines(0)
    assert len(url(0)) == 60
    assert len(lines) == 21 and {len(line) for line in lines} == {41}


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("columns, lines, whole, help_stays", SIZES)
def test_one_code_on_the_screen_and_none_after_the_sign_in(columns, lines, whole, help_stays, lang):
    set_lang(lang)
    term = Terminal(columns, lines)
    shown: list[list[str]] = []
    sign_in(term.ui(), [asyncio.TimeoutError(), asyncio.TimeoutError(), None],
            on_wait=lambda n: shown.append(term.shown_code()))

    assert len(shown) == 3
    for n, rows in enumerate(shown):
        expected = code_lines(n)
        if whole:
            assert rows == expected, f"code {n}: the screen must show it whole and nothing else"
        else:  # its top went up into the scrollback
            assert rows and rows == expected[-len(rows):], f"code {n}: only its own rows on the screen"

    assert term.code_rows() == []
    assert term.text() == (wrapped(cli.login_help(), columns) if help_stays else []) + [SIGNED_IN[lang]]
    transcript = term.scrolled() + term.rows()
    assert has_rows(transcript, wrapped(cli.login_help(), columns))
    if whole:  # nothing of the codes is left even in the scrollback
        assert not any(set(row) & QR_MARKS for row in transcript)


@pytest.mark.parametrize("columns, lines, whole, help_stays", SIZES)
def test_a_telegram_warning_goes_away_with_the_code(columns, lines, whole, help_stays, telethon_log):
    term = Terminal(columns, lines)
    ui = term.ui()
    cli.quiet_library_logs(ui)
    warning = "Connection closed while receiving data"
    seen = []

    def on_wait(n):
        seen.append(any(warning in row for row in term.rows()))
        telethon_log.warning(warning)

    sign_in(ui, [asyncio.TimeoutError(), None], on_wait=on_wait)
    assert seen == [False, False]  # the first warning left with the first code
    assert term.text() == (wrapped(cli.login_help(), columns) if help_stays else []) + ["Вход выполнен."]
    assert term.code_rows() == []


def test_no_code_on_the_screen_when_the_cloud_password_is_asked():
    term = Terminal(120, 40)
    ui = term.ui()
    asked = []

    def ask_secret(prompt):
        asked.append((prompt, term.code_rows(), term.text()))
        return "pw"

    ui.ask_secret = ask_secret
    client = sign_in(ui, [asyncio.TimeoutError(), errors.SessionPasswordNeededError(request=None)])
    assert asked == [(PASSWORD_PROMPT, [], [cli.login_help()])]
    assert client.passwords == ["pw"]
    assert term.text() == [cli.login_help(), "Вход выполнен."]


@pytest.mark.parametrize("stop", [KeyboardInterrupt(), asyncio.CancelledError(), ConnectionError("network down")],
                         ids=["ctrl-c", "cancelled by ctrl-c", "network error"])
def test_the_code_goes_away_when_the_sign_in_is_cut_short(stop):
    term = Terminal(120, 40)
    with pytest.raises(type(stop)):
        sign_in(term.ui(), [asyncio.TimeoutError(), stop])
    assert term.code_rows() == []
    assert term.text() == [cli.login_help()]


def test_whatever_is_shown_goes_when_the_block_closes():
    term = Terminal(80, 24)
    ui = term.ui()
    ui.say("before")
    with ui.transient() as block:
        block.show("one\ntwo")
        ui.say("three")
        assert term.text() == ["before", "one", "two", "three"]
    assert term.text() == ["before"]
    with pytest.raises(RuntimeError):
        with ui.transient() as block:
            block.show("four")
            raise RuntimeError
    assert term.text() == ["before"]
    ui.say("after")
    assert term.text() == ["before", "after"]


def test_the_status_line_comes_back_where_the_block_was():
    term = Terminal(80, 24)
    ui = TerminalUI(status=StatusLine(out=term, columns=lambda: 80, clock=lambda: 0.0, live=True), ansi=True)
    ui.say("before")
    ui.progress("searching")
    with ui.transient() as block:
        block.show("one\ntwo")
        assert term.text() == ["before", "one", "two", "| 0:00 searching"]
    assert term.text() == ["before", "| 0:00 searching"]


def test_qr_login_clears_before_each_code_before_the_password_and_on_the_way_out():
    events = []
    client = Client(QR([asyncio.TimeoutError(), errors.SessionPasswordNeededError(request=None)], lambda n: None))
    asyncio.run(signin.qr_login(
        client,
        ask_secret=lambda prompt: events.append("password") or "pw",
        say=lambda text: events.append("say"),
        show=lambda link: events.append("show"),
        clear=lambda: events.append("clear"),
    ))
    assert events == ["clear", "show", "say", "clear", "show", "say", "clear", "password", "clear"]


def test_qr_login_clears_on_the_way_out_of_an_error():
    events = []
    client = Client(QR([ConnectionError("network down")], lambda n: None))
    with pytest.raises(ConnectionError):
        asyncio.run(signin.qr_login(client, ask_secret=lambda p: "", say=lambda t: None,
                                    show=lambda link: events.append("show"), clear=lambda: events.append("clear")))
    assert events == ["clear", "show", "clear"]


# --- escape codes only for a terminal that understands them ------------------


def test_output_that_is_not_a_terminal_gets_no_escape_codes(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(cli, "enable_ansi", lambda: True)
    ui = cli.terminal_ui()
    sign_in(ui, [asyncio.TimeoutError(), None])
    written = out.getvalue()
    assert "\x1b[" not in written
    # Nothing is erased: both codes, the line above them and the sign-in line stay in the output.
    assert "\n".join(code_lines(0)) in written and "\n".join(code_lines(1)) in written
    assert cli.login_help() in written and written.endswith("Вход выполнен.\n")


@pytest.mark.parametrize("vt, expected", [(True, True), (False, False)], ids=["vt on", "windows without vt"])
def test_a_terminal_gets_escape_codes_only_when_they_work(monkeypatch, vt, expected):
    term = Terminal(80, 24)
    monkeypatch.setattr(sys, "stdout", term)
    monkeypatch.setenv("COLUMNS", "80")  # the window size the program reads, as the terminal reports it
    monkeypatch.setenv("LINES", "24")
    monkeypatch.setattr(cli, "enable_ansi", lambda: vt)
    ui = cli.terminal_ui()
    assert ui.ansi is expected
    sign_in(ui, [asyncio.TimeoutError(), None])
    assert ("\x1b[" in term.written) is expected
    if expected:
        assert term.code_rows() == []
