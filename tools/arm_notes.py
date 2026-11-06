"""Insert or replace the ARM64 SHA-256 line in a GitHub release body.

The x64 line is left as it is. The ARM64 line always names Keyware-arm64.exe
so the two hashes cannot be swapped.
"""

from __future__ import annotations

import re
import sys

LINE = re.compile(r"^SHA-256 \(Keyware-arm64\.exe\): `[0-9A-F]{64}`\r?$", re.M)
NOTE = "Native ARM64 portable build of this same version. The x64 Keyware.exe is unchanged."
NO_ARM = "There is no native ARM64 build."
NATIVE = "Keyware-arm64.exe is the native ARM64 build of this same version."


def with_arm_hash(body: str, sha256: str) -> str:
    digest = sha256.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{64}", digest):
        raise ValueError("sha256 must be 64 hex characters")
    line = f"SHA-256 (Keyware-arm64.exe): `{digest}`"
    body = body.replace(NO_ARM, NATIVE)
    if LINE.search(body):
        body = LINE.sub(line, body)
    else:
        body = body.rstrip() + "\n\n" + NOTE + "\n" + line + "\n"
    if NOTE not in body:
        body = body.rstrip() + "\n\n" + NOTE + "\n"
    return body if body.endswith("\n") else body + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python tools/arm_notes.py <sha256> < body.md", file=sys.stderr)
        return 2
    sys.stdout.write(with_arm_hash(sys.stdin.read(), argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
