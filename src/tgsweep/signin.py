"""Interactive sign-in: by QR code (the default) or by a login code.

Telethon's start() drops Telegram's answer to the code request, so a user who got no code could
not tell whether it went to the app, to SMS, to the login email or nowhere at all. Here the answer
is read and named. Telegram also lets third-party apps sign in by QR code, which needs no code.
"""

from __future__ import annotations

import asyncio
import io
import time
from typing import Callable, TextIO

from telethon import errors, functions, types

QR_TOTAL_S = 300.0
CODE_ATTEMPTS = 5
PASSWORD_ATTEMPTS = 3
SEND_CODE_ATTEMPTS = 3

Ask = Callable[[str], str]
Say = Callable[[str], None]

USE_QR = "Войдите по QR-коду: та же команда без --code."
QR_HINT = (
    "Отсканируйте код телефоном: Telegram, Настройки, Устройства, «Подключить устройство». "
    "Код меняется примерно раз в 30 секунд, новый появится здесь сам."
)

NEXT_WAY = {
    types.auth.CodeTypeSms: "по SMS",
    types.auth.CodeTypeCall: "звонком",
    types.auth.CodeTypeFlashCall: "звонком",
    types.auth.CodeTypeMissedCall: "звонком",
    types.auth.CodeTypeFragmentSms: "через Fragment",
}


class SignInError(Exception):
    """A sign-in that cannot go on. The message is for the user."""


def where(code_type: object) -> str:
    """Where Telegram says it sent the code, in words."""
    t = code_type
    if isinstance(t, types.auth.SentCodeTypeApp):
        return "в приложение Telegram на другом вашем устройстве: это сообщение в чате «Telegram» с синей галочкой, не SMS"
    if isinstance(
        t,
        (
            types.auth.SentCodeTypeSms,
            types.auth.SentCodeTypeSmsWord,
            types.auth.SentCodeTypeSmsPhrase,
            types.auth.SentCodeTypeFirebaseSms,
        ),
    ):
        return "по SMS"
    if isinstance(t, types.auth.SentCodeTypeCall):
        return "голосовым звонком"
    if isinstance(t, (types.auth.SentCodeTypeFlashCall, types.auth.SentCodeTypeMissedCall)):
        return "звонком: код это последние цифры номера, с которого позвонят"
    if isinstance(t, types.auth.SentCodeTypeEmailCode):
        return f"на почту для входа {t.email_pattern}"
    if isinstance(t, types.auth.SentCodeTypeFragmentSms):
        return f"через Fragment: {t.url}"
    return f"способом {type(t).__name__}"


def describe(sent: types.auth.SentCode) -> str:
    text = f"Код отправлен {where(sent.type)}."
    if sent.next_type is not None:
        way = NEXT_WAY.get(type(sent.next_type), "другим способом")
        after = f" за {sent.timeout} с" if sent.timeout else ""
        text += f" Если он не придёт{after}, нажмите Enter без кода: Telegram отправит его {way}."
    return text


def _checked(sent: object) -> types.auth.SentCode | None:
    """The SentCode to go on with, None when Telegram signed in already, SignInError when no code will come."""
    if isinstance(sent, types.auth.SentCodeSuccess):
        return None
    if isinstance(sent, types.auth.SentCodePaymentRequired):
        raise SignInError("Telegram не отправит код бесплатно: для этого номера он просит оплатить SMS. " + USE_QR)
    if isinstance(sent.type, types.auth.SentCodeTypeSetUpEmailRequired):
        raise SignInError("Telegram не отправил код: сначала он требует привязать почту для входа. " + USE_QR)
    return sent


async def request_code(client, phone: str) -> types.auth.SentCode | None:
    for attempt in range(SEND_CODE_ATTEMPTS):
        try:
            sent = await client(
                functions.auth.SendCodeRequest(phone, client.api_id, client.api_hash, types.CodeSettings())
            )
            return _checked(sent)
        except errors.AuthRestartError:
            if attempt == SEND_CODE_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")


async def password_login(client, ask_secret: Ask, say: Say) -> None:
    for _ in range(PASSWORD_ATTEMPTS):
        try:
            await client.sign_in(password=ask_secret("Облачный пароль: "))
            return
        except errors.PasswordHashInvalidError:
            say("Пароль не подошёл.")
    raise SignInError("облачный пароль не подошёл три раза подряд, запустите вход позже")


async def code_login(client, phone: str, ask: Ask, ask_secret: Ask, say: Say) -> None:
    sent = await request_code(client, phone)
    if sent is None:
        return
    say(describe(sent))
    for _ in range(CODE_ATTEMPTS):
        code = ask("Код: ").strip()
        if not code:
            if sent.next_type is None:
                say("Другого способа Telegram не предлагает. Введите код или прервите вход (Ctrl+C) и войдите по QR-коду.")
                continue
            try:
                resent = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
            except errors.RPCError as exc:
                raise SignInError(f"Telegram не отправил код повторно ({exc.__class__.__name__}). " + USE_QR) from None
            sent = _checked(resent)
            if sent is None:
                return
            say(describe(sent))
            continue
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            return
        except errors.SessionPasswordNeededError:
            await password_login(client, ask_secret, say)
            return
        except errors.PhoneCodeInvalidError:
            say("Код не подошёл, попробуйте ещё раз.")
        except errors.PhoneCodeExpiredError:
            raise SignInError("код истёк, запустите вход ещё раз") from None
    raise SignInError("код не подошёл несколько раз подряд, запустите вход позже")


async def qr_login(
    client,
    ask_secret: Ask,
    say: Say,
    show: Callable[[str], None],
    *,
    total_s: float = QR_TOTAL_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """The client must receive updates: Telethon learns about the scan from UpdateLoginToken."""
    deadline = clock() + total_s
    try:
        qr = await client.qr_login()
        while True:
            show(qr.url)
            say(QR_HINT)
            try:
                await qr.wait()
                return
            except asyncio.TimeoutError:
                if clock() >= deadline:
                    raise SignInError(
                        "QR-код так и не отсканировали. Если вход на телефоне вы подтвердили, запустите вход ещё раз"
                    ) from None
                await qr.recreate()
                if await client.is_user_authorized():  # the scan landed between the timeout and the new code
                    return
    except errors.SessionPasswordNeededError:
        await password_login(client, ask_secret, say)


def render_qr(url: str) -> str:
    """The QR code as terminal text, white on black whatever the terminal theme.

    segno draws light modules as blocks in the text colour, so on a light theme without explicit
    colours the code comes out as a negative that cameras may not read.
    """
    import segno

    buf = io.StringIO()
    segno.make(url, error="l").terminal(out=buf, compact=True)
    return "\n".join(f"\x1b[97;40m{line}\x1b[0m" for line in buf.getvalue().splitlines())


def show_qr(url: str, out: TextIO | None = None) -> None:
    import sys

    print("\n" + render_qr(url), file=out or sys.stdout, flush=True)
