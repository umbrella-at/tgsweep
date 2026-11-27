# tgsweep

English · [Русский](README.ru.md)

Deletes your messages, comments on posts and reactions in the Telegram groups you select. Runs on your computer: macOS, Windows or Linux. No server or bot needed.

Step-by-step guide for Windows, macOS and Linux: https://tgsweep.atworks.dev/en/

What tgsweep never does:

- never opens private chats, bots, Saved Messages or the Telegram service chat;
- never touches other people's messages, even if you are a group admin;
- never touches groups you created or your posts in channels;
- deletes nothing until you select chats and confirm.

## Installation

You need uv, a manager for Python programs. It installs Python by itself.

macOS and Linux, in Terminal:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then make uv available in the current window (or just open a new terminal):

```sh
source $HOME/.local/bin/env
```

```powershell
$env:Path = "$HOME\.local\bin;$env:Path"
```

Now install tgsweep:

```sh
uv tool install https://github.com/umbrella-at/tgsweep/archive/refs/heads/main.zip
```

You do not need Git for this. To update, run the same command with `--reinstall`, it downloads the latest version:

```sh
uv tool install --reinstall https://github.com/umbrella-at/tgsweep/archive/refs/heads/main.zip
```

`uv tool upgrade tgsweep` does not work here: uv does not download an archive from the same address again and answers "Nothing to upgrade".

## api_id and api_hash

Third-party Telegram programs need their own key. It is free:

1. Open https://my.telegram.org and sign in with your phone number. The code comes to Telegram.
2. Choose API development tools and create an application. Any title and URL will do, platform Desktop.
3. On the first run tgsweep asks for App api_id and App api_hash. The api_hash stays hidden as you type.

They are saved only on your computer.

## Usage

```sh
tgsweep
```

1. Sign in with a QR code: on your phone, Telegram > Settings > Devices > Link Desktop Device. If you have Two-Step Verification on, tgsweep asks for that password. If scanning does not work, sign in with a code: `tgsweep --code`.
2. Choose which messages to delete: all, including today's, or older than 1, 2, 7 or 30 days.
3. tgsweep finds your messages in groups and your comments on channel posts. In large chats the search takes a few minutes, and the bottom line shows which chat it is checking and how much time has passed. The next search is faster: tgsweep remembers the chats where none of your reactions were found and reads only new messages there.
4. Select chats with Space and press Enter. At first nothing is selected.
5. Type the number tgsweep shows: that many messages and reactions will be deleted. They cannot be restored.
6. Everything selected is deleted in one go, with pauses so that Telegram does not limit your account. The bottom line shows how much is deleted already. If Telegram asks to wait, tgsweep waits and says so. Ctrl+C stops after the current request, and the rest will be found on the next run.
7. At the end tgsweep offers to sign out on this computer. It is safer: the session file opens your whole account.

To sign out later: `tgsweep --logout`. You can also end the session in Telegram itself: Settings > Devices > the tgsweep session > Terminate Session.

The program uses your system language: Russian on a Russian system, English otherwise. To set it manually: `tgsweep --lang en` or `--lang ru`, the choice is remembered; back to automatic: `--lang auto`. The `TGSWEEP_LANG` variable (ru, en or auto) overrides the saved choice.

## Where the files are

| System | Folder |
|---|---|
| macOS | `~/Library/Application Support/tgsweep` |
| Windows | `%LOCALAPPDATA%\tgsweep` |
| Linux | `~/.local/share/tgsweep` |

It holds the session file (`tgsweep.session`), the api_id and api_hash (`credentials.json`), `settings.json` with the chosen language if you changed it, and the deletion log `logs/actions.jsonl`: chat and message numbers, no texts. You can set another folder with the `TGSWEEP_HOME` variable.

## Uninstall

```sh
tgsweep --logout
uv tool uninstall tgsweep
```

Then delete the folder from the table above.

## License

MIT
