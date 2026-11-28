"""Static checks over the whole package source: what may send, delete or sign out, and where."""

from __future__ import annotations

import ast
import html
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from importlib.metadata import packages_distributions
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
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


# The website: the Russian page at / and the English one at /en/, the same markup with different words.
PAGES = {"ru": REPO / "site" / "index.html", "en": REPO / "site" / "en" / "index.html"}
URLS = {"ru": "https://tgsweep.atworks.dev/", "en": "https://tgsweep.atworks.dev/en/"}
READMES = [REPO / "README.md", REPO / "README.ru.md"]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def page(lang: str) -> str:
    return PAGES[lang].read_text(encoding="utf-8")


def site_files() -> list[Path]:
    files = sorted(p for p in (REPO / "site").rglob("*") if p.suffix in {".html", ".js", ".css"})
    assert set(PAGES.values()) <= set(files), "the website was not found"
    return files


def test_no_dashes_in_human_texts():
    targets = list(source_files().values())
    targets += sorted(REPO.glob("*.md"))  # every README, whatever its language
    assert set(READMES) <= set(targets), "a README was not found"
    targets += site_files() + sorted((REPO / "docs").glob("*.md"))
    targets += [path for pattern in ("*.html", "*.py", "*.sh") for path in sorted((REPO / "scripts").glob(pattern))]
    tapes = sorted((REPO / "docs" / "media").glob("*.tape"))
    assert len(tapes) == 2, "the README recordings: one script per language"
    targets += tapes
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text, path


def test_install_archive_leaves_the_recordings_out(tmp_path):
    """main.zip, the archive of the main branch (an unreleased build), is made by git archive: no GIFs in it."""
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    archive = tmp_path / "main.zip"
    # Attributes of the working tree too, so the check holds before the commit; CI checks out the commit itself.
    subprocess.run(["git", "archive", "--format=zip", "--worktree-attributes", "-o", str(archive), "HEAD"],
                   cwd=REPO, check=True)
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
    assert not [name for name in names if name.startswith("docs/media")]
    assert {"README.md", "pyproject.toml", "LICENSE", "src/tgsweep/__init__.py"} <= names
    assert (REPO / "docs/media/demo-en.gif").is_file()  # the recordings are still in the repository


# What a newcomer pastes. The site and the README must give the same commands.
INSTALL_COMMANDS = {
    'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
    "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "source $HOME/.local/bin/env",
    '$env:Path = "$HOME\\.local\\bin;$env:Path"',
    "uv tool install tgsweep",
    "uv tool upgrade tgsweep",
    # Moves an install from the GitHub archive to PyPI: without --force uv keeps the archive's build.
    "uv tool install --force tgsweep",
}
# Said right before the --force command, on both pages and in both READMEs.
SWITCH_NOTE = {
    "ru": "Если вы ставили tgsweep раньше по ссылке на архив с GitHub, один раз перейдите на версию с PyPI:",
    "en": "If you installed tgsweep earlier from the GitHub archive link, switch to the PyPI version once:",
}
README_LANG = {"README.md": "en", "README.ru.md": "ru"}


def page_commands(text: str) -> list[str]:
    """The commands to paste, in page order: <code> without a class (inline mentions have one)."""
    return [html.unescape(re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<code>(.*?)</code>", text)]


def test_site_and_readme_install_the_same_way():
    for readme_path in READMES:
        readme = readme_path.read_text(encoding="utf-8")
        for command in INSTALL_COMMANDS:
            assert command in readme, (readme_path.name, command)
        for old in ("git+", "main.zip", "--reinstall", "Nothing to upgrade"):
            assert old not in readme, (readme_path.name, old)
        note = SWITCH_NOTE[README_LANG[readme_path.name]]
        assert f"{note}\n\n```sh\nuv tool install --force tgsweep\n```" in readme, readme_path.name
    for lang in PAGES:
        commands = set(page_commands(page(lang)))
        for command in INSTALL_COMMANDS:
            assert command in commands, (lang, command)
        tool = {c for c in commands if c.startswith("uv tool") and c != "uv tool uninstall tgsweep"}
        assert tool == {"uv tool install tgsweep", "uv tool upgrade tgsweep", "uv tool install --force tgsweep"}
        for old in ("git+", "main.zip", "--reinstall", "Nothing to upgrade"):
            assert old not in page(lang), (lang, old)
        text = visible_text(page(lang))
        assert f"{SWITCH_NOTE[lang]}\n      uv tool install --force tgsweep" in text, lang
    # Both pages give the same commands in the same places.
    assert len(page_commands(page("ru"))) > 10
    assert page_commands(page("en")) == page_commands(page("ru"))


def test_caddy_site_matches_the_page():
    # Both CSPs apply in the browser, so a source added to only one of them is still blocked.
    caddy = (REPO / "deploy" / "tgsweep.caddy").read_text(encoding="utf-8")
    header = re.findall(r'^\s*Content-Security-Policy "([^"]+)"$', caddy, re.M)
    assert len(header) == 1
    for lang in PAGES:
        meta = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', page(lang))
        assert len(meta) == 1, lang
        assert header[0] == meta[0] + "; frame-ancestors 'none'", lang
    # The page promises no personal data: no access log with visitor IPs.
    directives = [line.split("#")[0].strip() for line in caddy.splitlines()]
    assert "tgsweep.atworks.dev {" in directives
    assert not [d for d in directives if d.startswith(("log", "import"))]


# The made-up account on the page mockups, in each language.
MOCKUP = {
    "ru": {
        "name": "Мария (@maria_demo)",
        "scanned": "Испанский клуб",
        "chats": ["Соседи по дому", "Книжный клуб", "Бег по утрам", "Объявления района"],
    },
    "en": {
        "name": "Maria (@maria_demo)",
        "scanned": "Spanish club",
        "chats": ["Building neighbors", "Book club", "Morning runs", "Neighborhood notices"],
    },
}


def visible_text(text: str) -> str:
    """What a visitor reads: no tags, and no script blocks (the demo's word list is not on screen)."""
    return html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<script\b.*?</script>", "", text, flags=re.S)))


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_site_mockups_match_program(lang):
    # The terminal mockups on the page repeat the program's output word for word. A line can
    # appear in several mockups, so every place that starts like it must go on exactly like it.
    from tgsweep import app, cli, i18n, signin, ui
    from tgsweep.cleanup import ApplyProgress, ScanProgress
    from tgsweep.home import shown_path

    i18n.set_lang(lang)
    text = visible_text(page(lang))
    lines = [app.skip_note(reason) for reason in app.SKIP_NOTES if reason != "unsupported"]
    lines += [ui.pick_hint(), ui.choose_hint(), signin.qr_hint(), cli.login_help()]
    lines += [line.strip() for line in cli.credentials_help().splitlines()]
    assert len(lines) == 12
    # Lines with numbers, put together by the program's own functions from the mockups' numbers.
    demo = MOCKUP[lang]
    counted = [
        i18n.t("search_took", duration=app.duration(50), chats=i18n.plural(21, "chat")),
        i18n.t("will_delete", counts=app.counts(49, 0, 4), chats=i18n.plural(2, "chat_in"),
               minutes=i18n.plural(1, "minute")),
        i18n.t("confirm_number", total=53),
        i18n.t("report_done", messages=49, comments=0, reactions=4),
        app.apply_line(ApplyProgress(done=37, total=53, chat=demo["chats"][0])),
        app.scan_line(ScanProgress(phase="messages", chat=9, chats=21, found=60, title=demo["scanned"])),
        i18n.t("account", name=demo["name"]),
        # Maria's journal on macOS, printed from inside her home folder as the program prints it.
        i18n.t("report_journal", path=shown_path("/Users/maria/Library/Application Support/tgsweep/logs/actions.jsonl",
                                                 home="/Users/maria", windows=False)),
    ]
    counted += [f"{title}: {app.counts(m, 0, r)}" for title, (m, r) in zip(demo["chats"], [(37, 0), (12, 4), (8, 0), (3, 2)])]
    assert len(counted) == 12
    lines += counted
    missing = [line for line in lines if line not in text]
    assert missing == []
    stale = [line for line in lines if text.count(line[:20]) != text.count(line)]
    assert stale == []
    # The answer to the last question, in the page's language as the program gives it.
    assert f"? {i18n.t('ask_sign_out')} {i18n.t('answer_yes')}\n" in text


class Shape(HTMLParser):
    """The skeleton of a page: ids, steps, commands, questions, screenshots and blocks per system."""

    def __init__(self, text: str):
        super().__init__()
        self.ids: list[str] = []
        self.os: list[str] = []
        self.counts: Counter[str] = Counter()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "id" in a:
            self.ids.append(a["id"])
        if "data-os" in a:
            self.os.append(a["data-os"])
        for name in ("step", "cmd"):
            self.counts[name] += name in classes
        self.counts["details"] += tag == "details"
        self.counts["figure.shot"] += tag == "figure" and "shot" in classes


class CommandSystems(HTMLParser):
    """The systems each command is shown for: the data-os of the nearest block around it."""

    VOID = {"area", "br", "hr", "img", "input", "link", "meta", "source", "wbr"}

    def __init__(self, text: str):
        super().__init__()
        self.stack: list[str | None] = []
        self.code: str | None = None
        self.systems: dict[str, str | None] = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "code" and not a.get("class"):
            self.code = ""
        if tag not in self.VOID:
            self.stack.append(a.get("data-os"))

    def handle_endtag(self, tag):
        if tag == "code" and self.code is not None:
            self.systems[self.code] = next((os for os in reversed(self.stack) if os), None)
            self.code = None
        if tag not in self.VOID:
            self.stack.pop()

    def handle_data(self, data):
        if self.code is not None:
            self.code += data


@pytest.mark.parametrize("lang", PAGES)
def test_each_system_gets_its_own_commands(lang):
    systems = CommandSystems(page(lang)).systems
    assert systems["source $HOME/.local/bin/env"] == "mac linux"
    assert systems['$env:Path = "$HOME\\.local\\bin;$env:Path"'] == "win"
    assert systems["curl -LsSf https://astral.sh/uv/install.sh | sh"] == "mac linux"
    assert systems['powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'] == "win"
    assert systems["uv tool install tgsweep"] is None


def test_pages_have_the_same_shape():
    ru, en = Shape(page("ru")), Shape(page("en"))
    assert ru.counts["step"] == 11 and ru.counts["details"] >= 10 and len(ru.os) > 10
    assert sorted(en.ids) == sorted(ru.ids)
    assert en.counts == ru.counts
    assert en.os == ru.os


def test_english_page_speaks_english():
    # Only the switch to the Russian page, marked lang="ru", is in Russian.
    text = page("en")
    outside = re.sub(r'<(\w+)\b[^>]*\blang="ru"[^>]*>.*?</\1>', "", text, flags=re.S)
    assert outside != text
    assert CYRILLIC.findall(outside) == []
    assert re.search(r'<html lang="en">', text)


def test_pages_point_to_each_other():
    alternates = [
        ("ru", "https://tgsweep.atworks.dev/"),
        ("en", "https://tgsweep.atworks.dev/en/"),
        ("x-default", "https://tgsweep.atworks.dev/en/"),
    ]
    switch = {
        "ru": '<a href="/en/" hreflang="en" lang="en">English</a>',
        "en": '<a href="/" hreflang="ru" lang="ru">Русский</a>',
    }
    for lang, url in URLS.items():
        text = page(lang)
        assert re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', text) == alternates
        assert re.findall(r'<link rel="canonical" href="([^"]+)">', text) == [url]
        assert re.findall(r'<meta property="og:url" content="([^"]+)">', text) == [url]
        nav = re.findall(r'<nav aria-label="[^"]+">(.*?)</nav>', text, re.S)
        assert len(nav) == 1
        assert re.findall(r"<a [^>]*>[^<]*</a>", nav[0])[-1] == switch[lang], lang


def test_english_link_preview_image_is_published():
    meta = dict(re.findall(r'<meta property="(og:[a-z:_]+)" content="([^"]*)">', page("en")))
    assert meta["og:image"] == "https://tgsweep.atworks.dev/og-en.png"
    assert meta["og:locale"] == "en_US"
    png = (REPO / "site" / "og-en.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    assert (width, height) == (1200, 630)
    assert (meta["og:image:width"], meta["og:image:height"]) == ("1200", "630")
    assert len(png) < 300_000


def page_words(lang: str) -> dict:
    text = page(lang)
    blocks = re.findall(r'<script type="application/json" id="page-text">(.*?)</script>', text, re.S)
    assert len(blocks) == 1, lang
    assert text.index('id="page-text"') < text.index("data-goatcounter"), lang
    return json.loads(blocks[0])


def test_demo_words_match_program():
    from tgsweep import i18n

    words = {lang: page_words(lang) for lang in PAGES}
    assert words["ru"].keys() == words["en"].keys()
    for lang, data in words.items():
        for key in ("message", "reaction", "chat_in", "minute"):
            assert data[key] == list(i18n.PLURALS[key][lang]), (lang, key)
        for key in ("nothing", "none_picked", "will_delete", "confirm_number", "picked_nothing", "picked_many"):
            assert data[key] == i18n.TEXTS[key][lang], (lang, key)
    assert CYRILLIC.findall(json.dumps(words["en"], ensure_ascii=False)) == []
    # app.js asks for exactly these words: a missing one would break the demo without an error.
    script = (REPO / "site" / "app.js").read_text(encoding="utf-8")
    assert set(re.findall(r"\bT\.(\w+)", script)) == set(words["ru"])


def test_app_script_has_no_russian_words():
    # The words live on the pages. Only the Russian layout keys stay: the program takes о, л, ф too.
    script = (REPO / "site" / "app.js").read_text(encoding="utf-8")
    start = script.index('list.addEventListener("keydown"')
    end = script.index("});", start)
    assert CYRILLIC.findall(script[:start] + script[end:]) == []
    assert set(CYRILLIC.findall(script[start:end])) == {"о", "л", "ф"}


def test_link_preview_image_is_published():
    # Telegram builds the link preview from og:image: an absolute URL to a file the deploy uploads.
    page = (REPO / "site" / "index.html").read_text(encoding="utf-8")
    meta = dict(re.findall(r'<meta property="(og:[a-z:_]+)" content="([^"]*)">', page))
    assert meta["og:image"] == "https://tgsweep.atworks.dev/og.png"
    png = (REPO / "site" / "og.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    assert (width, height) == (1200, 630)
    assert (meta["og:image:width"], meta["og:image:height"]) == ("1200", "630")
    assert len(png) < 300_000


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


# --- the package on PyPI --------------------------------------------------------


def project() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]


# The bounds of the release brief: below the next major release, and questionary and prompt-toolkit
# within their minor version, because ui.py relies on how they build a question.
UPPER_BOUNDS = {
    "telethon": "<2", "questionary": "<2.2", "prompt-toolkit": "<3.1", "pydantic": "<3",
    "platformdirs": "<5", "segno": "<2", "pyyaml": "<7", "wcwidth": "<1",
}


def test_every_dependency_has_an_upper_bound():
    # uv tool install resolves these ranges, not uv.lock: without a bound a new install gets any release.
    bounds = {}
    for requirement in project()["dependencies"]:
        name, spec = re.fullmatch(r"([A-Za-z0-9._-]+)(.*)", requirement).groups()
        parts = [part.strip() for part in spec.split(",")]
        assert len(parts) == 2 and parts[0].startswith(">="), requirement
        assert parts[1].startswith("<") and not parts[1].startswith("<="), requirement
        bounds[name.lower()] = parts[1]
    assert bounds == UPPER_BOUNDS


def test_every_imported_library_is_a_direct_dependency():
    # ui.py imports prompt_toolkit itself: it must not arrive only because questionary needs it.
    def normal(name: str) -> str:  # PEP 503: prompt_toolkit and prompt-toolkit are one name
        return re.sub(r"[-_.]+", "-", name).lower()

    declared = {normal(re.match(r"[A-Za-z0-9._-]+", r).group()) for r in project()["dependencies"]}
    distributions = packages_distributions()
    imported = set()
    for path in source_files().values():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    libraries = imported - set(sys.stdlib_module_names) - {"tgsweep"}
    assert {"prompt_toolkit", "questionary", "telethon", "yaml"} <= libraries
    used = {normal(distributions[name][0]) for name in libraries}
    assert used <= declared, used - declared


def changelog_version(text: str) -> str:
    """The version of the first numbered section. Only [Unreleased] may stand above it."""
    headings = re.findall(r"^## (.*)$", text, flags=re.M)
    if headings[:1] == ["[Unreleased]"]:
        headings = headings[1:]
    versions = [re.fullmatch(r"\[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}", h) for h in headings]
    assert versions and all(versions), headings
    return versions[0].group(1)


def test_one_version_in_pyproject_the_package_and_the_changelog():
    version = project()["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert tgsweep.__version__ == version
    assert changelog_version((REPO / "CHANGELOG.md").read_text(encoding="utf-8")) == version


@pytest.mark.parametrize("top", ["", "## [Unreleased]\n\n- coming\n\n", "## [Unreleased]\n\n"],
                         ids=["no unreleased", "unreleased", "empty unreleased"])
def test_the_changelog_version_is_the_first_numbered_section(top):
    text = f"# Changelog\n\n{top}## [1.2.3] - 2026-01-02\n\n- one\n\n## [1.2.2] - 2026-01-01\n\n- two\n"
    assert changelog_version(text) == "1.2.3"


@pytest.mark.parametrize("text", [
    "## [1.2.3] - 2026-01-02\n\n## [Unreleased]\n",
    "## [Unreleased]\n\n## [Unreleased]\n\n## [1.2.3] - 2026-01-02\n",
    "## [Unreleased]\n\n## Notes\n\n## [1.2.3] - 2026-01-02\n",
    "## [Unreleased]\n\n- only this\n",
], ids=["unreleased below", "two unreleased", "other heading", "nothing numbered"])
def test_the_changelog_takes_only_unreleased_and_numbered_sections(text):
    with pytest.raises(AssertionError):
        changelog_version(text)


@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_readme_links_are_absolute(readme):
    # PyPI shows README.md as the project page: a link relative to the repository leads nowhere there.
    text = readme.read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    targets += re.findall(r"^\[[^\]]+\]:\s*(\S+)", text, flags=re.M)
    targets += re.findall(r"(?:src|href)=[\"']([^\"']+)", text)
    assert len(targets) >= 8, targets
    relative = [t for t in targets if not t.startswith(("https://", "http://", "mailto:"))]
    assert relative == []


# --- the page in the visitor's language (site/lang.js) ---------------------------

# The CSP as it stands: lang.js is a file of the site, so 'self' lets it in and nothing changes.
CSP = ("default-src 'self'; script-src 'self' https://gc.zgo.at; connect-src 'self' https://*.goatcounter.com; "
       "img-src 'self' data: https://*.goatcounter.com; style-src 'self'; font-src 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'none'")
LANG_SCRIPT = REPO / "site" / "lang.js"


def test_the_csp_stays_as_it_was():
    caddy = (REPO / "deploy" / "tgsweep.caddy").read_text(encoding="utf-8")
    assert re.findall(r'^\s*Content-Security-Policy "([^"]+)"$', caddy, re.M) == [CSP + "; frame-ancestors 'none'"]
    for lang in PAGES:
        assert re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', page(lang)) == [CSP], lang


@pytest.mark.parametrize("lang", PAGES)
def test_the_language_script_runs_before_the_page_is_drawn(lang):
    # In <head>, blocking (no defer, no async), ahead of the stylesheet: the switch happens before any paint.
    assert LANG_SCRIPT.is_file()
    text = page(lang)
    head = text[:text.index("</head>")]
    tags = [tag for tag in re.findall(r"<script\b[^>]*>", text) if "lang.js" in tag]
    assert tags == ['<script src="/lang.js">'], lang
    at = head.index('<script src="/lang.js"></script>')
    assert head.index('<meta http-equiv="Content-Security-Policy"') < at < head.index('<link rel="stylesheet"')
    assert at < head.index('<link rel="preload"')  # no font is fetched for a page about to be left


ROBOT_AGENTS = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "TelegramBot (like TwitterBot)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Mozilla/5.0 (compatible; Yahoo! Slurp; http://help.yahoo.com/help/us/ysearch/slurp)",
]
PEOPLE_AGENTS = [
    # The Telegram in-app browser on Android: a person, who needs the right language most.
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP2A.240805.005; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/131.0.6778.135 Mobile Safari/537.36 Telegram-Android/11.5.3 (Google Pixel 8; Android 14; SDK 34; HIGH)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
]


def test_the_language_script_leaves_robots_where_they_came():
    script = LANG_SCRIPT.read_text(encoding="utf-8")
    pattern = re.search(r"const ROBOTS = /(.+)/i;", script).group(1)
    assert pattern == "bot|crawl|spider|slurp|facebookexternalhit"
    assert [ua for ua in ROBOT_AGENTS if not re.search(pattern, ua, re.I)] == []
    assert [ua for ua in PEOPLE_AGENTS if re.search(pattern, ua, re.I)] == []


def test_the_language_script_remembers_without_cookies():
    script = LANG_SCRIPT.read_text(encoding="utf-8")
    assert 'const KEY = "tgsweep-lang";' in script
    assert "document.cookie" not in script
    assert "location.replace(" in script and "location.assign" not in script and "location.href =" not in script
    assert CYRILLIC.findall(script) == []


def test_the_language_script_keeps_the_source_of_the_visit():
    # After location.replace the referrer is this site. The real one, whole, goes to the new page in
    # sessionStorage and from there to GoatCounter's referrer setting; the address stays as it came,
    # so a link copied after the switch carries no source of its own.
    script = LANG_SCRIPT.read_text(encoding="utf-8")
    assert "ref=" not in script and "hostname" not in script
    assert "location.replace(PAGES[wanted] + location.search + location.hash);" in script
    assert 'const REF_KEY = "tgsweep-ref";' in script
    assert "sessionStorage.setItem(REF_KEY, value);" in script
    assert "keepReferrer(document.referrer);" in script
    assert script.index("keepReferrer(document.referrer);") < script.index("location.replace(")
    # Read once, then gone: a reload must not count the old source again.
    read = script.index("const value = sessionStorage.getItem(REF_KEY);")
    assert read < script.index("sessionStorage.removeItem(REF_KEY);") < script.index("return value;", read)
    assert "window.goatcounter = Object.assign(window.goatcounter || {}, { referrer: kept });" in script
    # GoatCounter reads its settings when count.js loads: the language script must come first.
    for lang in PAGES:
        text = page(lang)
        assert text.index('<script src="/lang.js">') < text.index("data-goatcounter="), lang


SIMILAR = {"README.md": "## Similar tools", "README.ru.md": "## Похожие программы"}
SIMILAR_LINKS = [
    "https://github.com/gurland/telegram-delete-all-messages",
    "https://github.com/en9inerd/tgeraser",
    "https://github.com/rusq/wipemychat",
]


@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_the_readme_names_similar_tools(readme):
    # Right before the license, with the three tools people find first.
    text = readme.read_text(encoding="utf-8")
    sections = re.findall(r"^## .+$", text, re.M)
    heading = SIMILAR[readme.name]
    assert sections[-2:] == [heading, {"README.md": "## License", "README.ru.md": "## Лицензия"}[readme.name]]
    body = text[text.index(heading):text.index(sections[-1])]
    assert re.findall(r"\]\((https://[^)]+)\)", body) == SIMILAR_LINKS
