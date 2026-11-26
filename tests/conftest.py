from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Sleeper:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()
