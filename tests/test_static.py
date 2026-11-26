"""Static checks over the whole package source: what may send, delete or sign out, and where."""

from __future__ import annotations

import ast
import html
import re
from pathlib import Path

from telethon import TelegramClient

import tgsweep

# Keys below are '<package>/<file>'.
PACKAGES = {"tgsweep": Path(tgsweep.__file__).parent}
REPO = PACKAGES["tgsweep"].parents[1]


def source_files() -> dict[str, Path]:
    return {f"{pkg}/{path.name}": path for pkg, root in PACKAGES.items() for path in sorted(root.glob("*.py"))}

# Methods that send, forward, edit, mark read, join/leave, ban, download or delete.
BANNED_METHODS = {
    "send_message", "send_file", "forward_messages", "edit_message", "delete_dialog", "kick_participant",
    "edit_permissions", "edit_admin", "send_read_acknowledge", "mark_read", "forward_to", "respond", "reply",
    "edit", "pin_message", "unpin_message", "pin", "unpin", "click", "download_media", "download_file",
    "download_profile_photo", "iter_download", "upload_file", "delete", "action", "edit_folder",
    "delete_messages", "log_out", "inline_query", "sign_up", "edit_2fa", "takeout", "conversation",
}

# The only places where a banned method may appear, as (file, function, method).
ALLOWED_PLACES = {
    ("tgsweep/cleanup.py", "delete_own", "delete_messages"),
    ("tgsweep/cli.py", "log_out_here", "log_out"),  # signs this computer out, nothing else
}

# Public TelegramClient methods the package may call at all.
ALLOWED_CLIENT_METHODS = {
    "connect", "disconnect", "is_user_authorized", "get_me", "get_entity", "iter_messages", "get_messages",
    "iter_dialogs", "iter_participants", "start", "on", "run_until_disconnected", "get_peer_id",
    "delete_messages", "send_message", "edit_message", "log_out", "is_connected", "sign_in", "qr_login",
}

# Also banned as plain references (f = client.delete_messages, functools.partial(bot.send_message, ...)).
# "action" is a Message field, so only its call is banned. Dialog.archive() moves chats between folders.
REF_BANNED = (BANNED_METHODS - {"action"}) | {"archive"}
REFLECTION = ("__getattribute__", "methodcaller", "vars(", "__dict__", "import operator", "from operator")

ALLOWED_REQUESTS = {
    "GetFullChannelRequest", "GetFullChatRequest", "GetMessagesReactionsRequest", "SendReactionRequest",
    "GetAuthorizationsRequest", "ResetAuthorizationRequest", "SendCodeRequest", "ResendCodeRequest",
}
REQUEST_PLACES = {
    "SendReactionRequest": {("tgsweep/cleanup.py", "remove_my_reaction")},
    "SendCodeRequest": {("tgsweep/signin.py", "request_code")},
    "ResendCodeRequest": {("tgsweep/signin.py", "code_login")},
}
SPOT_CHECK_BANNED_REQUESTS = {
    "JoinChannelRequest", "LeaveChannelRequest", "ImportChatInviteRequest", "DeleteHistoryRequest",
    "DeleteAccountRequest", "DeleteChatUserRequest", "SendMessageRequest", "ForwardMessagesRequest",
    "EditMessageRequest", "ReadHistoryRequest", "DeleteMessagesRequest", "LogOutRequest", "SendMediaRequest",
}


class Collector(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.stack: list[str] = []
        self.calls: list[tuple[str, str, ast.Call]] = []
        self.attrs: list[tuple[str, str]] = []
        self.imports: list[tuple[str, str, str | None]] = []
        self.requests: list[tuple[str, str, ast.AST]] = []
        self.getattrs: list[ast.Call] = []

    def _scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    @property
    def scope(self) -> str:
        return ".".join(self.stack)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute):
            self.calls.append((self.scope, node.func.attr, node))
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            self.getattrs.append(node)
        self.generic_visit(node)

    def _request(self, name: str, node):
        if name.endswith("Request") and name[:1].isupper():
            self.requests.append((self.scope, name, node))

    def visit_Attribute(self, node: ast.Attribute):
        self._request(node.attr, node)
        self.attrs.append((self.scope, node.attr))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for alias in node.names:
            self.imports.append((node.module or "", alias.name, alias.asname))
            self._request(alias.name, node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        self._request(node.id, node)


def collect() -> dict[str, Collector]:
    out = {}
    for name, path in source_files().items():
        c = Collector(name)
        c.visit(ast.parse(path.read_text(encoding="utf-8")))
        out[name] = c
    assert len(out) >= 12
    return out


def test_banned_methods_only_in_allowed_places():
    found = set()
    for name, c in collect().items():
        for scope, attr, _ in c.calls:
            if attr in BANNED_METHODS:
                found.add((name, scope, attr))
    assert found - ALLOWED_PLACES == set()
    assert found == ALLOWED_PLACES, "an allowed place disappeared: update the test deliberately"


def test_only_allowed_client_methods():
    client_methods = {n for n in dir(TelegramClient) if not n.startswith("_")}
    assert {"send_message", "send_file", "delete_dialog", "send_read_acknowledge"} <= client_methods
    used = set()
    for name, c in collect().items():
        for scope, attr, _ in c.calls:
            if attr in client_methods:
                used.add(attr)
    assert used - ALLOWED_CLIENT_METHODS == set()


def test_only_allowed_raw_requests():
    used: dict[str, set[tuple[str, str]]] = {}
    for name, c in collect().items():
        for scope, req, _ in c.requests:
            used.setdefault(req, set()).add((name, scope))
    assert set(used) - ALLOWED_REQUESTS == set()
    assert not set(used) & SPOT_CHECK_BANNED_REQUESTS
    for req, places in REQUEST_PLACES.items():
        assert used.get(req, set()) <= places, req


def test_send_reaction_only_removes():
    c = collect()["tgsweep/cleanup.py"]
    calls = [
        call for scope, attr, call in c.calls if attr == "SendReactionRequest"
    ]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw["reaction"], ast.List) and kw["reaction"].elts == []


def test_no_dynamic_attribute_access():
    for name, c in collect().items():
        for call in c.getattrs:
            key = call.args[1]
            assert isinstance(key, ast.Constant) and isinstance(key.value, str), f"{name}: dynamic getattr"
            assert key.value not in BANNED_METHODS
        source = source_files()[name].read_text(encoding="utf-8")
        assert "__import__" not in source and "importlib" not in source


def site_files() -> list[Path]:
    files = sorted(p for p in (REPO / "site").glob("*") if p.suffix in {".html", ".js", ".css"})
    assert REPO / "site" / "index.html" in files, "the website was not found"
    return files


def test_no_dashes_in_human_texts():
    targets = list(source_files().values())
    targets += [p for p in [REPO / "README.md"] if p.is_file()]
    assert len(targets) > 12, "the README was not found"
    targets += site_files() + sorted((REPO / "docs").glob("*.md"))
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text, path


# What a newcomer pastes. The site and the README must give the same commands.
INSTALL_COMMANDS = {
    'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
    "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "uv tool install https://github.com/umbrella-at/tgsweep/archive/refs/heads/main.zip",
    "uv tool upgrade tgsweep",
}


def test_site_and_readme_install_the_same_way():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    page = (REPO / "site" / "index.html").read_text(encoding="utf-8")
    commands = {html.unescape(re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<code>(.*?)</code>", page)}
    for command in INSTALL_COMMANDS:
        assert command in readme, command
        assert command in commands, command
    installs = {c for c in commands if c.startswith("uv tool install")}
    assert installs == {"uv tool install https://github.com/umbrella-at/tgsweep/archive/refs/heads/main.zip"}
    assert "git+" not in readme and "git+" not in page


def test_caddy_site_matches_the_page():
    # Both CSPs apply in the browser, so a source added to only one of them is still blocked.
    page = (REPO / "site" / "index.html").read_text(encoding="utf-8")
    caddy = (REPO / "deploy" / "tgsweep.caddy").read_text(encoding="utf-8")
    meta = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', page)
    header = re.findall(r'^\s*Content-Security-Policy "([^"]+)"$', caddy, re.M)
    assert len(meta) == 1 and len(header) == 1
    assert header[0] == meta[0] + "; frame-ancestors 'none'"
    # The page promises no personal data: no access log with visitor IPs.
    directives = [line.split("#")[0].strip() for line in caddy.splitlines()]
    assert "tgsweep.atworks.dev {" in directives
    assert not [d for d in directives if d.startswith(("log", "import"))]


def test_banned_names_not_even_referenced():
    found = set()
    for name, c in collect().items():
        for scope, attr in c.attrs:
            if attr in REF_BANNED:
                found.add((name, scope, attr))
    assert found - ALLOWED_PLACES == set()


def test_imports_bring_no_banned_names():
    for name, c in collect().items():
        for module, imported, alias in c.imports:
            assert imported != "*", f"{name}: star import from {module}"
            assert imported not in BANNED_METHODS, f"{name}: imports {imported}"
            if imported.endswith("Request"):
                assert imported in ALLOWED_REQUESTS, f"{name}: imports {imported} from {module}"


def test_no_reflection_tricks():
    for name, path in source_files().items():
        source = path.read_text(encoding="utf-8")
        for needle in REFLECTION:
            assert needle not in source, f"{name}: {needle}"
