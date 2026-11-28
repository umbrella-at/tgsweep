"""`tgsweep`: delete your own messages from the Telegram chats you pick, on this computer."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import sys
from typing import Callable

from telethon import TelegramClient, errors
from telethon.errors import RPCError

from . import __version__, i18n, signin
from .app import UI, Stopper, sweep
from .errors import ConfigError
from .home import Home, default_root, load_credentials, parse_credentials, save_credentials, shown_path
from .i18n import t
from .net import SESSION_LOST_ERRORS

CREDENTIAL_ATTEMPTS = 3

# Telegram refuses these keys: they were mistyped, or the app was deleted on my.telegram.org.
KEYS_REFUSED = (errors.ApiIdInvalidError, errors.ApiIdPublishedFloodError)


def credentials_help() -> str:
    return "\n".join(t(key) for key in ("creds_intro", "creds_step1", "creds_step2", "creds_step3", "creds_saved"))


def login_help() -> str:
    return t("login_help")

ClientFactory = Callable[..., TelegramClient]


def make_client(home: Home, api_id: int, api_hash: str, *, receive_updates: bool) -> TelegramClient:
    client = TelegramClient(
        str(home.session_base),
        api_id,
        api_hash,
        device_model="tgsweep",
        system_version=f"{platform.system()} {platform.release()}".strip(),
        app_version=f"tgsweep {__version__}",
        receive_updates=receive_updates,
        flood_sleep_threshold=60,
    )
    home.secure_session()
    return client


def ask_credentials(home: Home, ui: UI) -> tuple[int, str]:
    ui.say(credentials_help())
    for _ in range(CREDENTIAL_ATTEMPTS):
        try:
            api_id, api_hash = parse_credentials(ui.ask("api_id: "), ui.ask_secret(t("api_hash_prompt")))
        except ConfigError as exc:
            ui.say(t("creds_rejected", error=exc))
            continue
        save_credentials(home, api_id, api_hash)
        return api_id, api_hash
    raise ConfigError(t("creds_three_times"))


async def log_out_here(client, home: Home, ui: UI) -> None:
    """Ends only this computer's session; the account and its other devices stay signed in.

    Telethon's log_out returns False when Telegram answers with an error, and then the session is
    still open. The file is closed before it is removed: Windows cannot delete an open file.
    """
    try:
        done = await client.log_out()
    except (OSError, RPCError):
        done = False
    await forget(client, home)
    if done:
        ui.say(t("signed_out"))
    else:
        ui.say(t("sign_out_unconfirmed"))


async def forget(client, home: Home) -> None:
    if client.is_connected():
        await client.disconnect()  # closes the session file
    home.forget_session()


async def run(args: argparse.Namespace, home: Home, ui: UI, *, client_factory: ClientFactory = make_client,
              stopper: Stopper | None = None) -> int:
    home.ensure()
    creds = load_credentials(home) or ask_credentials(home, ui)

    if args.logout:
        if not home.session_file.exists():
            ui.say(t("no_saved_login"))
            return 0
        client = client_factory(home, *creds, receive_updates=False)
        await client.connect()
        try:
            await log_out_here(client, home, ui)
        finally:
            await client.disconnect()
        return 0

    # The QR sign-in learns about the scan from an update, so only this client receives them.
    client = client_factory(home, *creds, receive_updates=True)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            ui.say(login_help())
            if args.code:
                phone = ui.ask(t("phone_prompt")).strip()
                await signin.code_login(client, phone, ask=ui.ask, ask_secret=ui.ask_secret, say=ui.say)
                ui.say(t("signed_in"))
            else:
                await qr_sign_in(client, ui)
    except KEYS_REFUSED:
        home.credentials_file.unlink(missing_ok=True)
        raise ConfigError(t("keys_refused")) from None
    finally:
        await client.disconnect()
        home.secure_session()

    client = client_factory(home, *creds, receive_updates=False)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ConfigError(t("login_incomplete"))
        try:
            code = await sweep(client, ui, home, stopper=stopper)
        except SESSION_LOST_ERRORS:
            await forget(client, home)
            ui.say(t("login_expired"))
            return 1
        if await ui.yes_no(t("ask_sign_out"), default=True):
            await log_out_here(client, home, ui)
        else:
            ui.say(t("login_kept", root=shown_path(home.root)))
        return code
    finally:
        if client.is_connected():
            await client.disconnect()
        home.secure_session()


async def qr_sign_in(client, ui: UI) -> None:
    """Sign in by QR code. The code is erased once it is no longer needed: one code on the screen at a time."""
    with ui.transient() as block:
        await signin.qr_login(
            client,
            ask_secret=ui.ask_secret,
            say=ui.say,
            show=lambda url: block.show("\n" + signin.render_qr(url, color=block.colors)),
            clear=block.clear,
        )
    ui.say(t("signed_in"))


def enable_ansi() -> bool:
    """Whether escape codes work: the QR code is drawn with ANSI colours and erased with cursor codes.

    Elsewhere they always do; the old Windows console understands them only when asked to.
    """
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def terminal_ui(home: Home | None = None):
    """The UI of the command: escape codes only for a terminal that understands them. A line never
    breaks inside the program's folder or a path in it, written as the program prints it."""
    from .ui import TerminalUI

    isatty = getattr(sys.stdout, "isatty", None)
    whole = (shown_path(home.root),) if home else ()
    return TerminalUI(ansi=enable_ansi() and bool(isatty and isatty()), whole=whole)


def quiet_library_logs(ui) -> None:
    """Telethon warns about reconnects on its own logger; without this they would cut into the status line."""
    from .ui import SayHandler

    lib = logging.getLogger("telethon")
    lib.handlers[:] = [SayHandler(ui)]
    lib.setLevel(logging.WARNING)
    lib.propagate = False


def lang_parser() -> argparse.ArgumentParser:
    """Only --lang, read before the full parser so that its help comes out in the chosen language."""
    p = argparse.ArgumentParser(prog="tgsweep", add_help=False)
    p.add_argument("--lang", nargs="?")  # a missing value is the full parser's to refuse
    return p


class HelpFormatter(argparse.HelpFormatter):
    """The usage line starts with the program's word for it, not argparse's English one."""

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, t("help_usage") if prefix is None else prefix)


def parser() -> argparse.ArgumentParser:
    # argparse's own -h and its "options" heading are English: both come from the program's texts instead.
    # The built-in group stays empty, and argparse leaves an empty group out of the help.
    p = argparse.ArgumentParser(
        prog="tgsweep", description=t("help_description"), add_help=False, formatter_class=HelpFormatter
    )
    options = p.add_argument_group(t("help_options"))
    options.add_argument("-h", "--help", action="help", help=t("help_help"))
    options.add_argument("--code", action="store_true", help=t("help_code"))
    options.add_argument("--logout", action="store_true", help=t("help_logout"))
    options.add_argument("--lang", choices=i18n.LANG_CHOICES, help=t("help_lang"))
    options.add_argument("--version", action="version", version=f"tgsweep {__version__}", help=t("help_version"))
    return p


def remember_lang(home: Home, choice: str | None, saved: str | None) -> str | None:
    """Keeps --lang ru/en for the next runs, forgets it on --lang auto. The line to say when the choice changed."""
    if choice in i18n.LANGS:
        if choice == saved:
            return None
        home.save_lang(choice)
        return t("lang_saved")
    if choice == "auto":
        home.forget_lang()
        return t("lang_auto") if saved else None
    return None


def file_error(exc: OSError) -> str:
    name = exc.filename
    return t("file_error", filename=shown_path(name) if isinstance(name, (str, os.PathLike)) else name or "",
             reason=exc.strerror or exc)


def main(argv: list[str] | None = None) -> int:
    early, _ = lang_parser().parse_known_args(argv)
    flag = early.lang if early.lang in i18n.LANG_CHOICES else None  # a wrong value is the full parser's to refuse
    home = Home(default_root())
    system = i18n.system_lang()
    saved = home.saved_lang()
    try:
        i18n.set_lang(i18n.pick_lang(flag, os.environ.get(i18n.LANG_ENV), saved, system))
    except ValueError:
        i18n.set_lang(system)
        print(t("lang_env_bad", value=os.environ.get(i18n.LANG_ENV, "")), file=sys.stderr)
        return 2
    args = parser().parse_args(argv)
    try:
        note = remember_lang(home, args.lang, saved)
    except OSError as exc:
        print(file_error(exc), file=sys.stderr)
        return 1
    if note:
        print(note, flush=True)
    if not sys.stdin.isatty():
        print(t("needs_terminal"), file=sys.stderr)
        return 2
    ui = terminal_ui(home)
    quiet_library_logs(ui)
    try:
        return asyncio.run(run(args, home, ui))
    except (KeyboardInterrupt, EOFError):
        print(t("interrupted"), file=sys.stderr)
        return 130
    except (ConfigError, signin.SignInError) as exc:
        print(t("error", error=exc), file=sys.stderr)
        return 1
    except (ConnectionError, TimeoutError, RPCError) as exc:
        print(t("network_error", name=exc.__class__.__name__), file=sys.stderr)
        return 1
    except OSError as exc:
        print(file_error(exc), file=sys.stderr)
        return 1
