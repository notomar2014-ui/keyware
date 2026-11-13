"""Photograph the live keyware.site pages used for Store screenshots.

Needs Chrome and Playwright. Frames them with store/make_screenshots.py:

  python3 store/capture_site.py /tmp/kw-site
  python3 -c "import store.make_screenshots as m; m.frame_site('/tmp/kw-site', 'site/assets/store')"
"""
from __future__ import annotations

import sys
from pathlib import Path

CHROME = "/usr/local/bin/google-chrome"
PAGES = (
    ("https://keyware.site/", "hero", None),
    ("https://keyware.site/", "features", "#features"),
    ("https://keyware.site/", "gallery", "#screenshots"),
    ("https://keyware.site/guide", "guide", None),
    ("https://keyware.site/whats-new", "whats-new", None),
)


def main(out_dir: str) -> None:
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=CHROME,
            args=["--hide-scrollbars", "--disable-dev-shm-usage"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 860}, device_scale_factor=2)
        page.emulate_media(reduced_motion="reduce")
        current = ""
        for url, name, anchor in PAGES:
            if url != current:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(400)
                current = url
            if anchor:
                page.evaluate(
                    """(sel) => {
                      const el = document.querySelector(sel);
                      const y = el.getBoundingClientRect().top + window.scrollY - 76;
                      window.scrollTo(0, y);
                    }""",
                    anchor,
                )
                page.wait_for_timeout(400)
            path = out / f"{name}.png"
            page.screenshot(path=str(path))
            print(path)
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "site-captures")
