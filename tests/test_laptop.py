"""tgsweep on a laptop: the whole interactive run, driven by a scripted UI over the fake client."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from fake import ME, NOW, FakeClient
from tgsweep import app, cli, home as home_mod
from tgsweep.app import Stopper, plural, sweep
from tgsweep.cleanup import Policy
from tgsweep.errors import ConfigError
from tgsweep.home import Home, load_credentials, parse_credentials

pytestmark = pytest.mark.anyio

FRESH = NOW - timedelta(hours=2)
OLD = NOW - timedelta(days=40)
GOOD_HASH = "0123456789abcdef0123456789abcdef"
# Windows has no POSIX modes: there the data folder is private because it sits in the user profile.
POSIX_MODES = sys.platform != "win32"


def assert_mode(path, expected: int) -> None:
    if POSIX_MODES:
        assert stat.S_IMODE(os.stat(path).st_mode) == expected


class ScriptedUI:
    def __init__(self, *, retention=0, pick=None, confirm=None, keep_login=False, asks=None, secrets=None):
        self.retention = retention
        self.pick_rows = pick if pick is not None else (lambda rows: list(range(len(rows))))
        self.confirm = confirm
        self.keep_login = keep_login
        self.asks = list(asks or [])
        self.secrets = list(secrets or [])
        self.lines: list[str] = []
        self.rows: list[str] | None = None
        self.questions: list[str] = []
        self.secret_prompts: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def say(self, text):
        self.lines.append(text)

    def ask(self, prompt):
        self.questions.append(prompt)
        return self.asks.pop(0)

    async def type_in(self, prompt):
        self.questions.append(prompt)
        expected = re.fullmatch(r"Чтобы подтвердить, введите число (\d+):", prompt).group(1)
        return self.confirm(expected) if self.confirm else expected

    def ask_secret(self, prompt):
        self.secret_prompts.append(prompt)
        return self.secrets.pop(0)

    async def choose(self, question, choices):
        assert [v for _, v in choices] == [0, 1, 2, 7, 30]
        return self.retention

    async def pick(self, question, rows):
        self.rows = rows
        return self.pick_rows(rows)

    async def yes_no(self, question, default):
        assert default is True  # leaving the computer signed out is the default
        return not self.keep_login


async def no_sleep(_):
    return None


@pytest.fixture
def laptop(tmp_path):
    h = Home(tmp_path / "tgsweep-home")
    h.ensure()
    return h


def world():
    client = FakeClient()
    chat = client.add_channel(100, "Chat with friends")
    other = client.add_channel(200, "Work chat")
    mine_created = client.add_channel(300, "My own group", creator=True)
    friend = client.add_user(3000, "Friend")
    fresh = [client.add_message(chat, ME, f"today {i}", date=FRESH).id for i in range(5)]
    old = [client.add_message(chat, ME, "old", date=OLD).id]
    theirs = client.add_message(chat, 4000, "not mine", date=FRESH).id
    work = client.add_message(other, ME, "work", date=FRESH).id
    created = client.add_message(mine_created, ME, "mine in my group", date=FRESH).id
    dm = client.add_message(friend, ME, "private", date=FRESH).id
    return client, dict(chat=chat, other=other, created=mine_created, friend=friend,
                        fresh=fresh, old=old, theirs=theirs, work=work, created_msg=created, dm=dm)


async def run_sweep(client, ui, home, **kw):
    return await sweep(client, ui, home, now=lambda: NOW, sleep=no_sleep, **kw)


def pick_titled(title):
    return lambda rows: [i for i, r in enumerate(rows) if r.startswith(title)]


# --- the run -----------------------------------------------------------------


async def test_all_deletes_todays_messages_in_the_ticked_chat_only(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0, pick=pick_titled("Chat with friends"))
    assert await run_sweep(client, ui, laptop) == 0
    left = client.ids_in(w["chat"])
    assert not set(w["fresh"]) & set(left) and not set(w["old"]) & set(left)
    assert w["theirs"] in left
    assert w["work"] in client.ids_in(w["other"])  # not ticked
    assert w["created_msg"] in client.ids_in(w["created"])  # created by me: never offered
    assert w["dm"] in client.ids_in(w["friend"])  # private chat: never opened
    assert "Удалено сообщений: 6" in ui.text
    assert laptop.journal_file.exists()
    records = [json.loads(line) for line in laptop.journal_file.read_text().splitlines()]
    assert {r["msg_id"] for r in records if r["result"] == "deleted"} == set(w["fresh"]) | set(w["old"])
    assert all("text" not in r and "message" not in r for r in records)


async def test_picker_never_lists_private_or_own_chats(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0, pick=lambda rows: [])
    await run_sweep(client, ui, laptop)
    assert ui.rows == ["Chat with friends: 6 сообщений", "Work chat: 1 сообщение"]
    assert "личные чаты и боты: tgsweep их не открывает: 1" in ui.text
    assert "чаты, которые вы создали" in ui.text


async def test_one_day_leaves_fresh_messages_and_says_how_to_take_them(laptop):
    """The reported case: a chat full of messages from the last day, and 'nothing to delete'."""
    client, w = world()
    client.chat_of(w["chat"]).messages.pop(w["old"][0])
    ui = ScriptedUI(retention=1)
    assert await run_sweep(client, ui, laptop) == 0
    assert client.delete_calls == []
    assert "ваших сообщений старше 1 дня нет" in ui.text
    assert "«Все, даже сегодняшние»" in ui.text


async def test_thirty_days_takes_only_old_ones(laptop):
    client, w = world()
    ui = ScriptedUI(retention=30)
    await run_sweep(client, ui, laptop)
    left = client.ids_in(w["chat"])
    assert not set(w["old"]) & set(left)
    assert set(w["fresh"]) <= set(left)


async def test_wrong_number_deletes_nothing(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0, confirm=lambda expected: str(int(expected) + 1))
    assert await run_sweep(client, ui, laptop) == 0
    assert client.delete_calls == []
    assert "Число не совпало, ничего не удалено." in ui.text


async def test_confirmation_asks_for_the_exact_total(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0)
    await run_sweep(client, ui, laptop)
    assert "Чтобы подтвердить, введите число 7:" in ui.questions  # 6 in one chat, 1 in the other


async def test_nothing_ticked_deletes_nothing(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0, pick=lambda rows: [])
    assert await run_sweep(client, ui, laptop) == 0
    assert client.delete_calls == []
    assert "Ни один чат не отмечен" in ui.text


async def test_backing_out_of_the_question_deletes_nothing(laptop):
    client, w = world()
    ui = ScriptedUI(retention=None)
    assert await run_sweep(client, ui, laptop) == 0
    assert client.delete_calls == [] and ui.rows is None


async def test_more_than_the_limit_goes_in_batches_until_done(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0, pick=pick_titled("Chat with friends"))
    await run_sweep(client, ui, laptop, policy=Policy(max_messages_per_run=4, include_reactions=False))
    assert not set(w["fresh"] + w["old"]) & set(client.ids_in(w["chat"]))
    assert [len(ids) for _, ids in client.delete_calls] == [4, 2]
    assert "Обработано 4 из 6, продолжаю." in ui.text


async def test_stop_request_ends_between_batches(laptop):
    client, w = world()
    stopper = Stopper()
    calls = {"n": 0}
    real = client.delete_messages

    async def delete_then_stop(*a, **kw):
        calls["n"] += 1
        stopper.requested = True
        return await real(*a, **kw)

    client.delete_messages = delete_then_stop
    ui = ScriptedUI(retention=0, pick=pick_titled("Chat with friends"))
    await run_sweep(client, ui, laptop, policy=Policy(max_messages_per_run=4, include_reactions=False), stopper=stopper)
    assert calls["n"] == 1
    assert len(client.ids_in(w["chat"])) == 3  # 2 left over plus the message that is not mine
    assert "Остановлено до конца" in ui.text


async def test_zero_reaction_limit_does_not_loop(laptop, monkeypatch):
    client = FakeClient()
    chat = client.add_channel(100, "Chat")
    client.add_message(chat, 4000, "liked", date=FRESH, my_reaction=True)
    real, runs = app.cleanup.apply_plan, []

    async def counted(*a, **kw):
        runs.append(1)
        assert len(runs) < 5, "batches never end"
        return await real(*a, **kw)

    monkeypatch.setattr(app.cleanup, "apply_plan", counted)
    ui = ScriptedUI(retention=0)
    assert await run_sweep(client, ui, laptop, policy=Policy(max_reactions_per_run=0)) == 0
    assert ui.rows == ["Chat: 1 реакция"]
    assert len(runs) == 1


async def test_reactions_are_removed_and_scan_position_saved(laptop):
    client = FakeClient()
    chat = client.add_channel(100, "Chat")
    liked = client.add_message(chat, 4000, "liked", date=FRESH, my_reaction=True)
    ui = ScriptedUI(retention=0)
    await run_sweep(client, ui, laptop)
    assert "снято реакций: 1" in ui.text
    state = json.loads(laptop.state_file.read_text())
    assert state["reaction_scan"][str(-1000000000100)] >= liked.id
    assert_mode(laptop.state_file, 0o600)


async def test_build_plan_reports_progress(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0, pick=lambda rows: [])
    await run_sweep(client, ui, laptop)
    assert "  1/2: Chat with friends" in ui.lines and "  2/2: Work chat" in ui.lines


# --- sign-in, sign-out and files --------------------------------------------


def args(**kw):
    return argparse.Namespace(**{"code": False, "logout": False, **kw})


def factory_for(client, made):
    def make(home, api_id, api_hash, *, receive_updates):
        made.append((api_id, api_hash, receive_updates))
        return client
    return make


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv(home_mod.API_ID_ENV, "4242")
    monkeypatch.setenv(home_mod.API_HASH_ENV, GOOD_HASH)


async def test_first_run_signs_in_by_qr_with_updates_then_signs_out(laptop, creds, monkeypatch):
    client, w = world()
    client.authorized = False
    scanned = []

    async def fake_qr(c, ask_secret, say, show):
        scanned.append(c)
        c.authorized = True
        laptop.session_file.write_bytes(b"session")

    monkeypatch.setattr(cli.signin, "qr_login", fake_qr)
    made = []
    ui = ScriptedUI(retention=0, pick=lambda rows: [])
    assert await cli.run(args(), laptop, ui, client_factory=factory_for(client, made)) == 0
    assert scanned == [client]
    assert made == [(4242, GOOD_HASH, True), (4242, GOOD_HASH, False)]  # only the sign-in client gets updates
    assert client.logged_out is True
    assert not laptop.session_file.exists()
    assert "Вы вышли из аккаунта на этом компьютере." in ui.text


async def test_keeping_the_login_keeps_a_private_session_file(laptop, creds):
    client, _ = world()
    laptop.session_file.write_bytes(b"session")
    os.chmod(laptop.session_file, 0o644)
    ui = ScriptedUI(retention=0, pick=lambda rows: [], keep_login=True)
    await cli.run(args(), laptop, ui, client_factory=factory_for(client, []))
    assert client.logged_out is False
    assert_mode(laptop.session_file, 0o600)
    assert "tgsweep --logout" in ui.text


async def test_logout_flag_ends_the_session_and_removes_the_file(laptop, creds):
    client, _ = world()
    laptop.session_file.write_bytes(b"session")
    ui = ScriptedUI()
    assert await cli.run(args(logout=True), laptop, ui, client_factory=factory_for(client, [])) == 0
    assert client.logged_out is True and not laptop.session_file.exists()
    assert client.delete_calls == []


async def test_lost_session_is_forgotten(laptop, creds):
    from telethon import errors

    client, _ = world()
    laptop.session_file.write_bytes(b"session")

    async def revoked():
        raise errors.AuthKeyUnregisteredError(request=None)

    client.get_me = revoked
    ui = ScriptedUI()
    assert await cli.run(args(), laptop, ui, client_factory=factory_for(client, [])) == 1
    assert not laptop.session_file.exists()


async def test_credentials_are_asked_once_hidden_and_saved_private(laptop, monkeypatch):
    monkeypatch.delenv(home_mod.API_ID_ENV, raising=False)
    monkeypatch.delenv(home_mod.API_HASH_ENV, raising=False)
    client, _ = world()
    ui = ScriptedUI(retention=None, asks=["abc", "4242"], secrets=["short", GOOD_HASH.upper()])
    await cli.run(args(), laptop, ui, client_factory=factory_for(client, []))
    assert len(ui.secret_prompts) == 2  # the hash is never echoed
    assert json.loads(laptop.credentials_file.read_text()) == {"api_id": 4242, "api_hash": GOOD_HASH}
    assert_mode(laptop.credentials_file, 0o600)
    assert load_credentials(laptop, env={}) == (4242, GOOD_HASH)


def test_credentials_validation():
    assert parse_credentials(" 4242 ", GOOD_HASH) == (4242, GOOD_HASH)
    for api_id, api_hash in [("0", GOOD_HASH), ("12a", GOOD_HASH), ("4242", "xyz"), ("4242", GOOD_HASH + "0")]:
        with pytest.raises(ConfigError):
            parse_credentials(api_id, api_hash)
    with pytest.raises(ConfigError):
        load_credentials(Home(Path("/nonexistent/tgsweep")), env={home_mod.API_ID_ENV: "4242"})


def test_home_is_private(tmp_path):
    h = Home(tmp_path / "a" / "b")
    h.ensure()
    assert_mode(h.root, 0o700)


def test_existing_open_home_is_closed(tmp_path):
    root = tmp_path / "shared"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    Home(root).ensure()
    assert_mode(root, 0o700)


def test_home_env_override(tmp_path):
    assert home_mod.default_root({"TGSWEEP_HOME": str(tmp_path)}) == tmp_path


# --- texts and the command ---------------------------------------------------


def test_russian_plurals():
    assert [plural(n, "сообщение", "сообщения", "сообщений") for n in (1, 2, 5, 11, 12, 21, 22, 25, 111)] == [
        "1 сообщение", "2 сообщения", "5 сообщений", "11 сообщений", "12 сообщений",
        "21 сообщение", "22 сообщения", "25 сообщений", "111 сообщений",
    ]


def test_retention_menu_puts_all_first():
    assert app.RETENTION_CHOICES[0] == ("Все, даже сегодняшние", 0)


def test_command_refuses_without_a_terminal(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TGSWEEP_HOME", str(tmp_path / "never-created"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.main([]) == 2
    assert "интерактивном терминале" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "tgsweep 0.1.0"


async def test_backing_out_of_the_number_deletes_nothing(laptop):
    client, w = world()
    ui = ScriptedUI(retention=0, confirm=lambda expected: None)
    assert await run_sweep(client, ui, laptop) == 0
    assert client.delete_calls == []


# --- the real terminal UI, on a virtual terminal inside a running loop --------


@pytest.fixture
def terminal():
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from tgsweep.ui import TerminalUI

    with create_pipe_input() as pipe:
        yield pipe, TerminalUI(input=pipe, output=DummyOutput())


DOWN, ENTER, CTRL_C = "\x1b[B", "\r", "\x03"


async def test_terminal_choose_runs_inside_the_client_loop(terminal):
    pipe, ui = terminal
    pipe.send_text(DOWN + ENTER)
    assert await ui.choose("Q", app.RETENTION_CHOICES) == 1


async def test_terminal_pick_starts_with_nothing_ticked(terminal):
    pipe, ui = terminal
    pipe.send_text(ENTER)
    assert await ui.pick("Q", ["a", "b", "c"]) == []


async def test_terminal_pick_ticks_with_space(terminal):
    pipe, ui = terminal
    pipe.send_text(DOWN + " " + DOWN + " " + ENTER)
    assert await ui.pick("Q", ["a", "b", "c"]) == [1, 2]


async def answered(coro):
    """A KeyboardInterrupt escaping a question would end the whole program: fail the test instead."""
    try:
        return await coro
    except KeyboardInterrupt:
        pytest.fail("Ctrl+C escaped the question")


async def test_terminal_ctrl_c_backs_out(terminal):
    pipe, ui = terminal
    pipe.send_text(CTRL_C)
    assert await answered(ui.pick("Q", ["a"])) is None


async def test_terminal_typed_number(terminal):
    pipe, ui = terminal
    pipe.send_text("42" + ENTER)
    assert await ui.type_in("N:") == "42"


async def test_terminal_yes_no_defaults_to_signing_out(terminal):
    pipe, ui = terminal
    pipe.send_text(ENTER)
    assert await ui.yes_no("Q", default=True) is True


async def test_terminal_yes_no_takes_a_no(terminal):
    pipe, ui = terminal
    pipe.send_text("n")
    assert await ui.yes_no("Q", default=True) is False


async def test_terminal_ctrl_d_backs_out(terminal):
    pipe, ui = terminal
    pipe.send_text("\x04")
    assert await ui.type_in("N:") is None


async def test_terminal_ctrl_c_backs_out_of_the_number(terminal):
    pipe, ui = terminal
    pipe.send_text(CTRL_C)
    assert await answered(ui.type_in("N:")) is None


# --- review round: the count, the rows, batches of reactions -----------------


async def test_count_includes_comments_and_reactions(laptop):
    client = FakeClient()
    group = client.add_channel(100, "Group")
    channel = client.add_channel(300, "News", broadcast=True)
    talk = client.add_channel(301, "News talk", dialog=False)  # commenting needs no membership
    client.link(channel, talk)
    client.add_message(group, ME, "mine", date=FRESH)
    client.add_message(group, 4000, "theirs", date=FRESH, my_reaction=True)
    client.add_message(talk, ME, "comment", date=FRESH)
    client.add_message(talk, ME, "comment 2", date=FRESH)
    ui = ScriptedUI(retention=0)
    await run_sweep(client, ui, laptop)
    assert sorted(ui.rows) == ["Group: 1 сообщение, 1 реакция", "News (комментарии к постам): 2 комментария"]
    assert "Чтобы подтвердить, введите число 4:" in ui.questions


async def test_ticked_row_maps_to_its_chat_when_quiet_chats_come_first(laptop):
    """A chat with nothing of mine still gets a plan entry (its reaction scan position) but no row."""
    client = FakeClient()
    quiet = client.add_channel(50, "Quiet")
    client.add_message(quiet, 4000, "nothing of mine", date=FRESH)
    first = client.add_channel(100, "First")
    second = client.add_channel(200, "Second")
    a = client.add_message(first, ME, "a", date=FRESH).id
    b = client.add_message(second, ME, "b", date=FRESH).id
    ui = ScriptedUI(retention=0, pick=pick_titled("Second"))
    await run_sweep(client, ui, laptop)
    assert ui.rows == ["First: 1 сообщение", "Second: 1 сообщение"]
    assert a in client.ids_in(first) and b not in client.ids_in(second)


async def test_reactions_over_the_limit_go_in_batches_too(laptop):
    client = FakeClient()
    chat = client.add_channel(100, "Chat")
    liked = [client.add_message(chat, 4000, f"liked {i}", date=FRESH, my_reaction=True) for i in range(3)]
    ui = ScriptedUI(retention=0)
    await run_sweep(client, ui, laptop, policy=Policy(max_reactions_per_run=2))
    assert "снято реакций: 3" in ui.text
    assert not any(m._fake_my_reaction for m in liked)


async def test_unticked_chat_keeps_its_reactions_for_the_next_run(laptop):
    client = FakeClient()
    left_alone = client.add_channel(100, "Left alone")
    liked = client.add_message(left_alone, 4000, "liked", date=FRESH, my_reaction=True)
    quiet = client.add_channel(200, "Quiet")
    client.add_message(quiet, 4000, "nothing of mine", date=FRESH)
    ui = ScriptedUI(retention=0, pick=lambda rows: [])
    await run_sweep(client, ui, laptop)
    state = json.loads(laptop.state_file.read_text())["reaction_scan"]
    assert str(-1000000000100) not in state  # its reaction was found but not removed
    assert str(-1000000000200) in state  # nothing there: the next run may start later
    again = ScriptedUI(retention=0, pick=lambda rows: [])
    await run_sweep(client, again, laptop)
    assert again.rows == ["Left alone: 1 реакция"]
    assert liked._fake_my_reaction


class FlakyReactions(FakeClient):
    """SendReaction fails once for the given message ids."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fail_once: set[int] = set()

    async def __call__(self, request):
        from telethon import errors
        from telethon.tl import functions

        if isinstance(request, functions.messages.SendReactionRequest) and request.msg_id in self.fail_once:
            self.fail_once.discard(request.msg_id)
            raise errors.MessageIdInvalidError(request=None)
        return await super().__call__(request)


async def test_failed_reaction_is_found_again_after_a_later_batch(laptop):
    client = FlakyReactions()
    chat = client.add_channel(100, "Chat")
    for i in range(6):
        client.add_message(chat, ME, f"mine {i}", date=FRESH)
    bad = client.add_message(chat, 4000, "liked", date=FRESH, my_reaction=True)
    client.fail_once.add(bad.id)
    ui = ScriptedUI(retention=0)
    await run_sweep(client, ui, laptop, policy=Policy(max_messages_per_run=4))
    assert "Не удалось удалить: 1." in ui.text
    again = ScriptedUI(retention=0)
    await run_sweep(client, again, laptop)
    assert again.rows == ["Chat: 1 реакция"]
    assert not bad._fake_my_reaction


async def test_failed_reaction_goes_back_to_the_earlier_scan_position(laptop):
    client = FlakyReactions()
    chat = client.add_channel(100, "Chat")
    client.add_message(chat, 4000, "before", date=FRESH)
    client.add_message(chat, ME, "keeps the chat in the list", date=FRESH)
    await run_sweep(client, ScriptedUI(retention=0, pick=lambda rows: []), laptop)
    earlier = json.loads(laptop.state_file.read_text())["reaction_scan"][str(-1000000000100)]
    for i in range(6):
        client.add_message(chat, ME, f"mine {i}", date=FRESH)
    bad = client.add_message(chat, 4000, "liked", date=FRESH, my_reaction=True)
    client.fail_once.add(bad.id)
    await run_sweep(client, ScriptedUI(retention=0), laptop, policy=Policy(max_messages_per_run=4))
    assert json.loads(laptop.state_file.read_text())["reaction_scan"][str(-1000000000100)] == earlier
    again = ScriptedUI(retention=0)
    await run_sweep(client, again, laptop)
    assert again.rows == ["Chat: 1 реакция"]


async def test_stopper_first_ctrl_c_asks_second_aborts():
    import signal

    stopper, said = Stopper(), []
    with stopper.armed(said.append):
        try:
            signal.raise_signal(signal.SIGINT)
        except KeyboardInterrupt:
            pytest.fail("the first Ctrl+C must only ask to stop")
        assert stopper() is True and "Ctrl+C ещё раз" in said[0]
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGINT)
    assert signal.getsignal(signal.SIGINT) is not None and stopper.requested


# --- review round: sign-in and sign-out edge cases ---------------------------


def test_make_client_names_itself_and_takes_updates_only_when_asked(laptop):
    signing_in = cli.make_client(laptop, 4242, GOOD_HASH, receive_updates=True)
    working = cli.make_client(laptop, 4242, GOOD_HASH, receive_updates=False)
    assert signing_in._no_updates is False and working._no_updates is True
    assert working.session.filename == str(laptop.session_file)
    assert_mode(laptop.session_file, 0o600)
    working.session.close()
    signing_in.session.close()


async def test_sign_in_client_is_closed_before_the_working_one_opens(laptop, creds, monkeypatch):
    clients = []

    def make(home, api_id, api_hash, *, receive_updates):
        assert all(not c.connected for c in clients), "two clients on one session file"
        client, _ = world()
        clients.append(client)
        return client

    ui = ScriptedUI(retention=None)
    await cli.run(args(), laptop, ui, client_factory=make)
    assert len(clients) == 2


class ClosesFirst(Home):
    """Windows cannot delete an open session file: forgetting it while connected is a bug."""

    client = None

    def forget_session(self):
        assert not type(self).client.connected, "session file removed while the client still holds it"
        super().forget_session()


async def test_refused_logout_says_so_and_still_removes_the_file(laptop, creds):
    client, _ = world()
    home = ClosesFirst(laptop.root)
    ClosesFirst.client = client
    home.session_file.write_bytes(b"session")

    async def refused():
        return False  # what Telethon returns when Telegram answers LogOut with an error

    client.log_out = refused
    ui = ScriptedUI()
    await cli.run(args(logout=True), home, ui, client_factory=factory_for(client, []))
    assert not home.session_file.exists()
    assert "Вы вышли" not in ui.text and "Настройки, Устройства" in ui.text


async def test_logout_without_network_still_removes_the_file(laptop, creds):
    client, _ = world()
    home = ClosesFirst(laptop.root)
    ClosesFirst.client = client
    home.session_file.write_bytes(b"session")

    async def offline():
        raise ConnectionError("no network")

    client.log_out = offline
    ui = ScriptedUI()
    assert await cli.run(args(logout=True), home, ui, client_factory=factory_for(client, [])) == 0
    assert not home.session_file.exists()


async def test_lost_session_closes_the_file_before_removing_it(laptop, creds):
    from telethon import errors

    client, _ = world()
    home = ClosesFirst(laptop.root)
    ClosesFirst.client = client
    home.session_file.write_bytes(b"session")

    async def revoked():
        raise errors.AuthKeyUnregisteredError(request=None)

    client.get_me = revoked
    ui = ScriptedUI()
    assert await cli.run(args(), home, ui, client_factory=factory_for(client, [])) == 1
    assert not home.session_file.exists()


async def test_rejected_api_keys_are_forgotten(laptop, monkeypatch):
    from telethon import errors

    monkeypatch.delenv(home_mod.API_ID_ENV, raising=False)
    monkeypatch.delenv(home_mod.API_HASH_ENV, raising=False)
    home_mod.save_credentials(laptop, 4242, GOOD_HASH)
    client, _ = world()
    client.authorized = False

    async def rejected(c, ask_secret, say, show):
        raise errors.ApiIdInvalidError(request=None)

    monkeypatch.setattr(cli.signin, "qr_login", rejected)
    with pytest.raises(ConfigError, match="api_id и api_hash"):
        await cli.run(args(), laptop, ScriptedUI(), client_factory=factory_for(client, []))
    assert not laptop.credentials_file.exists()


def test_main_names_file_errors_as_file_errors(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("TGSWEEP_HOME", str(tmp_path))

    async def broken(*a, **kw):
        raise PermissionError(13, "Permission denied", str(tmp_path / "tgsweep.session"))

    monkeypatch.setattr(cli, "run", broken)
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert "интернет" not in err and "tgsweep.session" in err


def test_main_treats_ctrl_d_as_interrupt(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("TGSWEEP_HOME", str(tmp_path))

    async def eof(*a, **kw):
        raise EOFError

    monkeypatch.setattr(cli, "run", eof)
    assert cli.main([]) == 130

