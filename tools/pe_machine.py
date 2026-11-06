"""Read the Machine field of a Windows PE file.

PyInstaller builds for the CPU it is running on and cannot cross-compile.
CI uses this to refuse an exe whose header does not match the runner:
0x8664 is x64, 0xAA64 is ARM64.
"""

from __future__ import annotations

import sys
from pathlib import Path

MACHINE = {
    "8664": 0x8664,
    "AA64": 0xAA64,
    "14C": 0x014C,
}


def pe_machine(data: bytes) -> int:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("not a PE file")
    pe = int.from_bytes(data[0x3C:0x40], "little")
    if pe < 0 or pe + 6 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("bad PE header")
    return int.from_bytes(data[pe + 4:pe + 6], "little")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2].upper() not in MACHINE:
        print("usage: python tools/pe_machine.py <exe> <8664|AA64|14C>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    expect = argv[2].upper()
    got = pe_machine(path.read_bytes())
    want = MACHINE[expect]
    if got != want:
        print(f"{path}: machine 0x{got:04X}, expected 0x{want:04X} ({expect})", file=sys.stderr)
        return 1
    print(f"{path}: PE machine 0x{got:04X} ({expect})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
