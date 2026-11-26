"""Private files: owner-only permissions and atomic writes."""

from __future__ import annotations

import os
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


def secure_file(path: Path) -> None:
    """Restrict a file to its owner if it exists."""
    if path.exists():
        os.chmod(path, FILE_MODE)


def write_private(path: Path, text: str) -> None:
    """Write a file atomically with 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, FILE_MODE)
    os.replace(tmp, path)
