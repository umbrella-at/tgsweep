"""Where tgsweep keeps its files on this computer, and the Telegram API credentials."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from . import i18n
from .errors import ConfigError
from .files import DIR_MODE, secure_file, write_private
from .i18n import t

APP = "tgsweep"
HOME_ENV = "TGSWEEP_HOME"
API_ID_ENV = "TGSWEEP_API_ID"
API_HASH_ENV = "TGSWEEP_API_HASH"
API_HASH_RE = re.compile(r"^[0-9a-f]{32}$")


def default_root(env: Mapping[str, str] = os.environ) -> Path:
    override = env.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    import platformdirs

    return Path(platformdirs.user_data_dir(APP, appauthor=False))


def shown_path(path: str | os.PathLike[str], *, home: str | os.PathLike[str] | None = None,
               windows: bool = os.name == "nt") -> str:
    """A path as the program prints it. On macOS and Linux a path inside the user's home folder starts
    with ~: shorter, and a screenshot of the terminal shows no user name. Paths outside the home folder
    (TGSWEEP_HOME set elsewhere) and every path on Windows are printed whole."""
    text = os.fspath(path)
    if windows:
        return text
    try:
        base = PurePosixPath(Path.home() if home is None else home)
    except (RuntimeError, KeyError):  # no home folder known at all
        return text
    if not base.is_absolute() or base == PurePosixPath("/"):  # HOME=/ would turn every path into ~
        return text
    try:
        rest = PurePosixPath(text).relative_to(base)  # by path parts: /Users/maria2 is not inside /Users/maria
    except ValueError:
        return text
    return "~" if rest == PurePosixPath() else f"~/{rest.as_posix()}"


@dataclass(frozen=True)
class Home:
    root: Path

    @property
    def session_base(self) -> Path:
        """Telethon adds .session itself."""
        return self.root / APP

    @property
    def session_file(self) -> Path:
        return self.root / f"{APP}.session"

    @property
    def session_files(self) -> list[Path]:
        return [self.session_file, self.root / f"{APP}.session-journal"]

    @property
    def credentials_file(self) -> Path:
        return self.root / "credentials.json"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def journal_file(self) -> Path:
        return self.root / "logs" / "actions.jsonl"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        os.chmod(self.root, DIR_MODE)  # a no-op on Windows, where the user profile already keeps it private

    def secure_session(self) -> None:
        for path in self.session_files:
            secure_file(path)

    def forget_session(self) -> None:
        for path in self.session_files:
            path.unlink(missing_ok=True)

    # --- reaction scan positions, so the next run does not scan the same messages again ---

    def load_state(self) -> dict[str, int]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}  # a broken file only costs a longer scan
        scan = data.get("reaction_scan", {}) if isinstance(data, dict) else {}
        return {str(k): int(v) for k, v in scan.items() if isinstance(v, int)}

    def save_state(self, reaction_scan: dict[str, int]) -> None:
        write_private(self.state_file, json.dumps({"reaction_scan": reaction_scan}, indent=2))

    # --- the language chosen with --lang ---

    def saved_lang(self) -> str | None:
        try:
            data = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # no file or a broken one: no choice, the system decides
        lang = data.get("lang") if isinstance(data, dict) else None
        return lang if lang in i18n.LANGS else None

    def save_lang(self, lang: str) -> None:
        self.ensure()
        write_private(self.settings_file, json.dumps({"lang": lang}, indent=2))

    def forget_lang(self) -> None:
        self.settings_file.unlink(missing_ok=True)


def parse_credentials(api_id: str, api_hash: str) -> tuple[int, str]:
    api_id, api_hash = api_id.strip(), api_hash.strip().lower()
    if not api_id.isdigit() or int(api_id) <= 0:
        raise ConfigError(t("api_id_format"))
    if not API_HASH_RE.match(api_hash):
        raise ConfigError(t("api_hash_format"))
    return int(api_id), api_hash


def load_credentials(home: Home, env: Mapping[str, str] = os.environ) -> tuple[int, str] | None:
    """Environment first, then the saved file. None when neither has them."""
    if env.get(API_ID_ENV) or env.get(API_HASH_ENV):
        if not (env.get(API_ID_ENV) and env.get(API_HASH_ENV)):
            raise ConfigError(t("env_both", a=API_ID_ENV, b=API_HASH_ENV))
        return parse_credentials(env[API_ID_ENV], env[API_HASH_ENV])
    try:
        data = json.loads(home.credentials_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ConfigError(t("file_damaged", path=shown_path(home.credentials_file))) from None
    if not isinstance(data, dict):
        raise ConfigError(t("file_damaged", path=shown_path(home.credentials_file)))
    return parse_credentials(str(data.get("api_id", "")), str(data.get("api_hash", "")))


def save_credentials(home: Home, api_id: int, api_hash: str) -> None:
    write_private(home.credentials_file, json.dumps({"api_id": api_id, "api_hash": api_hash}, indent=2))
