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

from .i18n import t

QR_TOTAL_S = 300.0
CODE_ATTEMPTS = 5
PASSWORD_ATTEMPTS = 3
SEND_CODE_ATTEMPTS = 3

Ask = Callable[[str], str]
Say = Callable[[str], None]

# How Telegram will send the code next time -> text key.
NEXT_WAY = {
    types.auth.CodeTypeSms: "way_sms",
    types.auth.CodeTypeCall: "way_call",
    types.auth.CodeTypeFlashCall: "way_call",
    types.auth.CodeTypeMissedCall: "way_call",
    types.auth.CodeTypeFragmentSms: "way_fragment",
}


def use_qr() -> str:
    return t("use_qr")


def qr_hint() -> str:
    return t("qr_hint")


class SignInError(Exception):
    """A sign-in that cannot go on. The message is for the user."""


def where(code_type: object) -> str:
    """Where Telegram says it sent the code, in words."""
    kind = code_type
    if isinstance(kind, types.auth.SentCodeTypeApp):
        return t("sent_app")
    if isinstance(
        kind,
        (
            types.auth.SentCodeTypeSms,
            types.auth.SentCodeTypeSmsWord,
            types.auth.SentCodeTypeSmsPhrase,
            types.auth.SentCodeTypeFirebaseSms,
        ),
    ):
        return t("way_sms")
    if isinstance(kind, types.auth.SentCodeTypeCall):
        return t("way_voice")
    if isinstance(kind, (types.auth.SentCodeTypeFlashCall, types.auth.SentCodeTypeMissedCall)):
        return t("sent_flash")
    if isinstance(kind, types.auth.SentCodeTypeEmailCode):
        return t("sent_email", email=kind.email_pattern)
    if isinstance(kind, types.auth.SentCodeTypeFragmentSms):
        return t("sent_fragment", url=kind.url)
    return t("sent_unknown", name=type(kind).__name__)


def describe(sent: types.auth.SentCode) -> str:
    text = t("code_sent", where=where(sent.type))
    if sent.next_type is not None:
        way = t(NEXT_WAY.get(type(sent.next_type), "way_other"))
        after = t("code_within", seconds=sent.timeout) if sent.timeout else ""
        text += t("code_next", after=after, way=way)
    return text


def _checked(sent: object) -> types.auth.SentCode | None:
    """The SentCode to go on with, None when Telegram signed in already, SignInError when no code will come."""
    if isinstance(sent, types.auth.SentCodeSuccess):
        return None
    if isinstance(sent, types.auth.SentCodePaymentRequired):
        raise SignInError(t("paid_sms") + use_qr())
    if isinstance(sent.type, types.auth.SentCodeTypeSetUpEmailRequired):
        raise SignInError(t("email_required") + use_qr())
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
            await client.sign_in(password=ask_secret(t("password_prompt")))
            return
        except errors.PasswordHashInvalidError:
            say(t("password_wrong"))
    raise SignInError(t("password_three_times"))


async def code_login(client, phone: str, ask: Ask, ask_secret: Ask, say: Say) -> None:
    sent = await request_code(client, phone)
    if sent is None:
        return
    say(describe(sent))
    for _ in range(CODE_ATTEMPTS):
        code = ask(t("code_prompt")).strip()
        if not code:
            if sent.next_type is None:
                say(t("no_other_way"))
                continue
            try:
                resent = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
            except errors.RPCError as exc:
                raise SignInError(t("resend_failed", name=exc.__class__.__name__) + use_qr()) from None
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
            say(t("code_wrong"))
        except errors.PhoneCodeExpiredError:
            raise SignInError(t("code_expired")) from None
    raise SignInError(t("code_too_many"))


async def qr_login(
    client,
    ask_secret: Ask,
    say: Say,
    show: Callable[[str], None],
    *,
    total_s: float = QR_TOTAL_S,
    clock: Callable[[], float] = time.monotonic,
    clear: Callable[[], None] | None = None,
) -> None:
    """The client must receive updates: Telethon learns about the scan from UpdateLoginToken.

    clear takes the shown code off the screen: it runs before each new code, before the password
    question and on the way out, whatever the way. Without it the codes stay where they were printed.
    """
    clear = clear or (lambda: None)
    deadline = clock() + total_s
    try:
        qr = await client.qr_login()
        while True:
            clear()
            show(qr.url)
            say(qr_hint())
            try:
                await qr.wait()
                return
            except asyncio.TimeoutError:
                if clock() >= deadline:
                    raise SignInError(t("qr_not_scanned")) from None
                await qr.recreate()
                if await client.is_user_authorized():  # the scan landed between the timeout and the new code
                    return
    except errors.SessionPasswordNeededError:
        clear()
        await password_login(client, ask_secret, say)
    finally:
        clear()


def render_qr(url: str, color: bool = True) -> str:
    """The QR code as terminal text, white on black whatever the terminal theme.

    segno draws light modules as blocks in the text colour, so on a light theme without explicit
    colours the code comes out as a negative that cameras may not read. Without colour (output
    that is not a terminal) the lines carry no escape codes at all.
    """
    import segno

    buf = io.StringIO()
    segno.make(url, error="l").terminal(out=buf, compact=True)
    lines = buf.getvalue().splitlines()
    return "\n".join(f"\x1b[97;40m{line}\x1b[0m" if color else line for line in lines)


def show_qr(url: str, out: TextIO | None = None) -> None:
    import sys

    print("\n" + render_qr(url), file=out or sys.stdout, flush=True)
