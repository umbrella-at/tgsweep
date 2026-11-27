"""Russian and English: every text in both, the language of the system, --lang and TGSWEEP_LANG."""

from __future__ import annotations

import ast
import json
import re
import string
import subprocess
import sys
from pathlib import Path

import pytest

from test_laptop import ScriptedUI, assert_mode, pick_titled, run_sweep, world
from tgsweep import app, cli, i18n, signin, ui
from tgsweep.home import Home, default_root
from tgsweep.i18n import detect_lang, pick_lang, plural

pytestmark = pytest.mark.anyio

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
SRC = Path(i18n.__file__).parent


# --- the texts themselves ---------------------------------------------------------


def test_every_key_has_both_languages():
    assert len(i18n.TEXTS) > 100
    for key, versions in i18n.TEXTS.items():
        assert set(versions) == {"ru", "en"}, key
        assert all(isinstance(v, str) and v for v in versions.values()), key
    for key, versions in i18n.PLURALS.items():
        assert set(versions) == {"ru", "en"}, key
        assert len(versions["ru"]) == 3 and len(versions["en"]) == 2, key


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def test_both_languages_take_the_same_names():
    for key, versions in i18n.TEXTS.items():
        names = fields(versions["ru"])
        assert fields(versions["en"]) == names, key
        dummy = {name: "X" for name in names}
        for text in versions.values():
            text.format(**dummy)


def test_english_has_no_cyrillic():
    english = [v["en"] for v in i18n.TEXTS.values()] + [w for v in i18n.PLURALS.values() for w in v["en"]]
    assert [text for text in english if CYRILLIC.search(text)] == []


def cyrillic_literals(path: Path) -> list[tuple[str, int, str]]:
    """String literals with Cyrillic, except docstrings and ui.RUSSIAN_KEYS."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                allowed.add(id(first.value))
        if path.name == "ui.py" and isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "RUSSIAN_KEYS" for target in node.targets):
                allowed.add(id(node.value))
    return [
        (path.name, node.lineno, node.value[:40])
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and CYRILLIC.search(node.value) and id(node) not in allowed
    ]


def test_texts_live_only_in_i18n():
    files = sorted(p for p in SRC.glob("*.py") if p.name != "i18n.py")
    assert len(files) >= 12
    assert [hit for path in files for hit in cyrillic_literals(path)] == []
    assert len(cyrillic_literals(SRC / "i18n.py")) > 100  # the check does see Cyrillic where it is


def test_no_text_is_fixed_at_import():
    i18n.set_lang("en")
    english = (ui.pick_hint(), app.skip_note("dm"), signin.qr_hint().split(":")[0])
    i18n.set_lang("ru")
    russian = (ui.pick_hint(), app.skip_note("dm"), signin.qr_hint().split(":")[0])
    assert english == (
        "(arrows move, space selects, a selects all, Enter continues)",
        "private chats and bots (never opened)",
        "Scan the code with your phone",
    )
    assert russian == (
        "(стрелки двигают, пробел отмечает, a отмечает все, Enter продолжает)",
        "личные чаты и боты (их не открываю)",
        "Отсканируйте код телефоном",
    )


def test_english_word_forms():
    i18n.set_lang("en")
    assert [plural(n, "message") for n in (1, 2, 21, 0)] == ["1 message", "2 messages", "21 messages", "0 messages"]
    assert plural(1, "chat") == "1 chat"
    assert plural(5, "chat_in") == "5 chats"


def test_unknown_language_is_refused():
    with pytest.raises(ValueError):
        i18n.set_lang("de")


# --- the language of the system ---------------------------------------------------


def not_asked():
    raise AssertionError("the environment had the answer")


def broken():
    raise subprocess.TimeoutExpired(cmd="defaults", timeout=2)


@pytest.mark.parametrize(
    "env, platform, mac, win, expected",
    [
        ({"LANG": "ru_RU.UTF-8"}, "linux", not_asked, not_asked, "ru"),
        ({"LANG": "en_US.UTF-8"}, "linux", not_asked, not_asked, "en"),
        ({"LANGUAGE": "ru:en", "LANG": "en_US.UTF-8"}, "linux", not_asked, not_asked, "ru"),
        ({"LC_ALL": "C", "LANG": "ru_RU.UTF-8"}, "linux", not_asked, not_asked, "ru"),
        ({"LC_MESSAGES": "POSIX", "LANG": "C.UTF-8"}, "linux", not_asked, not_asked, "en"),
        ({"LANG": "ru_UA.UTF-8"}, "linux", not_asked, not_asked, "ru"),
        ({"LANG": "en_US.UTF-8"}, "darwin", not_asked, not_asked, "en"),
        ({}, "darwin", lambda: ["ru-RU", "en-US"], not_asked, "ru"),
        ({"LANG": "C.UTF-8"}, "darwin", lambda: ["ru-RU", "en-US"], not_asked, "ru"),
        ({}, "darwin", lambda: ["en-GB"], not_asked, "en"),
        ({}, "darwin", broken, not_asked, "en"),
        ({}, "darwin", lambda: [], not_asked, "en"),
        ({}, "win32", not_asked, lambda: 0x0419, "ru"),
        ({}, "win32", not_asked, lambda: 0x0819, "ru"),
        ({}, "win32", not_asked, lambda: 0x0409, "en"),
        ({}, "win32", not_asked, broken, "en"),
        ({}, "linux", not_asked, not_asked, "en"),
    ],
)
def test_detect_lang(env, platform, mac, win, expected):
    assert detect_lang(env, platform, mac, win) == expected


def test_mac_languages_are_read_from_defaults(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw.get("timeout")))
        return subprocess.CompletedProcess(cmd, 0, stdout='(\n    "en-RU",\n    ru\n)\n', stderr="")

    monkeypatch.setattr(i18n.subprocess, "run", run)
    assert i18n._mac_languages() == ["en-RU", "ru"]
    assert calls == [(["defaults", "read", "-g", "AppleLanguages"], 2)]


# --- which source wins ------------------------------------------------------------


@pytest.mark.parametrize(
    "flag, env, saved, system, expected",
    [
        ("ru", "en", "en", "en", "ru"),  # --lang beats everything
        (None, "en", "ru", "ru", "en"),  # TGSWEEP_LANG beats the saved choice
        (None, "auto", "en", "ru", "en"),  # auto in the variable is no variable: the saved choice
        (None, None, None, "ru", "ru"),  # nothing chosen: the system
        ("auto", None, "ru", "en", "en"),  # --lang auto drops the saved choice
        ("auto", "ru", "en", "en", "ru"),  # ... but not the variable
    ],
)
def test_where_the_language_comes_from(flag, env, saved, system, expected):
    assert pick_lang(flag, env, saved, system) == expected


def test_a_wrong_variable_is_an_error():
    with pytest.raises(ValueError):
        pick_lang(None, "de", None, "ru")


# --- the command ------------------------------------------------------------------


@pytest.fixture
def no_terminal(monkeypatch):
    """main() stops at the terminal check, after the language has been chosen and saved."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)


def test_lang_flag_is_kept_and_auto_forgets_it(monkeypatch, capsys, no_terminal):
    monkeypatch.setattr(i18n, "system_lang", lambda: "en")
    home = Home(default_root())
    assert cli.main(["--lang", "ru"]) == 2
    out, err = capsys.readouterr()
    assert out == "Язык сохранён: русский. Вернуть выбор по языку системы: tgsweep --lang auto\n"
    assert err == "tgsweep работает только в интерактивном терминале.\n"
    assert json.loads(home.settings_file.read_text(encoding="utf-8")) == {"lang": "ru"}
    assert_mode(home.settings_file, 0o600)

    assert cli.main([]) == 2  # the next run, without the flag, on an English system
    assert capsys.readouterr() == ("", "tgsweep работает только в интерактивном терминале.\n")
    assert cli.main(["--lang", "ru"]) == 2  # the same choice again: nothing to say
    assert capsys.readouterr().out == ""

    assert cli.main(["--lang", "auto"]) == 2
    assert capsys.readouterr() == ("The language follows the system again.\n", "tgsweep only works in an interactive terminal.\n")
    assert not home.settings_file.exists()
    assert cli.main(["--lang", "auto"]) == 2  # nothing was saved: nothing to say
    assert capsys.readouterr().out == ""


def test_english_choice_is_announced_in_english(capsys, no_terminal):
    assert cli.main(["--lang", "en"]) == 2
    assert capsys.readouterr() == (
        "Language saved: English. To follow the system language again: tgsweep --lang auto\n",
        "tgsweep only works in an interactive terminal.\n",
    )


@pytest.mark.parametrize("content", ["{broken", '["ru"]', '{"lang": "de"}'])
def test_a_broken_settings_file_means_no_choice(monkeypatch, capsys, no_terminal, content):
    monkeypatch.setattr(i18n, "system_lang", lambda: "en")
    home = Home(default_root())
    home.ensure()
    home.settings_file.write_text(content, encoding="utf-8")
    assert cli.main([]) == 2
    assert capsys.readouterr().err == "tgsweep only works in an interactive terminal.\n"
    assert i18n.get_lang() == "en"


def test_the_variable_beats_the_saved_choice(monkeypatch, capsys, no_terminal):
    Home(default_root()).save_lang("ru")
    monkeypatch.setenv("TGSWEEP_LANG", "en")
    assert cli.main([]) == 2
    assert capsys.readouterr().err == "tgsweep only works in an interactive terminal.\n"


@pytest.mark.parametrize("argv", [[], ["--help"]])
def test_a_wrong_variable_stops_the_command(monkeypatch, capsys, argv):
    monkeypatch.setenv("TGSWEEP_LANG", "de")
    assert cli.main(argv) == 2
    assert capsys.readouterr() == ("", "TGSWEEP_LANG может быть ru, en или auto, а сейчас: de\n")


def test_a_wrong_flag_is_refused_by_argparse(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--lang", "de"])
    assert exc.value.code == 2
    assert "--lang" in capsys.readouterr().err
    assert not Home(default_root()).settings_file.exists()


def test_a_flag_without_value_is_refused_with_the_full_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--lang"])
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("usage: tgsweep [-h] [--code] [--logout]")


def command_help(monkeypatch, capsys, lang: str) -> str:
    monkeypatch.setenv("TGSWEEP_LANG", lang)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    return capsys.readouterr().out


def test_help_in_english(monkeypatch, capsys):
    text = command_help(monkeypatch, capsys, "en")
    assert "program language" in text and "Deletes your messages" in text
    assert not CYRILLIC.search(text)


def test_help_in_russian(monkeypatch, capsys):
    text = command_help(monkeypatch, capsys, "ru")
    assert "язык программы" in text and "Удаляет ваши сообщения" in text


# --- the whole run in English -----------------------------------------------------


@pytest.fixture
def laptop(tmp_path):
    h = Home(tmp_path / "tgsweep-home")
    h.ensure()
    return h


async def test_the_whole_run_in_english(laptop):
    i18n.set_lang("en")
    client, w = world()
    script = ScriptedUI(retention=0, pick=pick_titled("Chat with friends"))
    assert await run_sweep(client, script, laptop) == 0
    left = client.ids_in(w["chat"])
    assert not set(w["fresh"]) & set(left) and w["theirs"] in left
    assert w["work"] in client.ids_in(w["other"])

    assert "Left alone:" in script.text
    assert "To be deleted:" in script.text
    assert "Done. Messages deleted:" in script.text
    assert [q for q in script.questions if q.startswith("To confirm, type the number ")]
    shown = script.lines + script.questions + (script.rows or []) + [s for s in script.status if s]
    assert [line for line in shown if CYRILLIC.search(line)] == []  # the test chats have Latin names
