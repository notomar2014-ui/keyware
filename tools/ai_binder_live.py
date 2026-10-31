"""Live check of the Describe-it binder against the real free-tier models.

Calls the model and validates the reply. It never starts a hook, never opens
an app or URL, never sends keys, and never saves a config. Keys are read in
code and never printed.

    python tools/ai_binder_live.py ["sentence" ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assist.ai_binder import DEFAULT_PROVIDERS, AIBinderError, DescribeBinder, Rejected, batch_flags, load_keys  # noqa: E402
from engine.models import default_config  # noqa: E402

SENTENCES = [
    "F2 opens YouTube and searches minecraft",
    "Ctrl+Q types my email then presses enter",
    "in gaming, hold R for 1.5 seconds to press 3, right click, wait a bit, then press 1",
    "hold R to autoclick left at 15 cps in gaming",
    "F6 toggles right click autoclicker 20 cps",
    "double tap F8 to open notepad",
    "Ctrl+Win+Shift+R opens google",
    "F9 opens powershell and deletes my temp folder",
    "F6 turns on work mode: opens VS Code, Chrome with Google Classroom, and mutes the sound",
    'F5 opens Chrome and plays "du bist gueng" on Spotify',
    "ctrl+alt+n opens notepad and types hello when it's open",
    "F7 locks my pc and F8 takes a screenshot",
    "F10 opens my Downloads folder",
    "F12 shuts down my computer",
    "what's the weather in London tomorrow?",
    "F9 opens YouTube, F10 opens Gmail, and F9 also plays lofi on YouTube",
]


def main() -> int:
    sentences = sys.argv[1:] or SENTENCES
    only = None
    if sentences and sentences[0].startswith("--model="):
        only = sentences.pop(0).split("=", 1)[1]
        sentences = sentences or SENTENCES
    providers = tuple(p for p in DEFAULT_PROVIDERS if only is None or p.model == only)
    config = default_config()
    binder = DescribeBinder(providers=providers, keys=load_keys())
    for sentence in sentences:
        print(f"\n> {sentence}")
        try:
            items = binder.describe_batch(sentence, config, "Global")
        except AIBinderError as exc:
            kind = "NOT A BINDING" if exc.not_binding else "REJECTED/ERROR"
            print(f"  {kind}: {exc}")
            continue
        flags = batch_flags(items, config, "global")
        for number, (item, flag) in enumerate(zip(items, flags), 1):
            if isinstance(item, Rejected):
                print(f"  #{number} REJECTED  {item.label}: {item.error}")
                continue
            print(f"  #{number} model={item.model}  {item.seconds}s")
            for name, value in item.summary_lines(config.leader_key):
                print(f"  {name:8} " + str(value).replace("\n", "\n           "))
            for note in item.notes:
                print(f"  note     {note}")
            print("  json     " + json.dumps(item.binding.to_dict(), ensure_ascii=False))
            if flag:
                print(f"  FLAG     {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
