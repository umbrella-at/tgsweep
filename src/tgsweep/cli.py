"""`tgsweep`: delete your own messages from the Telegram chats you pick, on this computer."""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys
from typing import Callable

from telethon import TelegramClient, errors
from telethon.errors import RPCError

from . import __version__, signin
from .app import UI, Stopper, sweep
from .errors import ConfigError
from .home import Home, default_root, load_credentials, parse_credentials, save_credentials
from .net import SESSION_LOST_ERRORS

CREDENTIAL_ATTEMPTS = 3

CREDENTIALS_HELP = """Для работы нужны api_id и api_hash. Их выдаёт сам Telegram, это бесплатно:
  1. Откройте https://my.telegram.org и войдите по номеру телефона.
  2. Выберите API development tools и создайте приложение, название любое.
  3. Скопируйте сюда App api_id и App api_hash.
Они сохранятся только на этом компьютере."""

# Telegram refuses these keys: they were mistyped, or the app was deleted on my.telegram.org.
KEYS_REFUSED = (errors.ApiIdInvalidError, errors.ApiIdPublishedFloodError)

LOGIN_HELP = "Вход в Telegram. tgsweep увидит только группы и каналы, личные чаты он не открывает."

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
    ui.say(CREDENTIALS_HELP)
    for _ in range(CREDENTIAL_ATTEMPTS):
        try:
            api_id, api_hash = parse_credentials(ui.ask("api_id: "), ui.ask_secret("api_hash (при вводе не виден): "))
        except ConfigError as exc:
            ui.say(f"Не подошло: {exc}.")
            continue
        save_credentials(home, api_id, api_hash)
        return api_id, api_hash
    raise ConfigError("api_id и api_hash не подошли три раза подряд, запустите tgsweep ещё раз")


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
        ui.say("Вы вышли из аккаунта на этом компьютере.")
    else:
        ui.say("Telegram не подтвердил выход. Файл входа на этом компьютере удалён, а сессию tgsweep "
               "завершите в Telegram: Настройки, Устройства.")


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
            ui.say("На этом компьютере вход не сохранён.")
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
            ui.say(LOGIN_HELP)
            if args.code:
                phone = ui.ask("Телефон в международном формате: ").strip()
                await signin.code_login(client, phone, ask=ui.ask, ask_secret=ui.ask_secret, say=ui.say)
            else:
                await signin.qr_login(client, ask_secret=ui.ask_secret, say=ui.say, show=signin.show_qr)
    except KEYS_REFUSED:
        home.credentials_file.unlink(missing_ok=True)
        raise ConfigError(
            "Telegram не принял api_id и api_hash. Проверьте их на my.telegram.org и запустите tgsweep ещё раз: "
            "он спросит их заново"
        ) from None
    finally:
        await client.disconnect()
        home.secure_session()

    client = client_factory(home, *creds, receive_updates=False)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ConfigError("вход не завершён, запустите tgsweep ещё раз")
        try:
            code = await sweep(client, ui, home, stopper=stopper)
        except SESSION_LOST_ERRORS:
            await forget(client, home)
            ui.say("Вход на этом компьютере больше не действует. Запустите tgsweep ещё раз и войдите заново.")
            return 1
        if await ui.yes_no("Выйти из аккаунта на этом компьютере? Тогда в следующий раз снова понадобится QR-код.", default=True):
            await log_out_here(client, home, ui)
        else:
            ui.say(f"Вход сохранён в {home.root}. Выйти можно командой: tgsweep --logout")
        return code
    finally:
        if client.is_connected():
            await client.disconnect()
        home.secure_session()


def enable_ansi() -> None:
    """The QR code is drawn with ANSI colours; the old Windows console shows them only when asked to."""
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tgsweep",
        description="Удаляет ваши сообщения, комментарии и реакции в группах Telegram, которые вы отметите. "
        "Личные чаты не открывает.",
    )
    p.add_argument("--code", action="store_true", help="войти по коду из Telegram вместо QR-кода")
    p.add_argument("--logout", action="store_true", help="выйти из аккаунта на этом компьютере и удалить файл входа")
    p.add_argument("--version", action="version", version=f"tgsweep {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not sys.stdin.isatty():
        print("tgsweep работает только в интерактивном терминале.", file=sys.stderr)
        return 2
    enable_ansi()
    from .ui import TerminalUI

    home = Home(default_root())
    try:
        return asyncio.run(run(args, home, TerminalUI()))
    except (KeyboardInterrupt, EOFError):
        print("\nПрервано.", file=sys.stderr)
        return 130
    except (ConfigError, signin.SignInError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except (ConnectionError, TimeoutError, RPCError) as exc:
        print(f"Ошибка связи с Telegram ({exc.__class__.__name__}). Проверьте интернет и запустите ещё раз.", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Ошибка доступа к файлу {exc.filename or ''}: {exc.strerror or exc}", file=sys.stderr)
        return 1
