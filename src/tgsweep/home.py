"""Where tgsweep keeps its files on this computer, and the Telegram API credentials."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import ConfigError
from .files import DIR_MODE, secure_file, write_private

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


def parse_credentials(api_id: str, api_hash: str) -> tuple[int, str]:
    api_id, api_hash = api_id.strip(), api_hash.strip().lower()
    if not api_id.isdigit() or int(api_id) <= 0:
        raise ConfigError("api_id это число, например 1234567, возьмите его на my.telegram.org")
    if not API_HASH_RE.match(api_hash):
        raise ConfigError("api_hash это 32 знака из цифр и латинских букв a-f, возьмите его на my.telegram.org")
    return int(api_id), api_hash


def load_credentials(home: Home, env: Mapping[str, str] = os.environ) -> tuple[int, str] | None:
    """Environment first, then the saved file. None when neither has them."""
    if env.get(API_ID_ENV) or env.get(API_HASH_ENV):
        if not (env.get(API_ID_ENV) and env.get(API_HASH_ENV)):
            raise ConfigError(f"задайте обе переменные, {API_ID_ENV} и {API_HASH_ENV}, или ни одной")
        return parse_credentials(env[API_ID_ENV], env[API_HASH_ENV])
    try:
        data = json.loads(home.credentials_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ConfigError(f"файл {home.credentials_file} повреждён: удалите его и введите данные заново") from None
    if not isinstance(data, dict):
        raise ConfigError(f"файл {home.credentials_file} повреждён: удалите его и введите данные заново")
    return parse_credentials(str(data.get("api_id", "")), str(data.get("api_hash", "")))


def save_credentials(home: Home, api_id: int, api_hash: str) -> None:
    write_private(home.credentials_file, json.dumps({"api_id": api_id, "api_hash": api_hash}, indent=2))
