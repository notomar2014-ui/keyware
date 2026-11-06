"""Name, hash and describe the Keyware.exe PyInstaller just built.

The build always writes dist/Keyware.exe. x64 keeps that name. The ARM64
artifact is copied to Keyware-arm64.exe so the two files cannot be mixed up.
The SHA-256 is of the file that is uploaded, uppercase hex plus a newline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from pe_machine import pe_machine

MACHINES = {"x64": 0x8664, "arm64": 0xAA64}


def package(dist: Path, arch: str, asset: str, version_file: Path) -> dict:
    if arch not in MACHINES:
        raise ValueError(f"unknown arch {arch}")
    built = dist / "Keyware.exe"
    dest = dist / asset
    data = built.read_bytes()
    machine = pe_machine(data)
    if machine != MACHINES[arch]:
        raise ValueError(f"{built} is machine 0x{machine:04X}, not {arch}")
    if dest.resolve() != built.resolve():
        dest.write_bytes(data)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest().upper()
    version = version_file.read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError("version file is empty")
    (dist / f"{asset}.sha256").write_text(digest + "\n", encoding="ascii")
    (dist / "version.txt").write_text(version + "\n", encoding="ascii")
    manifest = {
        "version": version,
        "arch": arch,
        "file": asset,
        "sha256": digest,
        "bytes": dest.stat().st_size,
    }
    (dist / "release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(dist: Path, arch: str, asset: str, source_version: str | None = None) -> dict:
    manifest = json.loads((dist / "release.json").read_text(encoding="utf-8"))
    if manifest.get("arch") != arch or manifest.get("file") != asset:
        raise ValueError(f"manifest does not describe {arch} {asset}")
    data = (dist / asset).read_bytes()
    digest = hashlib.sha256(data).hexdigest().upper()
    if digest != manifest.get("sha256"):
        raise ValueError("sha256 does not match the exe")
    if (dist / f"{asset}.sha256").read_text(encoding="ascii").strip() != digest:
        raise ValueError("sha256 file does not match the exe")
    if (dist / "version.txt").read_text(encoding="utf-8").strip() != manifest.get("version"):
        raise ValueError("version.txt does not match the manifest")
    if pe_machine(data) != MACHINES[arch]:
        raise ValueError(f"{asset} is not a {arch} PE")
    if manifest.get("bytes") != len(data):
        raise ValueError("byte size does not match the exe")
    if source_version is not None and manifest.get("version") != source_version:
        raise ValueError(f"exe reports {manifest.get('version')}, source is {source_version}")
    return manifest


def source_version(root: Path) -> str:
    text = (root / "engine" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ValueError("engine/__init__.py has no __version__")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--version-file", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        source = source_version(args.source_root) if args.source_root else None
        manifest = verify(args.dist, args.arch, args.asset, source)
    else:
        if args.version_file is None:
            print("--version-file is required when packaging", file=sys.stderr)
            return 2
        manifest = package(args.dist, args.arch, args.asset, args.version_file)
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
