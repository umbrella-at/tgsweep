"""The README recordings come from scripts/demo.py: its made-up world must still give the numbers they show.

When the program changes, these tests say so before a recording quietly turns out different.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from fake import NOW, FakeClient
from test_laptop import ScriptedUI, run_sweep
from tgsweep.home import Home
from tgsweep.i18n import set_lang

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "scripts" / "demo.py"
READMES = [REPO / "README.md", REPO / "README.ru.md"]
MAX_GIF = 3 * 1024 * 1024


def load_demo():
    spec = importlib.util.spec_from_file_location("tgsweep_demo", DEMO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPECTED = {
    "en": {
        "account": "Account: Maria (@maria_demo)",
        "took": "21 chats checked.",
        "left": ["Left alone:", "  private chats and bots (never opened): 3",
                 "  chats you created (skipped so your own group is not emptied): 1",
                 "  channels (delete your own posts in Telegram itself, comments on posts are searched separately): 1"],
        "rows": ["Building neighbors: 37 messages", "Book club: 12 messages, 4 reactions",
                 "Morning runs: 8 messages", "Neighborhood notices: 3 messages, 2 reactions"],
        "will": "To be deleted: 49 messages, 4 reactions in 2 chats. This cannot be undone. It will take about 1 minute.",
        "confirm": "To confirm, type the number 53:",
        "done": "Done. Messages deleted: 49, comments deleted: 0, reactions removed: 4.",
    },
    "ru": {
        "account": "Аккаунт: Мария (@maria_demo)",
        "took": "просмотрено 21 чат.",
        "left": ["Не трогаю:", "  личные чаты и боты (их не открываю): 3",
                 "  чаты, которые вы создали (пропускаю, чтобы не опустошить вашу же группу): 1",
                 "  каналы (свои посты удаляйте в самом Telegram, комментарии к постам ищу отдельно): 1"],
        "rows": ["Соседи по дому: 37 сообщений", "Книжный клуб: 12 сообщений, 4 реакции",
                 "Бег по утрам: 8 сообщений", "Объявления района: 3 сообщения, 2 реакции"],
        "will": "Будет удалено: 49 сообщений, 4 реакции в 2 чатах. Вернуть их будет нельзя. Это займёт примерно 1 минуту.",
        "confirm": "Чтобы подтвердить, введите число 53:",
        "done": "Готово. Удалено сообщений: 49, комментариев: 0, снято реакций: 4.",
    },
}


@pytest.mark.anyio
@pytest.mark.parametrize("lang", ["en", "ru"])
async def test_the_demo_world_gives_the_numbers_of_the_recording(lang, tmp_path):
    set_lang(lang)
    demo = load_demo()
    client = demo.build_world(lang, NOW, FakeClient())
    home = Home(tmp_path / "home")
    home.ensure()
    ui = ScriptedUI(retention=7, pick=lambda rows: [0, 1])
    assert await run_sweep(client, ui, home) == 0

    want = EXPECTED[lang]
    lines = ui.text.splitlines()
    assert lines[0] == want["account"]
    took = next(line for line in lines if line.startswith(("Search took", "Поиск занял")))
    assert took.endswith(want["took"]), took
    start = lines.index(want["left"][0])
    assert lines[start:start + 4] == want["left"]
    assert ui.rows == want["rows"]
    assert want["will"] in lines
    assert ui.questions[-1] == want["confirm"]
    assert want["done"] in lines
    assert sum(len(ids) for _, ids in client.delete_calls) == 49
    unreacted = [r for r in client.requests if type(r).__name__ == "SendReactionRequest"]
    assert len(unreacted) == 4


def test_the_demo_hands_out_only_the_made_up_client():
    demo = load_demo()
    make = demo.factory_for(FakeClient())
    assert isinstance(make(None, 1, "x", receive_updates=True), FakeClient)
    with pytest.raises(SystemExit):
        demo.factory_for(object())(None, 1, "x", receive_updates=False)


def test_the_demo_never_makes_a_real_telegram_client():
    tree = ast.parse(DEMO.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "TelegramClient" not in names | imported
    assert "make_client" not in names | imported  # the program's factory of real clients
    assert not any(m and m.startswith("telethon") for m in modules) and "telethon" not in imported


@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_the_readme_recording_is_there_and_small(readme):
    gifs = re.findall(r"!\[[^\]]+\]\((docs/media/[^)]+\.gif)\)", readme.read_text(encoding="utf-8"))
    assert len(gifs) == 1
    path = REPO / gifs[0]
    assert path.is_file(), path
    assert path.stat().st_size <= MAX_GIF, (path.name, path.stat().st_size)
