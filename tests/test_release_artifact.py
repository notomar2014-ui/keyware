"""Packaging helpers for the x64 and ARM64 exes. No real exe is built here."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from arm_notes import with_arm_hash  # noqa: E402
from package_exe import package, verify  # noqa: E402
from pe_machine import pe_machine  # noqa: E402


def _pe(machine: int) -> bytes:
    data = bytearray(b"MZ" + b"\0" * 0x80)
    data[0x3C:0x40] = (0x40).to_bytes(4, "little")
    data[0x40:0x44] = b"PE\0\0"
    data[0x44:0x46] = machine.to_bytes(2, "little")
    return bytes(data)


def test_pe_machine_reads_the_header():
    assert pe_machine(_pe(0x8664)) == 0x8664
    assert pe_machine(_pe(0xAA64)) == 0xAA64


def test_package_names_arm64_and_records_its_own_hash(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "Keyware.exe").write_bytes(_pe(0xAA64))
    version = tmp_path / "version.txt"
    version.write_text("1.3.7\n", encoding="utf-8")
    manifest = package(dist, "arm64", "Keyware-arm64.exe", version)
    assert manifest["arch"] == "arm64"
    assert manifest["file"] == "Keyware-arm64.exe"
    assert manifest["version"] == "1.3.7"
    assert manifest["sha256"] != ""
    assert (dist / "Keyware-arm64.exe").read_bytes() == (dist / "Keyware.exe").read_bytes()
    assert verify(dist, "arm64", "Keyware-arm64.exe", "1.3.7")["sha256"] == manifest["sha256"]


def test_package_refuses_an_x64_exe_labeled_arm64(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "Keyware.exe").write_bytes(_pe(0x8664))
    version = tmp_path / "version.txt"
    version.write_text("1.3.7\n", encoding="utf-8")
    try:
        package(dist, "arm64", "Keyware-arm64.exe", version)
    except ValueError as exc:
        assert "not arm64" in str(exc)
    else:
        raise AssertionError("x64 bytes were accepted as ARM64")


def test_arm_release_notes_replace_the_arm_line_only():
    body = "SHA-256 (Keyware.exe): `AAAA`\n"
    digest = "B" * 64
    updated = with_arm_hash(body, digest)
    assert "SHA-256 (Keyware.exe): `AAAA`" in updated
    assert f"SHA-256 (Keyware-arm64.exe): `{digest}`" in updated
    again = with_arm_hash(updated, "C" * 64)
    assert again.count("Keyware-arm64.exe") == 1
    assert f"SHA-256 (Keyware-arm64.exe): `{'C' * 64}`" in again
    assert "Native ARM64 portable build" in again
    noted = with_arm_hash(
        "This build is 64-bit. There is no native ARM64 build.\nSHA-256 (Keyware.exe): `AAAA`\n",
        "E" * 64,
    )
    assert "There is no native ARM64 build." not in noted
    assert "Keyware-arm64.exe is the native ARM64 build of this same version." in noted
    assert "SHA-256 (Keyware.exe): `AAAA`" in noted
    crlf = with_arm_hash(again.replace("\n", "\r\n"), "D" * 64)
    assert crlf.count("Keyware-arm64.exe") == 1
    assert f"SHA-256 (Keyware-arm64.exe): `{'D' * 64}`" in crlf


def test_release_json_roundtrip(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "Keyware.exe").write_bytes(_pe(0x8664))
    version = tmp_path / "version.txt"
    version.write_text("1.3.7\n", encoding="utf-8")
    package(dist, "x64", "Keyware.exe", version)
    saved = json.loads((dist / "release.json").read_text(encoding="utf-8"))
    assert saved["file"] == "Keyware.exe"
    assert verify(dist, "x64", "Keyware.exe") == saved
