"""Add or remove the temporary E2E profile (python.exe) in a Keyware config file.

  python tools/e2e_config.py add <config.json> [--enable]
  python tools/e2e_config.py remove <config.json>
Every action only types into the focused test window.
"""

from __future__ import annotations

import json
import sys

PROFILE_ID = "e2e-test"


def e2e_profile() -> dict:
    def b(bid, mode, keys, action, **extra):
        item = {"id": bid, "enabled": True, "mode": mode, "keys": keys, "hold_ms": 800, "taps": 2,
                "label": bid, "action": action}
        item.update(extra)
        return item

    text = lambda value: {"type": "type_text", "text": value}
    return {
        "id": PROFILE_ID, "name": "E2E test", "process_names": ["python.exe"], "enabled": True,
        "bindings": [
            b("e2e-chord", "chord", ["ctrl", "f20"], text("[CHORD]")),
            b("e2e-leader", "leader", ["y"], text("[LEADER]")),
            b("e2e-hold", "hold", ["p"], text("[HOLD]"), hold_ms=600),
            b("e2e-dance", "tap_dance", ["f8"], text("[DANCE]")),
            b("e2e-combo", "chord", ["ctrl", "f21"], {"type": "key_combo", "keys": ["shift", "x"]}),
            b("e2e-macro", "chord", ["ctrl", "f22"], {"type": "macro", "steps": [
                {"op": "key", "key": "m"}, {"op": "delay", "ms": 40}, {"op": "key", "key": "n"}]}),
        ],
    }


def main() -> int:
    op, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data["profiles"] = [p for p in data.get("profiles", []) if p.get("id") != PROFILE_ID]
    if op == "add":
        data["profiles"].append(e2e_profile())
        if "--enable" in sys.argv:
            data["enabled"] = True
        if "--leader-f1" in sys.argv:
            data["leader_key"] = "f1"
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    import os
    os.replace(path + ".tmp", path)
    print(f"{op} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
