"""The terminal UI: arrow keys and checkboxes, on macOS, Linux and Windows alike.

The questions run on the same asyncio loop as the Telegram client (questionary's async API):
the synchronous one would start a second loop and fail inside the running one.
"""

from __future__ import annotations

import getpass
from typing import Any

import questionary

CHOOSE_HINT = "(стрелки и Enter)"
PICK_HINT = "(стрелки двигают, пробел отмечает, a отмечает все, Enter продолжает)"


class TerminalUI:
    def __init__(self, **io: Any) -> None:
        """io: prompt_toolkit input= and output=, for tests."""
        self.io = io

    def say(self, text: str) -> None:
        print(text, flush=True)

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def ask_secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    async def _answer(self, question: questionary.Question) -> Any:
        """None when the user presses Ctrl+C or Ctrl+D: backing out of a question deletes nothing."""
        try:
            return await question.unsafe_ask_async(patch_stdout=False)
        except (KeyboardInterrupt, EOFError):
            return None

    async def choose(self, question: str, choices: list[tuple[str, int]]) -> int | None:
        return await self._answer(questionary.select(
            question,
            choices=[questionary.Choice(title=label, value=value) for label, value in choices],
            instruction=CHOOSE_HINT,
            **self.io,
        ))

    async def pick(self, question: str, rows: list[str]) -> list[int] | None:
        return await self._answer(questionary.checkbox(
            question,
            choices=[questionary.Choice(title=text, value=i, checked=False) for i, text in enumerate(rows)],
            instruction=PICK_HINT,
            **self.io,
        ))

    async def type_in(self, prompt: str) -> str | None:
        return await self._answer(questionary.text(prompt, **self.io))

    async def yes_no(self, question: str, default: bool) -> bool:
        answer = await self._answer(questionary.confirm(question, default=default, **self.io))
        return default if answer is None else answer
