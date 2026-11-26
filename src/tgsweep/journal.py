"""Append-only action log. Message texts are never written here."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .files import FILE_MODE

ALLOWED_FIELDS = {"chat_id", "msg_id", "date", "action", "result", "error", "run_id"}


class Journal:
    def __init__(self, path: Path | None):
        self.path = path
        self.records: list[dict[str, Any]] = []

    def write(self, **fields: Any) -> None:
        unknown = set(fields) - ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"journal fields not allowed: {sorted(unknown)}")
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields}
        if self.path is None:
            self.records.append(record)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
