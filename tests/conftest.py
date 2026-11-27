from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def russian(monkeypatch, tmp_path_factory):
    """Every test starts in Russian, as the program spoke before English existed.

    The system language counts as Russian too, so cli.main() does not switch to English on a CI
    machine; the folder of the program is a fresh one, so a settings.json of this computer is never read.
    """
    from tgsweep import i18n

    before = i18n.get_lang()
    i18n.set_lang("ru")
    monkeypatch.delenv("TGSWEEP_LANG", raising=False)
    monkeypatch.setattr(i18n, "system_lang", lambda: "ru")
    monkeypatch.setenv("TGSWEEP_HOME", str(tmp_path_factory.mktemp("tgsweep-home")))
    yield
    i18n.set_lang(before)


class Sleeper:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()
