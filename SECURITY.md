# Security policy

## Reporting a vulnerability

Report it privately on GitHub: open the Security tab of the repository and choose "Report a vulnerability", or go straight to https://github.com/umbrella-at/tgsweep/security/advisories/new. Only you and the maintainer see the report and the discussion in it. Please do not open a public issue for a vulnerability.

Describe what happens, on which system and tgsweep version (`tgsweep --version`), and how to repeat it.

Fixes go into the next release on PyPI. Update with `uv tool upgrade tgsweep`.

## Scope

tgsweep runs on your computer and talks only to Telegram. In scope:

- The session file `tgsweep.session`, which opens your whole Telegram account: where it is written, who else on the computer can read it, and whether `tgsweep --logout` and the sign-out at the end of a run remove it.
- The key, the api_id and api_hash in `credentials.json`: where it is stored, who else can read it, and any way it leaks to the screen, the log or anywhere else.
- The deletion log `logs/actions.jsonl`: it holds chat and message numbers and must never hold message texts.
- Any data sent anywhere other than Telegram's own servers.

Other bugs, including wrong messages being deleted, are welcome as ordinary issues: https://github.com/umbrella-at/tgsweep/issues
