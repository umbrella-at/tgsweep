"""Every text the user sees, in Russian and English, and how the language is chosen.

Texts are looked up when they are shown, never at import: the language is set by the command
after the modules are loaded. Each key keeps both versions side by side, so an edit shows both.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Mapping

LANGS = ("ru", "en")
LANG_CHOICES = ("ru", "en", "auto")
LANG_ENV = "TGSWEEP_LANG"

# Before the command chooses, library callers keep the texts they had before English existed.
_lang = "ru"

TEXTS: dict[str, dict[str, str]] = {
    # --- app: how old ---
    "retention_all": {"ru": "Все, даже сегодняшние", "en": "All, including today's"},
    "retention_1": {"ru": "Старше 1 дня", "en": "Older than 1 day"},
    "retention_2": {"ru": "Старше 2 дней", "en": "Older than 2 days"},
    "retention_7": {"ru": "Старше 7 дней", "en": "Older than 7 days"},
    "retention_30": {"ru": "Старше 30 дней", "en": "Older than 30 days"},
    # --- app: what is left alone ---
    "skipped_header": {"ru": "Не трогаю:", "en": "Left alone:"},
    "skip_dm": {"ru": "личные чаты и боты (их не открываю)", "en": "private chats and bots (never opened)"},
    "skip_creator": {
        "ru": "чаты, которые вы создали (пропускаю, чтобы не опустошить вашу же группу)",
        "en": "chats you created (skipped so your own group is not emptied)",
    },
    "skip_broadcast": {
        "ru": "каналы (свои посты удаляйте в самом Telegram, комментарии к постам ищу отдельно)",
        "en": "channels (delete your own posts in Telegram itself, comments on posts are searched separately)",
    },
    "skip_unsupported": {"ru": "чаты неизвестного типа", "en": "chats of an unknown type"},
    "nothing": {"ru": "ничего", "en": "nothing"},
    # --- app and cleanup: titles and time ---
    "title_comments_on_posts": {"ru": "{title} (комментарии к постам)", "en": "{title} (comments on posts)"},
    "title_comments": {"ru": "{title} (комментарии)", "en": "{title} (comments)"},
    "seconds": {"ru": "{seconds} с", "en": "{seconds} s"},
    "minutes": {"ru": "{minutes} мин", "en": "{minutes} min"},
    "hours_minutes": {"ru": "{hours} ч {rest} мин", "en": "{hours} h {rest} min"},
    "hours": {"ru": "{hours} ч", "en": "{hours} h"},
    # --- app: the status line ---
    "scan_dialogs": {"ru": "Получаю список чатов: {read}", "en": "Getting the chat list: {read}"},
    "waiting": {"ru": "Telegram попросил подождать {duration}, жду", "en": "Telegram asked to wait {duration}, waiting"},
    "scan_reactions": {"ru": "ищу ваши реакции, просмотрено {read}", "en": "looking for your reactions, {read} checked"},
    "scan_messages": {
        "ru": "ищу ваши сообщения, найдено всего {found}",
        "en": "looking for your messages, {found} found so far",
    },
    "scan_chat": {"ru": "Чат {chat} из {chats} · {doing} · {title}", "en": "Chat {chat} of {chats} · {doing} · {title}"},
    "deleting": {"ru": "Удаляю: {done} из {total} ({percent}%)", "en": "Deleting: {done} of {total} ({percent}%)"},
    "stop_note": {
        "ru": "Останавливаюсь после текущего запроса. Нажмите Ctrl+C ещё раз, чтобы прервать сразу.",
        "en": "Stopping after the current request. Press Ctrl+C again to stop right away.",
    },
    # --- app: the run ---
    "not_personal": {
        "ru": "Это не вход в ваш аккаунт Telegram. Выйдите (tgsweep --logout) и войдите заново.",
        "en": "This session is not your personal Telegram account. Sign out (tgsweep --logout) and sign in again.",
    },
    "account": {"ru": "Аккаунт: {name}", "en": "Account: {name}"},
    "which_messages": {"ru": "Какие ваши сообщения удалить?", "en": "Which of your messages to delete?"},
    "nothing_deleted": {"ru": "Ничего не удалено.", "en": "Nothing was deleted."},
    "searching": {
        "ru": "Ищу ваши сообщения в группах и комментарии к постам. В больших чатах это может занять несколько минут.",
        "en": "Looking for your messages in groups and your comments on posts. "
        "In large chats this can take a few minutes.",
    },
    "search_took": {"ru": "Поиск занял {duration}, просмотрено {chats}.", "en": "Search took {duration}, {chats} checked."},
    "nothing_found": {
        "ru": "Удалять нечего: ваших сообщений в группах нет.",
        "en": "Nothing to delete: you have no messages in groups.",
    },
    "nothing_older": {
        "ru": "Удалять нечего: ваших сообщений старше {days} нет. "
        "Чтобы удалить и свежие, выберите «Все, даже сегодняшние».",
        "en": "Nothing to delete: you have no messages older than {days}. "
        'To delete recent ones too, choose "All, including today\'s".',
    },
    "pick_chats": {"ru": "Отметьте чаты, где удалить ваши сообщения:", "en": "Select the chats to delete your messages from:"},
    "none_picked": {"ru": "Ни один чат не отмечен, ничего не удалено.", "en": "No chats selected, nothing was deleted."},
    "will_delete": {
        "ru": "Будет удалено: {counts} в {chats}. Вернуть их будет нельзя. Это займёт примерно {minutes}.",
        "en": "To be deleted: {counts} in {chats}. This cannot be undone. It will take about {minutes}.",
    },
    "confirm_number": {"ru": "Чтобы подтвердить, введите число {total}:", "en": "To confirm, type the number {total}:"},
    "number_mismatch": {"ru": "Число не совпало, ничего не удалено.", "en": "The number does not match, nothing was deleted."},
    "report_done": {
        "ru": "Готово. Удалено сообщений: {messages}, комментариев: {comments}, снято реакций: {reactions}.",
        "en": "Done. Messages deleted: {messages}, comments deleted: {comments}, reactions removed: {reactions}.",
    },
    "report_missing": {"ru": "Уже удалены раньше: {missing}.", "en": "Already deleted earlier: {missing}."},
    "report_refused": {
        "ru": "Не тронуты, потому что оказались не вашими: {refused}.",
        "en": "Left alone because they turned out not to be yours: {refused}.",
    },
    "report_failed": {
        "ru": "Не удалось удалить: {failed}. Запустите tgsweep ещё раз позже.",
        "en": "Could not delete: {failed}. Run tgsweep again later.",
    },
    "report_stopped": {
        "ru": "Остановлено до конца. Оставшееся найдётся при следующем запуске.",
        "en": "Stopped before the end. The rest will be found on the next run.",
    },
    "report_journal": {"ru": "Журнал без текстов сообщений: {path}", "en": "Log without message texts: {path}"},
    # --- cli: the keys ---
    "creds_intro": {
        "ru": "Для работы нужны api_id и api_hash. Их выдаёт сам Telegram, это бесплатно:",
        "en": "tgsweep needs an api_id and api_hash. Telegram issues them for free:",
    },
    "creds_step1": {
        "ru": "  1. Откройте https://my.telegram.org и войдите по номеру телефона.",
        "en": "  1. Open https://my.telegram.org and sign in with your phone number.",
    },
    "creds_step2": {
        "ru": "  2. Выберите API development tools и создайте приложение, название любое.",
        "en": "  2. Choose API development tools and create an application, any name will do.",
    },
    "creds_step3": {
        "ru": "  3. Скопируйте сюда App api_id и App api_hash.",
        "en": "  3. Copy App api_id and App api_hash here.",
    },
    "creds_saved": {"ru": "Они сохранятся только на этом компьютере.", "en": "They are saved only on this computer."},
    # --- cli: the rest ---
    "login_help": {
        "ru": "Вход в Telegram. tgsweep работает только с группами и каналами, личные чаты он не открывает.",
        "en": "Signing in to Telegram. tgsweep works only with groups and channels and never opens private chats.",
    },
    "api_hash_prompt": {"ru": "api_hash (при вводе не виден): ", "en": "api_hash (hidden while typing): "},
    "creds_rejected": {"ru": "Не подошло: {error}.", "en": "That did not work: {error}."},
    "creds_three_times": {
        "ru": "api_id и api_hash не подошли три раза подряд, запустите tgsweep ещё раз",
        "en": "api_id and api_hash were rejected three times in a row, run tgsweep again",
    },
    "signed_out": {"ru": "Вы вышли из аккаунта на этом компьютере.", "en": "You are signed out on this computer."},
    "sign_out_unconfirmed": {
        "ru": "Telegram не подтвердил выход. Файл входа на этом компьютере удалён, а сеанс tgsweep "
        "завершите в Telegram: Настройки, Устройства.",
        "en": "Telegram did not confirm the sign-out. The session file on this computer is deleted, "
        "so terminate the tgsweep session in Telegram yourself: Settings > Devices.",
    },
    "no_saved_login": {"ru": "На этом компьютере вход не сохранён.", "en": "There is no saved sign-in on this computer."},
    "phone_prompt": {"ru": "Телефон в международном формате: ", "en": "Phone number in international format: "},
    "keys_refused": {
        "ru": "Telegram не принял api_id и api_hash. Проверьте их на my.telegram.org и запустите tgsweep ещё раз: "
        "он спросит их заново",
        "en": "Telegram rejected the api_id and api_hash. Check them on my.telegram.org and run tgsweep again: "
        "it will ask for them again",
    },
    "login_incomplete": {
        "ru": "вход не завершён, запустите tgsweep ещё раз",
        "en": "sign-in was not completed, run tgsweep again",
    },
    "login_expired": {
        "ru": "Вход на этом компьютере больше не действует. Запустите tgsweep ещё раз и войдите заново.",
        "en": "The sign-in on this computer is no longer valid. Run tgsweep again and sign in.",
    },
    "ask_sign_out": {
        "ru": "Выйти из аккаунта на этом компьютере? Тогда в следующий раз снова понадобится QR-код.",
        "en": "Sign out on this computer? Next time you will need the QR code again.",
    },
    "login_kept": {
        "ru": "Вход сохранён в {root}. Выйти можно командой: tgsweep --logout",
        "en": "Sign-in kept in {root}. To sign out later: tgsweep --logout",
    },
    "help_description": {
        "ru": "Удаляет ваши сообщения, комментарии и реакции в группах Telegram, которые вы отметите. "
        "Личные чаты не открывает.",
        "en": "Deletes your messages, comments and reactions in the Telegram groups you select. "
        "Never opens private chats.",
    },
    "help_code": {
        "ru": "войти по коду из Telegram вместо QR-кода",
        "en": "sign in with a code from Telegram instead of a QR code",
    },
    "help_logout": {
        "ru": "выйти из аккаунта на этом компьютере и удалить файл входа",
        "en": "sign out on this computer and delete the session file",
    },
    "help_lang": {
        "ru": "язык программы: ru, en или auto (по языку системы). Выбор запоминается",
        "en": "program language: ru, en or auto (system language). The choice is remembered",
    },
    "needs_terminal": {
        "ru": "tgsweep работает только в интерактивном терминале.",
        "en": "tgsweep only works in an interactive terminal.",
    },
    "interrupted": {"ru": "\nПрервано.", "en": "\nInterrupted."},
    "error": {"ru": "Ошибка: {error}", "en": "Error: {error}"},
    "network_error": {
        "ru": "Ошибка связи с Telegram ({name}). Проверьте интернет и запустите ещё раз.",
        "en": "Could not reach Telegram ({name}). Check your internet connection and try again.",
    },
    "file_error": {"ru": "Ошибка доступа к файлу {filename}: {reason}", "en": "Cannot access file {filename}: {reason}"},
    "lang_saved": {
        "ru": "Язык сохранён: русский. Вернуть выбор по языку системы: tgsweep --lang auto",
        "en": "Language saved: English. To follow the system language again: tgsweep --lang auto",
    },
    "lang_auto": {"ru": "Язык снова выбирается по системе.", "en": "The language follows the system again."},
    "lang_env_bad": {
        "ru": "TGSWEEP_LANG может быть ru, en или auto, а сейчас: {value}",
        "en": "TGSWEEP_LANG must be ru, en or auto, not {value}",
    },
    # --- signin ---
    "use_qr": {
        "ru": "Войдите по QR-коду: та же команда без --code.",
        "en": "Sign in with a QR code instead: the same command without --code.",
    },
    "qr_hint": {
        "ru": "Отсканируйте код телефоном: Telegram, Настройки, Устройства, «Подключить устройство». "
        "Код меняется примерно раз в 30 секунд, новый появится здесь сам.",
        "en": "Scan the code with your phone: Telegram > Settings > Devices > Link Desktop Device. "
        "The code changes about every 30 seconds, a new one appears here.",
    },
    "way_sms": {"ru": "по SMS", "en": "by SMS"},
    "way_call": {"ru": "звонком", "en": "by a call"},
    "way_voice": {"ru": "голосовым звонком", "en": "by a voice call"},
    "way_fragment": {"ru": "через Fragment", "en": "via Fragment"},
    "way_other": {"ru": "другим способом", "en": "another way"},
    "sent_app": {
        "ru": "в приложение Telegram на другом вашем устройстве: это сообщение в чате «Telegram» с синей галочкой, не SMS",
        "en": 'to the Telegram app on your other device: look for a message in the "Telegram" chat '
        "with a blue check mark, not an SMS",
    },
    "sent_flash": {
        "ru": "звонком: код это последние цифры номера, с которого позвонят",
        "en": "by a call: the code is the last digits of the number that calls you",
    },
    "sent_email": {"ru": "на почту для входа {email}", "en": "to your login email {email}"},
    "sent_fragment": {"ru": "через Fragment: {url}", "en": "via Fragment: {url}"},
    "sent_unknown": {"ru": "способом {name}", "en": "by {name}"},
    "code_sent": {"ru": "Код отправлен {where}.", "en": "The code was sent {where}."},
    "code_next": {
        "ru": " Если он не придёт{after}, нажмите Enter без кода: Telegram отправит его {way}.",
        "en": " If it does not arrive{after}, press Enter without a code and Telegram will send it {way}.",
    },
    "code_within": {"ru": " за {seconds} с", "en": " within {seconds} s"},
    "paid_sms": {
        "ru": "Telegram не отправит код бесплатно: для этого номера он просит оплатить SMS. ",
        "en": "Telegram will not send the code for free: for this number it asks you to pay for the SMS. ",
    },
    "email_required": {
        "ru": "Telegram не отправил код: сначала он требует привязать почту для входа. ",
        "en": "Telegram did not send the code: it first requires you to set up a login email. ",
    },
    "password_prompt": {"ru": "Облачный пароль: ", "en": "Two-Step Verification password: "},
    "password_wrong": {"ru": "Пароль не подошёл.", "en": "Wrong password."},
    "password_three_times": {
        "ru": "облачный пароль не подошёл три раза подряд, запустите вход позже",
        "en": "the Two-Step Verification password was wrong three times in a row, try signing in later",
    },
    "code_prompt": {"ru": "Код: ", "en": "Code: "},
    "no_other_way": {
        "ru": "Другого способа Telegram не предлагает. Введите код или прервите вход (Ctrl+C) и войдите по QR-коду.",
        "en": "Telegram offers no other way. Enter the code, or stop with Ctrl+C and sign in with a QR code.",
    },
    "resend_failed": {
        "ru": "Telegram не отправил код повторно ({name}). ",
        "en": "Telegram did not resend the code ({name}). ",
    },
    "code_wrong": {"ru": "Код не подошёл, попробуйте ещё раз.", "en": "Wrong code, try again."},
    "code_expired": {"ru": "код истёк, запустите вход ещё раз", "en": "the code has expired, start signing in again"},
    "code_too_many": {
        "ru": "код не подошёл несколько раз подряд, запустите вход позже",
        "en": "the code was wrong several times in a row, try signing in later",
    },
    "qr_not_scanned": {
        "ru": "QR-код так и не отсканировали. Если вход на телефоне вы подтвердили, запустите вход ещё раз",
        "en": "The QR code was not scanned. If you did confirm the sign-in on your phone, start signing in again",
    },
    "signed_in": {"ru": "Вход выполнен.", "en": "Signed in."},
    # --- home ---
    "api_id_format": {
        "ru": "api_id это число, например 1234567, возьмите его на my.telegram.org",
        "en": "api_id is a number, for example 1234567, you can find it on my.telegram.org",
    },
    "api_hash_format": {
        "ru": "api_hash это 32 знака из цифр и латинских букв a-f, возьмите его на my.telegram.org",
        "en": "api_hash is 32 characters, digits and letters a-f, you can find it on my.telegram.org",
    },
    "env_both": {
        "ru": "задайте обе переменные, {a} и {b}, или ни одной",
        "en": "set both variables, {a} and {b}, or neither",
    },
    "file_damaged": {
        "ru": "файл {path} повреждён: удалите его и введите данные заново",
        "en": "file {path} is damaged: delete it and enter the details again",
    },
    # --- ui ---
    "choose_hint": {"ru": "(стрелки и Enter)", "en": "(arrows and Enter)"},
    "pick_hint": {
        "ru": "(стрелки двигают, пробел отмечает, a отмечает все, Enter продолжает)",
        "en": "(arrows move, space selects, a selects all, Enter continues)",
    },
    # What stands after an answered question, in place of questionary's English.
    "answer_yes": {"ru": "Да", "en": "Yes"},
    "answer_no": {"ru": "Нет", "en": "No"},
    "picked_nothing": {"ru": "ничего", "en": "nothing"},
    "picked_many": {"ru": "отмечено: {count}", "en": "{count} selected"},
}

# Word forms after a number: ru one, few, many (1 сообщение, 2 сообщения, 5 сообщений); en one, other.
PLURALS: dict[str, dict[str, tuple[str, ...]]] = {
    "message": {"ru": ("сообщение", "сообщения", "сообщений"), "en": ("message", "messages")},
    "comment": {"ru": ("комментарий", "комментария", "комментариев"), "en": ("comment", "comments")},
    "reaction": {"ru": ("реакция", "реакции", "реакций"), "en": ("reaction", "reactions")},
    "chat": {"ru": ("чат", "чата", "чатов"), "en": ("chat", "chats")},  # after «просмотрено»
    "chat_in": {"ru": ("чате", "чатах", "чатах"), "en": ("chat", "chats")},  # after «в»
    "minute": {"ru": ("минуту", "минуты", "минут"), "en": ("minute", "minutes")},
    "day_older": {"ru": ("дня", "дней", "дней"), "en": ("day", "days")},  # after «старше»
}


def set_lang(lang: str) -> None:
    global _lang
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}")
    _lang = lang


def get_lang() -> str:
    return _lang


def t(key: str, **kwargs: object) -> str:
    return TEXTS[key][_lang].format(**kwargs)


def plural(n: int, key: str) -> str:
    forms = PLURALS[key][_lang]
    if _lang == "en":
        word = forms[0] if n == 1 else forms[1]
    elif n % 10 == 1 and n % 100 != 11:
        word = forms[0]
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = forms[1]
    else:
        word = forms[2]
    return f"{n} {word}"


# --- which language -------------------------------------------------------------


def _lang_of(code: str) -> str | None:
    """ru for a Russian language code (ru, ru_RU, ru-RU, ru_UA), en for any other, None for none at all."""
    code = code.strip()
    if not code or code.split(".")[0].upper() in ("C", "POSIX"):
        return None
    return "ru" if code.lower().startswith("ru") else "en"


def detect_lang(
    env: Mapping[str, str],
    platform: str,
    mac_languages: Callable[[], list[str]],
    windows_langid: Callable[[], int],
) -> str:
    """The system language: the environment in gettext's order, then macOS or Windows settings, else en."""
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(name, "")
        if name == "LANGUAGE":
            value = value.split(":")[0]
        found = _lang_of(value)
        if found:
            return found
    if platform == "darwin":
        try:
            languages = mac_languages()
        except Exception:  # no defaults, a timeout, a strange answer: not known
            return "en"
        return (_lang_of(languages[0]) if languages else None) or "en"
    if platform == "win32":
        try:
            langid = windows_langid()
        except Exception:
            return "en"
        return "ru" if langid & 0x3FF == 0x19 else "en"
    return "en"


def _mac_languages() -> list[str]:
    """The languages of macOS in order of preference: `defaults` prints them as ( "ru-RU", "en-US" )."""
    out = subprocess.run(
        ["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=2, check=True
    ).stdout
    return [part.strip().strip('"') for part in out.strip().strip("()").split(",") if part.strip()]


def _windows_langid() -> int:
    import ctypes

    return ctypes.windll.kernel32.GetUserDefaultUILanguage()


def system_lang() -> str:
    return detect_lang(os.environ, sys.platform, _mac_languages, _windows_langid)


def pick_lang(flag: str | None, env_value: str | None, saved: str | None, system: str) -> str:
    """--lang of this run, then TGSWEEP_LANG, then the saved choice, then the system.

    auto in the flag skips the saved choice (it is being removed); auto or nothing in the variable
    means no variable. Any other value of the variable is a ValueError.
    """
    if env_value in (None, "", "auto"):
        env_value = None
    elif env_value not in LANGS:
        raise ValueError(env_value)
    if flag in LANGS:
        return flag
    if env_value:
        return env_value
    if flag != "auto" and saved in LANGS:
        return saved
    return system
