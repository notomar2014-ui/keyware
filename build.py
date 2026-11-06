"""Build a single Keyware.exe with PyInstaller.

PyInstaller cannot cross-compile. The exe matches the CPU of the Windows PC
you run this on: x64 Windows writes an x64 Keyware.exe, and Windows on ARM
writes a native ARM64 Keyware.exe. GitHub Actions does the same with
windows-latest (x64) and windows-11-arm (ARM64).

    pip install -r requirements-dev.txt
    python build.py

The executable is written to dist/Keyware.exe.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    spec = root / "keyware.spec"
    if not spec.exists():
        print("keyware.spec is missing.", file=sys.stderr)
        return 1
    if sys.platform != "win32":
        print("Keyware.exe has to be built on Windows.")
        print("PyInstaller cannot cross-compile an .exe from this machine.")
        print("On Windows, from this folder, run:  python build.py")
        return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Install the build tools first:  pip install -r requirements-dev.txt", file=sys.stderr)
        return 1
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)]
    print(f"Host: {sys.platform} {platform.machine()}")
    print("Running:", " ".join(command))
    subprocess.check_call(command, cwd=root)
    exe = root / "dist" / "Keyware.exe"
    if not exe.is_file():
        print("The build finished but dist/Keyware.exe was not produced.", file=sys.stderr)
        return 1
    print(f"Built {exe} ({exe.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
