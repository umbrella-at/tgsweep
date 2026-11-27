"""The site palette lives in the tokens of site/style.css; everything that repeats a colour follows them.

The favicon, the address bar colour (theme-color), the QR sample, the link previews and the terminal
recordings in the README cannot read CSS variables, so they carry the values themselves. These tests
keep them equal to the tokens, keep every pair of colours readable (WCAG 2.1 contrast) and keep the
previous green palette from coming back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
CSS = SITE / "style.css"
PAGES = {"ru": SITE / "index.html", "en": SITE / "en" / "index.html"}
TAPES = sorted((REPO / "docs" / "media").glob("*.tape"))
TEMPLATE = REPO / "scripts" / "og.html"

TOKEN = re.compile(r"--([a-z][a-z-]*):\s*([^;]+);")
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b")


def block(css: str, selector: str, start: int = 0) -> str:
    """The body of the first rule with exactly this selector, from start on."""
    at = css.index(selector + " {", start)
    return css[at + len(selector) + 2:css.index("}", at)]


def tokens(body: str) -> dict[str, str]:
    return {name: value.strip().upper() for name, value in TOKEN.findall(body)}


def palette() -> dict[str, dict[str, str]]:
    css = CSS.read_text(encoding="utf-8")
    light = tokens(block(css, ":root"))
    media = css.index("@media (prefers-color-scheme: dark) {")
    dark_media = tokens(block(css, ':root:not([data-theme="light"])', media))
    dark_switch = tokens(block(css, ':root[data-theme="dark"]'))
    return {"light": light, "dark media": dark_media, "dark switch": dark_switch}


def themes() -> dict[str, dict[str, str]]:
    found = palette()
    return {"light": found["light"], "dark": {**found["light"], **found["dark switch"]}}


def luminance(color: str) -> float:
    """WCAG 2.1 relative luminance of #RRGGBB."""
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_contrast_formula():
    # Reference values of WCAG 2.1: black on white is 21, a colour on itself is 1.
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21)
    assert contrast("#777777", "#777777") == pytest.approx(1)
    assert contrast("#FFFFFF", "#767676") == pytest.approx(4.54, abs=0.01)


def test_the_tokens_are_found():
    found = palette()
    assert len(found["light"]) >= 20
    for name in ("bg", "surface", "ink", "accent", "t-bg", "safety-bg", "mark", "mark-in", "shadow"):
        assert name in found["light"], name
    assert len(found["dark media"]) >= 10


def test_both_dark_blocks_are_the_same():
    # One for the system's dark mode, one for the page's own switch: they must not drift apart.
    found = palette()
    assert found["dark media"] == found["dark switch"]


# (text, background, minimum): ink 7 (AAA), the rest 4.5 (AA for normal text).
PAIRS = [
    ("ink", "bg", 7), ("ink", "surface", 7),
    ("soft", "bg", 4.5), ("soft", "surface", 4.5),
    ("accent", "bg", 4.5), ("accent", "surface", 4.5),
    ("accent-ink", "accent", 4.5),
    ("bg", "ink", 7),  # the selected system button
    ("safety-ink", "safety-bg", 7),
    ("t-fg", "t-bg", 7), ("t-bright", "t-bg", 7),
    ("t-dim", "t-bg", 4.5), ("t-q", "t-bg", 4.5), ("t-ans", "t-bg", 4.5),
    ("t-dim", "t-bar", 4.5),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("text, background, minimum", PAIRS, ids=[f"{t} on {b}" for t, b, _ in PAIRS])
def test_text_is_readable(theme, text, background, minimum):
    colors = themes()[theme]
    ratio = contrast(colors[text], colors[background])
    assert ratio >= minimum, f"{theme}: {text} {colors[text]} on {background} {colors[background]} is {ratio:.2f}"


def test_the_address_bar_takes_the_page_background():
    colors = themes()
    for lang, path in PAGES.items():
        text = path.read_text(encoding="utf-8")
        found = dict((scheme, color.upper()) for color, scheme in re.findall(
            r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})" media="\(prefers-color-scheme: (light|dark)\)">', text))
        assert found == {"light": colors["light"]["bg"], "dark": colors["dark"]["bg"]}, lang


def test_the_favicon_is_the_mark():
    colors = themes()["light"]
    svg = (SITE / "favicon.svg").read_text(encoding="utf-8")
    assert re.search(r'<rect [^>]*fill="([^"]+)"', svg).group(1).upper() == colors["mark"]
    inner = re.findall(r'<circle [^>]*(?:fill|stroke)="(#[^"]+)"', svg)
    assert len(inner) == 2 and {c.upper() for c in inner} == {colors["mark-in"]}
    assert {c.upper() for c in HEX.findall(svg)} == {colors["mark"], colors["mark-in"]}


def test_the_qr_sample_is_drawn_in_palette_colors():
    colors = themes()["light"]
    for lang, path in PAGES.items():
        fills = {c.upper() for c in re.findall(r'fill="(#[0-9A-Fa-f]+)"', path.read_text(encoding="utf-8"))}
        assert fills == {colors["t-bg"], colors["t-ans"], "#FFF"}, lang


@pytest.mark.parametrize("tape", TAPES, ids=lambda p: p.name)
def test_the_recordings_take_the_palette(tape):
    assert len(TAPES) == 2
    colors = themes()["light"]
    text = tape.read_text(encoding="utf-8")
    margin = re.search(r'^Set MarginFill "([^"]+)"$', text, flags=re.M).group(1)
    theme = json.loads(re.search(r"^Set Theme (\{.*\})$", text, flags=re.M).group(1))
    assert margin.upper() == colors["bg"]
    assert theme["background"].upper() == colors["t-bg"]
    assert theme["foreground"].upper() == colors["t-fg"]


def test_the_preview_template_takes_the_light_tokens():
    colors = themes()["light"]
    template = TEMPLATE.read_text(encoding="utf-8")
    root = block(template, ":root")
    own = tokens(root)
    assert len(own) >= 10
    assert {name: colors[name] for name in own} == own
    # Every colour of the template is one of those tokens: nothing is written past them.
    outside = template.replace(root, "")
    assert HEX.findall(outside) == []


OLD_COLORS = [
    "#E3EAE4", "#F1F5F1", "#1B2F26", "#485D53", "#46662A", "#BCCABF", "#132620", "#1B3129", "#D3E0D7", "#F1F7F3",
    "#8BA597", "#0F1914", "#16231C", "#DCE6DF", "#9FB2A7", "#A3C57A", "#2B3C33", "#09130F", "#10201A", "#2B4A3E",
    "rgba(19, 38, 32", "rgba(227, 234, 228",
]


def palette_files() -> list[Path]:
    files = [p for p in sorted(SITE.rglob("*")) if p.suffix in {".html", ".css", ".js", ".svg", ".json"}]
    files += [p for p in sorted((REPO / "scripts").rglob("*")) if p.is_file() and p.suffix in {".html", ".py", ".sh"}]
    files += TAPES
    return files


def test_the_green_palette_is_gone():
    files = palette_files()
    assert {CSS, SITE / "favicon.svg", TEMPLATE, *PAGES.values(), *TAPES} <= set(files)
    found = []
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        found += [(path.relative_to(REPO).as_posix(), old) for old in OLD_COLORS if old.lower() in text]
    assert found == []
