"""Headless Chromium launch with ApplyPeace's user-space runtime (no root needed).

`scripts/install-browser-libs.sh` puts Chromium's shared libraries and fonts in
the runtime directory; the browser binary itself is installed there with
`PLAYWRIGHT_BROWSERS_PATH=<runtime>/ms-playwright playwright install chromium-headless-shell`.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from .config import runtime_dir


def browser_env() -> dict[str, str]:
    rt = runtime_dir()
    env = {}
    if (rt / "ms-playwright").exists():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(rt / "ms-playwright")
    if (rt / "lib").exists():
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [str(rt / "lib"), os.environ.get("LD_LIBRARY_PATH")]))
    if (rt / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(rt / "fonts.conf")
    return env


@contextmanager
def launch(headless: bool = True):
    """Yield a Playwright Browser using the runtime libraries."""
    os.environ.update(browser_env())  # Playwright reads these when it starts the driver
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            yield browser
        finally:
            browser.close()


def check_browser() -> tuple[bool, str]:
    try:
        with launch() as browser:
            page = browser.new_page()
            page.set_content("<p>ok</p>")
            text = page.inner_text("p")
        return (text == "ok", "headless Chromium works" if text == "ok" else "Chromium renders no text (fonts missing?)")
    except Exception as exc:  # report any launch failure as a doctor finding
        return False, f"cannot launch Chromium: {str(exc).splitlines()[0]} (run scripts/install-browser-libs.sh)"
