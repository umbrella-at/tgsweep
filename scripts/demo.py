"""A run of tgsweep on a made-up account, for the recordings at the top of the README.

    uv run scripts/demo.py en
    uv run scripts/demo.py ru

The program itself runs: cli.run, sweep and the terminal questions. Only Telegram is a stand-in,
FakeClient from tests/fake.py, filled with Maria's made-up chats, the same as on the site. The
client factory hands out that stand-in and nothing else, so the run never reaches the network.

The pauses are shortened: each request to the stand-in takes a moment, so the search runs a few
seconds with its status line, and the pauses between delete requests are a tenth of the real ones.
The sign-in is already done, so there is no QR code. Everything is kept in a temporary folder that
goes away at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from fake import FakeClient  # noqa: E402

from tgsweep import app, cli, i18n  # noqa: E402
from tgsweep.home import API_HASH_ENV, API_ID_ENV, HOME_ENV, Home, default_root  # noqa: E402

ACCOUNT = {"en": "Maria", "ru": "Мария"}
USERNAME = "maria_demo"
API_ID = "12345678"
API_HASH = "0123456789abcdef0123456789abcdef"

# Groups in the order of the chat list: (en, ru, Maria's messages older than 7 days, her reactions on
# older messages). The four with something to delete come first, so by chat 9 the search has found
# all 60 messages, as in the site's picture.
GROUPS = [
    ("Building neighbors", "Соседи по дому", 37, 0),
    ("Yoga in the park", "Йога в парке", 0, 0),
    ("Book club", "Книжный клуб", 12, 4),
    ("Board game nights", "Настольные игры", 0, 0),
    ("Morning runs", "Бег по утрам", 8, 0),
    ("Dog walkers", "Собачники района", 0, 0),
    ("Neighborhood notices", "Объявления района", 3, 2),
    ("Plant swap", "Обмен растениями", 0, 0),
    ("Spanish club", "Испанский клуб", 0, 0),
    ("Weekend hikes", "Походы выходного дня", 0, 0),
    ("School parents", "Родители класса", 0, 0),
    ("Bike rides", "Велопрогулки", 0, 0),
    ("Photo walks", "Фотопрогулки", 0, 0),
    ("Recipes", "Рецепты", 0, 0),
    ("Flat renovation", "Ремонт в квартире", 0, 0),
    ("Holiday plans", "Планы на отпуск", 0, 0),
    ("Choir", "Хор", 0, 0),
    ("Pool on Saturdays", "Бассейн по субботам", 0, 0),
    ("Shelter volunteers", "Волонтёры приюта", 0, 0),
    ("Flea market", "Барахолка района", 0, 0),
    ("Football in the yard", "Футбол во дворе", 0, 0),
]
CREATED = ("Family", "Семья")  # Maria created it: skipped so her own group is not emptied
CHANNEL = ("Garden notes", "Заметки о саде")  # a channel: skipped
PEOPLE = [("Anna", "Анна"), ("Pavel", "Павел")]  # private chats: never opened
BOT = ("Weather bot", "Бот погоды")

# How long the stand-in takes to answer, and how much shorter the program's own pauses are.
DIALOG_S = 0.03
READ_S = 0.1
REQUEST_S = 0.1
DELETE_S = 0.4
PAUSE_SCALE = 0.1


class PacedClient(FakeClient):
    """FakeClient that answers at the pace of a real account: each request takes a moment."""

    async def iter_dialogs(self, *args, **kwargs):
        async for dialog in super().iter_dialogs(*args, **kwargs):
            await asyncio.sleep(DIALOG_S)
            yield dialog

    async def iter_messages(self, *args, **kwargs):
        await asyncio.sleep(READ_S)
        async for msg in super().iter_messages(*args, **kwargs):
            yield msg

    async def delete_messages(self, *args, **kwargs):
        await asyncio.sleep(DELETE_S)
        return await super().delete_messages(*args, **kwargs)

    async def __call__(self, request):
        await asyncio.sleep(REQUEST_S)
        return await super().__call__(request)


def build_world(lang: str, now: datetime, client: FakeClient | None = None) -> FakeClient:
    """Maria's account: 21 groups to check, 3 private chats and a bot, her own group and a channel."""
    pick = 0 if lang == "en" else 1
    client = client if client is not None else PacedClient()
    client.me.first_name = ACCOUNT[lang]
    client.me.username = USERNAME

    def days_ago(days: float):
        return now - timedelta(days=days)

    # Private chats come first in the list, as recent chats do; their order sets the order of the skip notes.
    for n, person in enumerate(PEOPLE):
        user = client.add_user(4000 + n, person[pick])
        client.add_message(user, client.me.id, "", date=days_ago(15))
    client.add_user(4100, BOT[pick], bot=True)

    neighbor = 5000  # ids of the other members; no names are ever shown
    for n, group in enumerate(GROUPS):
        title, old_messages, old_reactions = group[pick], group[2], group[3]
        chat = client.add_channel(2000 + n, title)
        others = [neighbor + n * 10 + k for k in range(4)]
        said = []  # (days ago, sender, my reaction, someone else's reaction)
        for k in range(10):  # the others talk, before and after the cutoff
            said.append((40 - k * 4 + 0.3, others[k % 4], False, k % 4 == 0))
        for k in range(old_messages):
            said.append((9 + (k * 7) % 70 + k / 100, client.me.id, False, False))
        for k in range(old_reactions):
            said.append((12 + k * 5, others[k % 4], True, False))
        for k in range(n % 3 + 1):  # fresh ones stay: they are younger than 7 days
            said.append((k + 0.5, client.me.id, False, False))
        if n == 4:  # a fresh reaction stays too
            said.append((1, others[0], True, False))
        # Telegram numbers the messages of a chat in the order they were sent.
        for days, sender, mine, theirs in sorted(said, reverse=True):
            client.add_message(chat, sender, "", date=days_ago(days), my_reaction=mine, other_reaction=theirs)

    created = client.add_channel(3000, CREATED[pick], creator=True)
    client.add_message(created, client.me.id, "", date=days_ago(20))
    channel = client.add_channel(3100, CHANNEL[pick], broadcast=True)
    client.add_message(channel, client.me.id, "", date=days_ago(30))
    return client


def factory_for(client: FakeClient):
    """The client factory of cli.run: always the stand-in, never a real Telegram client."""

    def make(home, api_id, api_hash, *, receive_updates):
        if not isinstance(client, FakeClient):
            raise SystemExit("demo: the client is not the made-up one, the run stops before any network")
        return client

    return make


async def short_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds * PAUSE_SCALE)


async def paced_sweep(client, ui, home, **kwargs):
    """The program's own sweep, with its pauses shortened."""
    return await app.sweep(client, ui, home, sleep=short_sleep, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo.py", description="tgsweep on a made-up account")
    parser.add_argument("lang", choices=["en", "ru"])
    lang = parser.parse_args(argv).lang
    with tempfile.TemporaryDirectory(prefix="tgsweep-demo-", dir="/tmp" if os.path.isdir("/tmp") else None) as tmp:
        os.environ[HOME_ENV] = tmp
        os.environ[API_ID_ENV] = API_ID
        os.environ[API_HASH_ENV] = API_HASH
        i18n.set_lang(lang)
        home = Home(default_root())
        client = build_world(lang, datetime.now(timezone.utc))
        cli.sweep = paced_sweep
        ui = cli.terminal_ui(home)
        args = argparse.Namespace(code=False, logout=False)
        try:
            return asyncio.run(cli.run(args, home, ui, client_factory=factory_for(client)))
        except (KeyboardInterrupt, EOFError):
            print(i18n.t("interrupted"), file=sys.stderr)
            return 130


if __name__ == "__main__":
    sys.exit(main())
