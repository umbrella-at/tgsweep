# Changelog

All notable changes to tgsweep are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version numbers follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Python 3.15: the tests run on it on Linux, macOS and Windows, and the package lists it. uv 0.13 installs Python 3.15 for new users.

### Changed

- The package description on PyPI reads "Delete your own messages, comments and reactions in the Telegram groups you select". The old one, "from the chats you pick", sounded as if private chats were touched and left out comments and reactions.

## [0.3.0] - 2026-11-27

The first release on PyPI. Version 0.2.0 was never released: builds with that number were installed from the archive of the main branch, and their changes are listed here.

### Added

- Install from PyPI with `uv tool install tgsweep` and update with `uv tool upgrade tgsweep`. If you installed tgsweep from the GitHub archive link, switch once with `uv tool install --force tgsweep`.
- English. The program speaks the system language: Russian on a Russian system, English otherwise. `--lang en` or `--lang ru` sets it and is remembered, `--lang auto` goes back to the system language, and the `TGSWEEP_LANG` variable overrides the saved choice.
- Letter keys work on the Russian keyboard layout: ф selects all chats, о and л move the cursor, н and т answer the sign-out question.

### Changed

- The QR code leaves the screen once it is not needed: before the next code, before the Two-Step Verification password, after sign-in, on an error and on Ctrl+C. Only one code is on the screen at a time. "Signed in." is printed after a QR or code sign-in.
- Long lines in the terminal wrap between words, never in the middle of one. Commands, links and the program folder are never split.
- The answers to the questions (the chats you selected, yes or no) are shown in the program's language.
- Dependencies have upper bounds, so a new major release of a library cannot break a new install. questionary and prompt-toolkit stay within their current minor versions.
- Proofread Russian messages: shorter notes on the skipped chats, and the sign-in help says that tgsweep works only with groups and channels instead of claiming it sees nothing else.

## [0.1.1] - 2026-11-26

### Added

- A status line during the search and the deletion, with a timer: which chat out of how many, how many messages are found and deleted, and when Telegram asks to wait. Telethon warnings are printed above it.
- The search remembers where it stopped in chats without your reactions and next time reads only new messages there.

### Changed

- Everything you confirm is deleted in one run, batch by batch, without searching again. The limits per run only set the batch size, and a reaction batch of 0 turns the reaction search off.
- Ctrl+C stops after the current request, and the report says how much is left. A reaction that could not be removed is found by the next search.

## [0.1.0] - 2026-11-26

### Added

- Deletes your own messages, comments on channel posts and reactions in the Telegram groups you select, on your own computer.
- Never opens private chats, bots, Saved Messages or the Telegram service chat, and never touches other people's messages, groups you created or your posts in channels.
- Sign-in with a QR code or with a code (`--code`), and an offer to sign out at the end. `--logout` signs out later.
- Nothing is deleted until you select chats and type the number of messages and reactions to delete.
- The deletion log `logs/actions.jsonl` holds chat and message numbers, no texts.

[Unreleased]: https://github.com/umbrella-at/tgsweep/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/umbrella-at/tgsweep/compare/v0.1.1...v0.3.0
[0.1.1]: https://github.com/umbrella-at/tgsweep/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/umbrella-at/tgsweep/tree/v0.1.0
