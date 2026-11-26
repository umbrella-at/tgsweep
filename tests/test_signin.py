"""Sign-in: the user learns where the code went, and QR sign-in works without any code."""

from __future__ import annotations

import asyncio

import pytest
from telethon import errors, functions, types

from tgsweep import signin


def sent_code(code_type, hash_="h1", next_type=None, timeout=None):
    return types.auth.SentCode(type=code_type, phone_code_hash=hash_, next_type=next_type, timeout=timeout)


class SignInFake:
    api_id = 2040
    api_hash = "0123456789abcdef0123456789abcdef"

    def __init__(self, answers=(), sign_in_results=(), qr=None):
        self.answers = list(answers)
        self.requests = []
        self.sign_ins = []
        self.sign_in_results = list(sign_in_results)
        self.qr = qr
        self.authorized = False

    async def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        self.sign_ins.append({"phone": phone, "code": code, "password": password, "hash": phone_code_hash})
        result = self.sign_in_results.pop(0) if self.sign_in_results else None
        if isinstance(result, BaseException):
            raise result

    async def qr_login(self):
        return self.qr

    async def is_user_authorized(self):
        return self.authorized


class FakeQR:
    def __init__(self, waits, client=None, authorize_on_recreate=False):
        self.waits = list(waits)
        self.recreated = 0
        self.url = "tg://login?token=t0"
        self.client = client
        self.authorize_on_recreate = authorize_on_recreate

    async def wait(self, timeout=None):
        outcome = self.waits.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome

    async def recreate(self):
        self.recreated += 1
        self.url = f"tg://login?token=t{self.recreated}"
        if self.authorize_on_recreate:
            self.client.authorized = True


def answers(*replies):
    it = iter(replies)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return next(it)

    return ask, asked


def run(coro):
    return asyncio.run(coro)


# --- by code ---------------------------------------------------------------


def test_code_in_the_app_is_named_and_the_sign_in_uses_its_hash():
    client = SignInFake(answers=[sent_code(types.auth.SentCodeTypeApp(length=5), next_type=types.auth.CodeTypeSms(), timeout=60)])
    said = []
    ask, _ = answers("12345")
    run(signin.code_login(client, "+10000000000", ask=ask, ask_secret=lambda p: "", say=said.append))
    assert "чате «Telegram»" in said[0] and "не SMS" in said[0]
    assert "за 60 с" in said[0] and "по SMS" in said[0]
    assert isinstance(client.requests[0], functions.auth.SendCodeRequest)
    assert client.sign_ins == [{"phone": "+10000000000", "code": "12345", "password": None, "hash": "h1"}]


def test_empty_input_asks_telegram_for_the_next_way():
    client = SignInFake(
        answers=[
            sent_code(types.auth.SentCodeTypeApp(length=5), next_type=types.auth.CodeTypeSms()),
            sent_code(types.auth.SentCodeTypeSms(length=5), hash_="h2"),
        ]
    )
    said = []
    ask, _ = answers("", "54321")
    run(signin.code_login(client, "+10000000000", ask=ask, ask_secret=lambda p: "", say=said.append))
    resend = client.requests[1]
    assert isinstance(resend, functions.auth.ResendCodeRequest)
    assert resend.phone_code_hash == "h1"
    assert said[1] == "Код отправлен по SMS."
    assert client.sign_ins[0]["hash"] == "h2"


def test_email_setup_required_stops_without_asking_for_a_code():
    client = SignInFake(answers=[sent_code(types.auth.SentCodeTypeSetUpEmailRequired())])
    ask, asked = answers()
    with pytest.raises(signin.SignInError, match="почту для входа.*QR"):
        run(signin.code_login(client, "+10000000000", ask=ask, ask_secret=lambda p: "", say=print))
    assert asked == [] and client.sign_ins == []


def test_paid_sms_stops_without_asking_for_a_code():
    paid = types.auth.SentCodePaymentRequired(
        store_product="p", phone_code_hash="h", support_email_address="a@b", support_email_subject="s",
        premium_days=7, currency="USD", amount=100,
    )
    client = SignInFake(answers=[paid])
    ask, asked = answers()
    with pytest.raises(signin.SignInError, match="оплатить SMS"):
        run(signin.code_login(client, "+10000000000", ask=ask, ask_secret=lambda p: "", say=print))
    assert asked == []


def test_wrong_code_then_cloud_password():
    client = SignInFake(
        answers=[sent_code(types.auth.SentCodeTypeApp(length=5))],
        sign_in_results=[
            errors.PhoneCodeInvalidError(request=None),
            errors.SessionPasswordNeededError(request=None),
            errors.PasswordHashInvalidError(request=None),
            None,
        ],
    )
    said = []
    ask, _ = answers("11111", "12345")
    secret, _ = answers("wrong", "right")
    run(signin.code_login(client, "+10000000000", ask=ask, ask_secret=secret, say=said.append))
    assert "Код не подошёл, попробуйте ещё раз." in said and "Пароль не подошёл." in said
    assert [s["password"] for s in client.sign_ins] == [None, None, "wrong", "right"]


# --- by QR -----------------------------------------------------------------


def test_qr_is_shown_again_after_it_expires():
    client = SignInFake()
    client.qr = FakeQR([asyncio.TimeoutError(), None])
    shown = []
    run(signin.qr_login(client, ask_secret=lambda p: "", say=lambda s: None, show=shown.append))
    assert shown == ["tg://login?token=t0", "tg://login?token=t1"]
    assert client.qr.recreated == 1 and client.sign_ins == []


def test_qr_asks_for_the_cloud_password_after_the_scan():
    client = SignInFake()
    client.qr = FakeQR([errors.SessionPasswordNeededError(request=None)])
    secret, asked = answers("pw")
    run(signin.qr_login(client, ask_secret=secret, say=lambda s: None, show=lambda u: None))
    assert asked == ["Облачный пароль: "]
    assert client.sign_ins == [{"phone": None, "code": None, "password": "pw", "hash": None}]


def test_qr_scan_between_timeout_and_new_code_counts():
    client = SignInFake()
    client.qr = FakeQR([asyncio.TimeoutError()], client=client, authorize_on_recreate=True)
    shown = []
    run(signin.qr_login(client, ask_secret=lambda p: "", say=lambda s: None, show=shown.append))
    assert shown == ["tg://login?token=t0"]


def test_qr_gives_up_after_the_total_time():
    client = SignInFake()
    client.qr = FakeQR([asyncio.TimeoutError()] * 3)
    ticks = iter([0.0, 100.0, 400.0])
    with pytest.raises(signin.SignInError, match="не отсканировали"):
        run(signin.qr_login(client, ask_secret=lambda p: "", say=lambda s: None, show=lambda u: None,
                            total_s=300.0, clock=lambda: next(ticks)))
    assert client.qr.recreated == 1


def test_qr_is_white_on_black_whatever_the_terminal_theme():
    lines = signin.render_qr("tg://login?token=abc").splitlines()
    assert len(lines) > 10
    assert all(line.startswith("\x1b[97;40m") and line.endswith("\x1b[0m") for line in lines)
    # The quiet zone is light: with white text on black it must be drawn as full blocks.
    assert set(lines[0][len("\x1b[97;40m"):-len("\x1b[0m")]) == {"█"}
