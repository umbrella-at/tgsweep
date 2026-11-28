"""Paths to the program's own files as the program prints them: on macOS and Linux a path inside the
home folder starts with ~, so screenshots in issues and chats show no user name; outside the home
folder and on Windows a path is printed whole."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path, PurePosixPath

import pytest

from tgsweep import app, cli, i18n
from tgsweep.cleanup import ApplyReport
from tgsweep.errors import ConfigError
from tgsweep.home import Home, load_credentials, shown_path

SRC = Path(__file__).resolve().parent.parent / "src" / "tgsweep"
posix_only = pytest.mark.skipif(os.name == "nt", reason="the home folder is read from HOME on macOS and Linux")


@pytest.mark.parametrize("home, path, expected", [
    ("/Users/maria", "/Users/maria/Library/Application Support/tgsweep/logs/actions.jsonl",
     "~/Library/Application Support/tgsweep/logs/actions.jsonl"),
    ("/home/maria", "/home/maria/.local/share/tgsweep", "~/.local/share/tgsweep"),
    ("/Users/maria", "/Users/maria", "~"),
    ("/Users/maria/", "/Users/maria/Library", "~/Library"),
], ids=["macOS journal", "Linux folder", "the home folder itself", "HOME with a slash at the end"])
def test_a_path_inside_the_home_folder_starts_with_a_tilde(home, path, expected):
    assert shown_path(path, home=home, windows=False) == expected
    # Path objects as well as text; posix ones, since a Windows path never reaches this branch.
    assert shown_path(PurePosixPath(path), home=PurePosixPath(home), windows=False) == expected


@pytest.mark.parametrize("home, path", [
    ("/Users/maria", "/srv/tgsweep/logs/actions.jsonl"),
    ("/Users/maria", "/Users/maria2/tgsweep"),
    ("/Users/maria", "/Users"),
    ("/Users/maria", "Application Support/tgsweep"),
    ("/", "/srv/tgsweep"),
    ("", "/srv/tgsweep"),
], ids=["TGSWEEP_HOME elsewhere", "a neighbour with a longer name", "above the home folder", "a relative path",
        "HOME is /", "HOME is empty"])
def test_a_path_outside_the_home_folder_is_printed_whole(home, path):
    assert shown_path(path, home=home, windows=False) == path


def test_windows_paths_are_printed_as_they_are():
    journal = r"C:\Users\maria\AppData\Local\tgsweep\logs\actions.jsonl"
    assert shown_path(journal, home=r"C:\Users\maria", windows=True) == journal
    assert shown_path("/Users/maria/Library", home="/Users/maria", windows=True) == "/Users/maria/Library"


@pytest.mark.skipif(os.name != "nt", reason="on Windows itself")
def test_on_windows_the_real_home_folder_stays_whole():
    inside = Path.home() / "AppData" / "Local" / "tgsweep"
    assert shown_path(inside) == str(inside)


@posix_only
def test_the_home_folder_comes_from_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert shown_path(tmp_path / "Library" / "Application Support" / "tgsweep") == "~/Library/Application Support/tgsweep"


# --- every place where the program prints a path to its files ----------------------------------

PLACEHOLDERS = ("path", "root", "filename")


def calls_with_paths() -> list[tuple[str, str, ast.expr]]:
    """(file:line, text key, value) of every t() call that fills a path placeholder."""
    found = []
    for module in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "t"):
                continue
            if not (node.args and isinstance(node.args[0], ast.Constant)):
                continue
            for keyword in node.keywords:
                if keyword.arg in PLACEHOLDERS:
                    found.append((f"{module.name}:{node.lineno}", node.args[0].value, keyword.value))
    return found


def is_shown(value: ast.expr) -> bool:
    if isinstance(value, ast.IfExp):  # the file name of an OSError: a path, or nothing to show
        return is_shown(value.body)
    return isinstance(value, ast.Call) and getattr(value.func, "id", None) == "shown_path"


def test_every_path_the_program_prints_goes_through_shown_path():
    # The texts that show a path, from the text table itself: a new one is caught here too.
    with_paths = {key for key, langs in i18n.TEXTS.items()
                  if any(re.search(r"\{(path|root|filename)\}", text) for text in langs.values())}
    assert with_paths == {"report_journal", "login_kept", "file_damaged", "file_error"}
    calls = calls_with_paths()
    assert {key for _, key, _ in calls} == with_paths
    assert len(calls) == 5  # file_damaged twice
    assert [(where, key) for where, key, value in calls if not is_shown(value)] == []


class Said:
    def __init__(self):
        self.lines: list[str] = []

    def say(self, text: str) -> None:
        self.lines.append(text)


@posix_only
def test_the_report_names_the_journal_with_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = Home(tmp_path / "Library" / "Application Support" / "tgsweep")
    ui = Said()
    app._say_report(ui, ApplyReport(plan_id="p"), home)
    assert ui.lines[-1].endswith("Журнал без текстов сообщений: ~/Library/Application Support/tgsweep/logs/actions.jsonl")
    assert str(tmp_path) not in ui.lines[-1]


@posix_only
def test_a_damaged_file_and_a_file_error_name_the_file_with_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = Home(tmp_path / ".local" / "share" / "tgsweep")
    home.root.mkdir(parents=True)
    home.credentials_file.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError) as damaged:
        load_credentials(home, env={})
    assert str(damaged.value).startswith("файл ~/.local/share/tgsweep/credentials.json повреждён")
    error = PermissionError(13, "Permission denied", str(home.credentials_file))
    assert cli.file_error(error) == "Ошибка доступа к файлу ~/.local/share/tgsweep/credentials.json: Permission denied"
    assert cli.file_error(OSError(5, "Input/output error")) == "Ошибка доступа к файлу : Input/output error"


@posix_only
def test_a_line_never_breaks_inside_the_folder_as_printed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = Home(tmp_path / "Library" / "Application Support" / "tgsweep")
    assert cli.terminal_ui(home).whole == ("~/Library/Application Support/tgsweep",)


@posix_only
def test_a_folder_set_elsewhere_is_printed_whole(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "maria"))
    home = Home(tmp_path / "elsewhere" / "tgsweep")
    ui = Said()
    app._say_report(ui, ApplyReport(plan_id="p"), home)
    assert ui.lines[-1].endswith(str(home.journal_file))
