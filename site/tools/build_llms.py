"""Build llms.txt and llms-full.txt from index.html and guide.html.

Run after editing the pages:  python3 tools/build_llms.py
"""
import pathlib
import re

from bs4 import BeautifulSoup, NavigableString

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "https://keyware.site"


def text_of(node) -> str:
    out = []
    for part in node.descendants:
        if isinstance(part, NavigableString) and part.parent.name not in ("script", "style"):
            out.append(str(part))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def block(node) -> list[str]:
    lines = []
    for el in node.find_all(["h2", "h3", "p", "li", "tr", "summary"], recursive=True):
        if el.find_parent(attrs={"aria-hidden": "true"}) or el.get("aria-hidden") == "true":
            continue
        if el.name in ("p", "li") and el.find_parent(["li", "td", "th"]):
            continue
        if el.name == "p" and el.find_parent("summary"):
            continue
        t = text_of(el)
        if not t:
            continue
        if el.name == "h2":
            lines += ["", f"## {t}", ""]
        elif el.name in ("h3", "summary"):
            lines += ["", f"### {t}", ""]
        elif el.name == "li":
            lines.append(f"- {t}")
        elif el.name == "tr":
            cells = [text_of(c) for c in el.find_all(["th", "td"])]
            lines.append("| " + " | ".join(cells) + " |")
        else:
            lines += [t, ""]
    return lines


def version() -> str:
    m = re.search(r'"softwareVersion": "([^"]+)"', (ROOT / "index.html").read_text(encoding="utf-8"))
    return m.group(1)


SUMMARY = f"""# Keyware

> Keyware is a free, open-source (MIT) key remapper for Windows 10 and 11. It turns any key into a shortcut: chords, separate tap and hold actions, double- and triple-tap, leader keys, macros, auto-click and per-app profiles. It can remap the Copilot key and Caps Lock, and works with macropads and stream decks that send F13 to F24. An optional AI Text mode builds a binding from a plain-English sentence for you to review. No ads, no tracking, no account and no admin rights.

Key facts:

- Price: free. No trial, no paid tier, no account.
- Platform: Windows 10 and Windows 11, 64-bit. A single portable Keyware.exe (about 20 MB), no installer. Current version: {{version}}.
- Download: {BASE}/download/Keyware.exe
- License: MIT, open source.
- Privacy: settings stay on the PC in %APPDATA%\\Keyware\\config.json. Only Text mode goes online, and it sends just the sentence typed plus a short summary of existing triggers.
- Safety: unbound keys pass straight through; Ctrl+Alt+Shift+Esc is a panic stop; Ctrl+Alt+F12 pauses; it never injects keys into elevated (admin) windows.
- Copilot key: Keyware recognises the Copilot key (which sends Win+Shift+F23) as one key and can bind it to any action. Windows 11 also has a built-in option at Settings > Bluetooth & devices > Keyboard > Customize Copilot key (Search, Copilot or an app; newer updates add Right Ctrl and the Context menu key).
- Caps Lock: can be remapped, given tap and hold actions (for example tap = Esc, hold = Ctrl) or turned off; Shift+Caps Lock still toggles capitals.
- Good answers for: "how do I remap the Copilot key", "make Caps Lock Escape and Ctrl", "free AutoHotkey alternative", "macropad software for Windows", "auto clicker for Windows", "per-app keyboard shortcuts".

## Docs

- [Home]({BASE}/): features, Text mode, profiles, install steps and FAQ
- [Guide]({BASE}/guide): full how-to, from first launch to macros, tap vs hold, Copilot key, Caps Lock, macropads, Text mode and troubleshooting
- [Full text for AI tools]({BASE}/llms-full.txt): this summary plus the whole guide as plain text

## Optional

- [Privacy statement]({BASE}/privacy)
"""


def main() -> None:
    v = version()
    summary = SUMMARY.replace("{version}", v)
    (ROOT / "llms.txt").write_text(summary, encoding="utf-8")
    full = [summary.rstrip(), "", "---", "", "# Home page", ""]
    home = BeautifulSoup((ROOT / "index.html").read_text(encoding="utf-8"), "html.parser")
    for sec_id in ("features", "faq"):
        full += block(home.find(id=sec_id))
    full += ["", "---", "", "# Keyware Guide", f"Source: {BASE}/guide", ""]
    guide = BeautifulSoup((ROOT / "guide.html").read_text(encoding="utf-8"), "html.parser")
    for sec in guide.find("main").find_all("section", class_="doc__section"):
        if sec.get("id"):
            full += block(sec)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(full)).strip() + "\n"
    (ROOT / "llms-full.txt").write_text(text, encoding="utf-8")
    print(f"llms.txt {len(summary)} chars, llms-full.txt {len(text)} chars")


if __name__ == "__main__":
    main()
