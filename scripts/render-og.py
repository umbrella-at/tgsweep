# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.65.2"]
# ///
"""Renders the link previews from scripts/og.html: site/og.png (Russian) and site/og-en.png, 1200x630.

    uv run scripts/render-og.py

uv takes Playwright for this script only, the project gains no dependency. The page is drawn by the
installed Google Chrome; without it, the script falls back to Playwright's Chromium, which
`uv run --with playwright==1.65.2 playwright install chromium` downloads once.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "og.html"
SITE = HERE.parent / "site"
PICTURES = {"ru": SITE / "og.png", "en": SITE / "og-en.png"}

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(channel="chrome")
    except Exception:
        browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
    for lang, out in PICTURES.items():
        page.goto(TEMPLATE.as_uri())
        page.evaluate("lang => { document.documentElement.lang = lang; }", lang)
        page.evaluate("document.fonts.ready")
        fonts = page.evaluate("Array.from(document.fonts).map(f => f.family.replace(/\"/g, '') + ': ' + f.status)")
        if sorted(fonts) != ["Golos Text: loaded", "JetBrains Mono: loaded"]:
            raise SystemExit(f"{lang}: the site fonts did not load, the picture would use others: {fonts}")
        shown = page.evaluate("Array.from(document.querySelectorAll('[lang]')).filter(e => e !== document.documentElement && e.offsetParent !== null).map(e => e.lang)")
        if set(shown) != {lang}:
            raise SystemExit(f"{lang}: the picture would show other texts: {shown}")
        page.screenshot(path=str(out))
        print(f"{out} written, {out.stat().st_size // 1024} KB")
    browser.close()
