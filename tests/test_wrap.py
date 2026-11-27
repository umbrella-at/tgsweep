"""Long lines break between words on a terminal, questions and their answers too, and the answers speak
the program's language. Checked on a terminal emulator (pyte), the way the program writes to a terminal.

The check of a screen: its rows, each stripped and joined by one space, read as the text with its
spaces normalized. A break inside a word puts a space into the word and the check fails.
"""

from __future__ import annotations

import argparse
import string
from pathlib import Path

import pytest
import questionary
from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output

from test_qr_screen import Terminal
from tgsweep import app, cli, i18n
from tgsweep.i18n import set_lang, t
from tgsweep.ui import StatusLine, TerminalUI, columns_of, picked_answer, with_own_line, wrap

ROOT = "/Users/maria/Library/Application Support/tgsweep"  # the program's folder on macOS, with its space
JOURNAL = ROOT + "/logs/actions.jsonl"
ENTER, DOWN = "\r", "\x1b[B"


def normalized(text: str) -> str:
    return " ".join(text.split())


# --- the line breaking itself --------------------------------------------------

WRAPS = [
    pytest.param(
        "Вход сохранён в /Users/maria/Library/Application Support/tgsweep. Выйти можно командой: tgsweep --logout", 40,
        ["Вход сохранён в", "/Users/maria/Library/Application Support/tgsweep.", "Выйти можно командой: tgsweep --logout"],
        id="folder and command, 40: the folder is wider and stays whole",
    ),
    pytest.param(
        "Вход сохранён в /Users/maria/Library/Application Support/tgsweep. Выйти можно командой: tgsweep --logout", 60,
        ["Вход сохранён в", "/Users/maria/Library/Application Support/tgsweep. Выйти", "можно командой: tgsweep --logout"],
        id="folder and command, 60",
    ),
    pytest.param(
        "Вход сохранён в /Users/maria/Library/Application Support/tgsweep. Выйти можно командой: tgsweep --logout", 80,
        ["Вход сохранён в /Users/maria/Library/Application Support/tgsweep. Выйти можно", "командой: tgsweep --logout"],
        id="folder and command, 80",
    ),
    pytest.param(
        "Журнал без текстов сообщений: /Users/maria/Library/Application Support/tgsweep/logs/actions.jsonl", 60,
        ["Журнал без текстов сообщений:", "/Users/maria/Library/Application Support/tgsweep/logs/actions.jsonl"],
        id="a path in the folder, wider than the screen",
    ),
    pytest.param(
        "Language saved: English. To follow the system language again: tgsweep --lang auto", 40,
        ["Language saved: English. To follow the", "system language again:", "tgsweep --lang auto"],
        id="command with a value, 40",
    ),
    pytest.param(
        "Language saved: English. To follow the system language again: tgsweep --lang auto", 60,
        ["Language saved: English. To follow the system language", "again: tgsweep --lang auto"],
        id="command with a value, 60",
    ),
    pytest.param(
        "Language saved: English. To follow the system language again: tgsweep --lang auto", 80,
        ["Language saved: English. To follow the system language again:", "tgsweep --lang auto"],
        id="command with a value, 80",
    ),
    pytest.param(
        "Language saved: English. To follow the system language again: tgsweep --lang auto", 81,
        ["Language saved: English. To follow the system language again: tgsweep --lang auto"],
        id="fits as it is, 81",
    ),
    pytest.param(
        "  1. Откройте https://my.telegram.org и войдите по номеру телефона.", 40,
        ["  1. Откройте https://my.telegram.org и", "     войдите по номеру телефона."],
        id="numbered step with an address: under the text after the marker",
    ),
    pytest.param(
        "- deletes nothing until you select chats and confirm, then deletes only what is yours", 40,
        ["- deletes nothing until you select chats", "  and confirm, then deletes only what is", "  yours"],
        id="dash marker",
    ),
    pytest.param(
        "  каналы (свои посты удаляйте в самом Telegram, комментарии к постам ищу отдельно): 1", 60,
        ["  каналы (свои посты удаляйте в самом Telegram, комментарии", "    к постам ищу отдельно): 1"],
        id="indent without a marker: two more spaces",
    ),
    pytest.param(
        "  каналы (свои посты удаляйте в самом Telegram, комментарии к постам ищу отдельно): 1", 80,
        ["  каналы (свои посты удаляйте в самом Telegram, комментарии к постам ищу", "    отдельно): 1"],
        id="indent without a marker, 80",
    ),
    pytest.param(
        "Аккаунт: Мария 🌸🌿 (@maria_demo) · 北京读书会 Пекинский книжный клуб 📚 и чай", 40,
        ["Аккаунт: Мария 🌸🌿 (@maria_demo) ·", "北京读书会 Пекинский книжный клуб 📚 и", "чай"],
        id="emoji and CJK take two columns",
    ),
    pytest.param(
        "Ошибка доступа к файлу C:\\Users\\Maria\\AppData\\Roaming\\tgsweep\\settings.json.supercalifragilistic: "
        "нет доступа", 40,
        ["Ошибка доступа к файлу",
         "C:\\Users\\Maria\\AppData\\Roaming\\tgsweep\\settings.json.supercalifragilistic:", "нет доступа"],
        id="a word wider than the screen stays whole",
    ),
    pytest.param("Готово.\n\n  отступ остаётся", 40, ["Готово.", "", "  отступ остаётся"], id="short lines stay"),
]


@pytest.mark.parametrize("text, width, rows", WRAPS)
def test_lines_break_between_words(text, width, rows):
    assert wrap(text, width, [ROOT]).split("\n") == rows


def test_lines_with_escape_codes_or_qr_cells_never_break():
    coloured = "\x1b[47m" + "x " * 40 + "\x1b[0m"
    code = "█▀▄ " * 30
    assert wrap(coloured, 20) == coloured
    assert wrap(code, 20) == code


# --- every text of the program on a terminal ----------------------------------

def values() -> dict[str, object]:
    """Placeholders filled as the program fills them, with long titles and the macOS folder."""
    return {
        "title": "Клуб настольных игр 🎲 и прогулок по выходным 北京读书会",
        "name": "Мария 🌸 Иванова-Петрова (@maria_demo)",
        "path": JOURNAL, "root": ROOT, "filename": JOURNAL,
        "reason": "Operation not permitted", "error": t("api_id_format"),
        "read": 1200, "found": 60, "chat": 9, "chats": i18n.plural(21, "chat"), "doing": t("scan_messages", found=60),
        "duration": app.duration(3725), "done": 37, "total": 53, "percent": 69,
        "counts": app.counts(49, 0, 4), "minutes": i18n.plural(12, "minute"), "days": i18n.plural(7, "day_older"),
        "messages": 49, "comments": 0, "reactions": 4, "missing": 3, "refused": 1, "failed": 2,
        "email": "m***@example.com", "url": "https://fragment.com/login_code/7", "where": t("sent_app"),
        "after": t("code_within", seconds=60), "way": t("way_sms"), "seconds": 60,
        "hours": 1, "rest": 5, "a": "TGSWEEP_API_ID", "b": "TGSWEEP_API_HASH", "value": "english", "count": 2,
    }


def program_texts() -> dict[str, str]:
    """Every text of i18n, filled in, and the blocks the program puts together from them."""
    filled = values()
    texts = {}
    for key, versions in i18n.TEXTS.items():
        fields = {name for _, name, _, _ in string.Formatter().parse(versions[i18n.get_lang()]) if name}
        assert fields <= filled.keys(), (key, fields - filled.keys())
        texts[key] = t(key, **{name: filled[name] for name in fields})
    texts["credentials_help"] = cli.credentials_help()
    texts["skipped"] = t("skipped_header") + "\n" + "\n".join(f"  {app.skip_note(r)}: 3" for r in app.SKIP_NOTES)
    return texts


def read_back(rows: list[str], columns: int, glue: bool = False) -> str:
    """The rows as one text, joined by a space. glue: a row filled to the edge goes on without one,
    for a text with a piece wider than the screen, which the terminal wraps itself."""
    text, full = "", False
    for row in rows:
        text += row.rstrip() if full else " " + row.strip()
        full = glue and columns_of(row.rstrip()) >= columns
    return normalized(text)


def pieces(text: str) -> list[str]:
    """What a line may not break inside: words, and paths in the program's folder with their space."""
    return text.replace(ROOT, ROOT.replace(" ", "\0")).split()


def has_wider_piece(text: str, columns: int) -> bool:
    return any(columns_of(piece.replace("\0", " ")) > columns - 1 for piece in pieces(text))


def too_wide(line: str, columns: int) -> bool:
    """Wider than the screen and not one piece that is kept whole."""
    return columns_of(line) > columns - 1 and len(pieces(line)) > 1


SIZES = [pytest.param(80, 24, id="80x24"), pytest.param(60, 20, id="60x20"), pytest.param(50, 30, id="50x30")]


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("columns, lines", SIZES)
def test_every_text_reads_whole_on_a_terminal(columns, lines, lang):
    set_lang(lang)
    texts = program_texts()
    assert len(texts) == len(i18n.TEXTS) + 2 > 100
    broken, wide = [], []
    for key, text in texts.items():
        term = Terminal(columns, lines)
        ui = TerminalUI(status=StatusLine(out=term, columns=lambda: columns), ansi=True, whole=[ROOT])
        ui.say(text)
        if read_back(term.scrolled() + term.text(), columns, has_wider_piece(text, columns)) != normalized(text):
            broken.append((key, term.scrolled() + term.text()))
        wide += [(key, line) for line in term.written.split("\n") if too_wide(line, columns)]
    assert broken == []
    assert wide == []


def test_the_check_sees_a_word_cut_by_the_terminal():
    """Without the line breaking (a pipe gets the text as it is) the same screen fails the check."""
    term = Terminal(60, 20)
    TerminalUI(status=StatusLine(out=term, columns=lambda: 60, live=False)).say(t("searching"))
    assert read_back(term.text(), 60) != normalized(t("searching"))


class Pipe:
    def __init__(self):
        self.written = ""

    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def isatty(self):
        return False


@pytest.mark.parametrize("key", ["searching", "qr_hint", "login_kept"])
def test_output_that_is_not_a_terminal_gets_the_text_as_it_is(key):
    pipe = Pipe()
    ui = TerminalUI(status=StatusLine(out=pipe, columns=lambda: 20), whole=[ROOT])
    text = t(key, root=ROOT)
    ui.say(text)
    assert pipe.written == text + "\n"


# --- real questions on a terminal ----------------------------------------------

class KeyedUI(TerminalUI):
    """TerminalUI on a pyte terminal of a fixed size: each question gets its keys once it is on the
    screen, and the screen at that moment is kept."""

    def __init__(self, term: Terminal, pipe, keys: list[str], **kw):
        columns, lines = term.screen.columns, term.screen.lines
        output = Vt100_Output(term, get_size=lambda: Size(rows=lines, columns=columns), term="xterm", enable_cpr=False)
        super().__init__(status=StatusLine(out=term, columns=lambda: columns), ansi=True, input=pipe, output=output, **kw)
        self.term, self.pipe, self.keys = term, pipe, list(keys)
        self.asked: list[list[str]] = []

    async def _answer(self, question, answer=None):
        keys, sent = self.keys.pop(0), []

        def shown(_):
            if not sent:
                sent.append(True)
                self.asked.append(self.term.text())
                self.pipe.send_text(keys)

        question.application.after_render += shown
        return await super()._answer(question, answer)


def question_rows(rows: list[str]) -> list[str]:
    """The rows of the question line: up to the list of choices, if there is one."""
    end = next((i for i, row in enumerate(rows) if row.startswith((" » ", "   "))), len(rows))
    return rows[:end]


ROWS = {
    "ru": ["Соседи по дому: 37 сообщений", "Книжный клуб: 12 сообщений, 4 реакции", "Бег по утрам: 8 сообщений"],
    "en": ["Building neighbors: 37 messages", "Book club: 12 messages, 4 reactions", "Morning runs: 8 messages"],
}
PICKED = {
    # keys, the answer in Russian, in English
    "two": (" " + DOWN + " " + ENTER, "отмечено: 2", "2 selected", [0, 1]),
    "one": (DOWN + " " + ENTER, "[Книжный клуб: 12 сообщений, 4 реакции]", "[Book club: 12 messages, 4 reactions]", [1]),
    "none": (ENTER, "ничего", "nothing", []),
}
QUESTION_SIZES = [pytest.param(80, 24, id="80x24"), pytest.param(60, 20, id="60x20")]


@pytest.mark.anyio
@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("columns, lines", QUESTION_SIZES)
@pytest.mark.parametrize("case", list(PICKED))
async def test_pick_question_breaks_by_words_and_answers_in_the_program_language(case, columns, lines, lang):
    set_lang(lang)
    keys, ru, en, result = PICKED[case]
    term = Terminal(columns, lines)
    with create_pipe_input() as pipe:
        ui = KeyedUI(term, pipe, [keys])
        assert await ui.pick(t("pick_chats"), ROWS[lang]) == result
    asked = question_rows(ui.asked[0])
    assert read_back(asked, columns) == normalized(f"? {t('pick_chats')} {t('pick_hint')}")
    assert max(columns_of(row) for row in asked) <= columns - 1
    answered = {"ru": ru, "en": en}[lang]
    assert read_back(term.text(), columns) == normalized(f"? {t('pick_chats')} {answered}")
    shown = " ".join(term.text())
    assert "done" not in shown and "selection" not in shown


@pytest.mark.anyio
@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("columns, lines", QUESTION_SIZES)
@pytest.mark.parametrize("keys, result, ru, en", [(ENTER, True, "Да", "Yes"), ("n" + ENTER, False, "Нет", "No")],
                         ids=["yes", "no"])
async def test_sign_out_question_breaks_by_words_and_answers_in_the_program_language(
    keys, result, ru, en, columns, lines, lang
):
    set_lang(lang)
    term = Terminal(columns, lines)
    with create_pipe_input() as pipe:
        ui = KeyedUI(term, pipe, [keys])
        assert await ui.yes_no(t("ask_sign_out"), default=True) is result
    assert read_back(ui.asked[0], columns) == normalized(f"? {t('ask_sign_out')} (Y/n)")
    assert max(columns_of(row) for row in ui.asked[0]) <= columns - 1
    assert read_back(term.text(), columns) == normalized(f"? {t('ask_sign_out')} {ru if lang == 'ru' else en}")
    if lang == "ru":
        assert "Yes" not in " ".join(term.text()) and "No" not in " ".join(term.text())


@pytest.mark.anyio
@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("columns, lines", QUESTION_SIZES)
async def test_choose_and_type_in_break_by_words(columns, lines, lang):
    set_lang(lang)
    term = Terminal(columns, lines)
    with create_pipe_input() as pipe:
        ui = KeyedUI(term, pipe, [DOWN * 3 + ENTER, "53" + ENTER])
        assert await ui.choose(t("which_messages"), app.retention_choices()) == 7
        assert await ui.type_in(t("confirm_number", total=53)) == "53"
    asked = question_rows(ui.asked[0])
    assert read_back(asked, columns) == normalized(f"? {t('which_messages')} {t('choose_hint')}")
    assert read_back(term.text(), columns) == normalized(
        f"? {t('which_messages')} {t('retention_7')} ? {t('confirm_number', total=53)} 53"
    )


async def run_on_terminal(lang: str, root, monkeypatch, columns: int = 80, lines: int = 24) -> KeyedUI:
    """The whole program on Maria's made-up account (scripts/demo.py), without pauses: 7 days, the
    first two chats, the number 53, then Enter on the sign-out question."""
    from fake import NOW, FakeClient
    from test_demo import load_demo
    from test_laptop import no_sleep
    from tgsweep.home import API_HASH_ENV, API_ID_ENV, Home

    set_lang(lang)
    demo = load_demo()
    client = demo.build_world(lang, NOW, FakeClient())
    monkeypatch.setenv(API_ID_ENV, demo.API_ID)
    monkeypatch.setenv(API_HASH_ENV, demo.API_HASH)
    monkeypatch.setattr(cli, "sweep", lambda *a, **kw: app.sweep(*a, now=lambda: NOW, sleep=no_sleep, **kw))
    home = Home(root)
    term = Terminal(columns, lines)
    with create_pipe_input() as pipe:
        ui = KeyedUI(term, pipe, [DOWN * 3 + ENTER, " " + DOWN + " " + ENTER, "53" + ENTER, ENTER], whole=[str(root)])
        args = argparse.Namespace(code=False, logout=False)
        assert await cli.run(args, home, ui, client_factory=demo.factory_for(client)) == 0
    return ui


@pytest.mark.anyio
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_the_whole_run_reads_whole_on_an_80x24_terminal(lang, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a folder with a space, about as long as the real one
    root = Path("Application Support") / "tgsweep"
    ui = await run_on_terminal(lang, root, monkeypatch)
    shown = read_back(ui.term.scrolled() + ui.term.text(), 80)
    said = [
        t("searching"), t("skip_broadcast") + ": 1", t("pick_chats") + " " + t("picked_many", count=2),
        t("will_delete", counts=app.counts(49, 0, 4), chats=i18n.plural(2, "chat_in"), minutes=i18n.plural(1, "minute")),
        t("report_done", messages=49, comments=0, reactions=4),
        t("report_journal", path=root / "logs" / "actions.jsonl"),
        t("ask_sign_out") + " " + t("answer_yes"), t("signed_out"),
    ]
    assert [text for text in said if normalized(text) not in shown] == []
    after_search, sign_out = ui.asked[1], ui.asked[3]
    assert normalized(f"? {t('pick_chats')} {t('pick_hint')}") in read_back(after_search, 80)
    assert read_back(sign_out, 80).endswith(normalized(f"? {t('ask_sign_out')} (Y/n)"))
    assert max(columns_of(row) for row in after_search + sign_out + ui.term.text()) <= 79


# --- if questionary changes inside, the tests say so out loud -----------------

def real_questions(pipe) -> dict[str, questionary.Question]:
    io = {"input": pipe, "output": DummyOutput()}
    return {
        "checkbox": questionary.checkbox("Q", choices=["a", "b"], instruction="(hint)", **io),
        "confirm": questionary.confirm("Q", default=True, **io),
        "select": questionary.select("Q", choices=["a", "b"], instruction="(hint)", **io),
        "text": questionary.text("Q", **io),
    }


@pytest.mark.parametrize("kind", ["checkbox", "confirm", "select", "text"])
def test_questionary_still_has_the_line_tgsweep_rewrites(kind):
    """tgsweep finds questionary's question line inside its layout. A questionary built differently
    fails here with LookupError, instead of the program quietly answering in English."""
    with create_pipe_input() as pipe:
        question = real_questions(pipe)[kind]
        if kind == "checkbox":
            picked_answer(question)
        with_own_line(question, lambda text: text)


def test_a_question_without_that_line_is_refused():
    with create_pipe_input() as pipe, pytest.raises(LookupError):
        with_own_line(questionary.Question(Application(input=pipe, output=DummyOutput())))


@pytest.mark.anyio
async def test_the_program_still_asks_when_questionary_is_built_differently(monkeypatch):
    """At run time a questionary that tgsweep does not know only costs the words: the question works."""
    from tgsweep import ui as ui_mod

    def refuse(question):
        raise LookupError("built differently")

    monkeypatch.setattr(ui_mod, "_question_session", refuse)
    term = Terminal(80, 24)
    with create_pipe_input() as pipe:
        ui = KeyedUI(term, pipe, [" " + DOWN + " " + ENTER])
        assert await ui.pick(t("pick_chats"), ROWS["ru"]) == [0, 1]
    assert "done (2 selections)" in " ".join(term.text())
