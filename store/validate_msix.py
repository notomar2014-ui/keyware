"""Check a Keyware MSIX, bundle, or .msixupload without installing it.

The Microsoft Store identity is not invented here. A prototype package must
carry the Windows unsigned-package publisher marker and a file name that
says it is not for upload. A store package must carry some other CN=
publisher and must not carry that marker.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.pe_machine import pe_machine  # noqa: E402

UNSIGNED_OID = "OID.2.25.311729368913984317654407730594956997722=1"
PROTOTYPE_NAME = "Keyware.NotForStore"
MACHINE = {"x64": 0x8664, "arm64": 0xAA64}

NS = {
    "m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
    "uap3": "http://schemas.microsoft.com/appx/manifest/uap/windows10/3",
    "uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10",
    "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}


class PackageError(Exception):
    pass


def _names(zf: zipfile.ZipFile) -> list[str]:
    return [item.replace("\\", "/") for item in zf.namelist()]


def _read(zf: zipfile.ZipFile, name: str) -> bytes:
    wanted = name.lower()
    for item in zf.namelist():
        if item.replace("\\", "/").lower() == wanted:
            return zf.read(item)
    raise PackageError(f"missing {name}")


def _version4() -> str:
    text = (ROOT / "engine" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            raw = line.split("=", 1)[1].strip().strip('"').strip("'")
            parts = [part for part in raw.split(".") if part]
            while len(parts) < 3:
                parts.append("0")
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    raise PackageError("engine/__init__.py has no __version__")


def _check_identity(
    name: str,
    publisher: str,
    version: str,
    filename: str,
    expect: str,
    want_version: str,
    *,
    check_filename: bool,
) -> None:
    if "@@" in name or "@@" in publisher:
        raise PackageError("a placeholder is still in the identity")
    if version != want_version:
        raise PackageError(f"version is {version}, expected {want_version}")
    if not version.endswith(".0"):
        raise PackageError("the fourth version field must be 0")
    if not publisher.startswith("CN="):
        raise PackageError("Publisher does not start with CN=")
    unsigned = publisher.endswith(UNSIGNED_OID)
    if expect == "prototype":
        if name != PROTOTYPE_NAME:
            raise PackageError(f"prototype Name is {name}")
        if not unsigned:
            raise PackageError("prototype Publisher is missing the unsigned-package marker")
        if check_filename and "NOT-STORE-READY" not in Path(filename).name:
            raise PackageError("prototype file name must contain NOT-STORE-READY")
    elif expect == "store":
        if unsigned or name == PROTOTYPE_NAME:
            raise PackageError("store package still uses the prototype identity")
        if check_filename and "NOT-STORE-READY" in Path(filename).name:
            raise PackageError("store file name is labelled not ready")
    else:
        raise PackageError(f"unknown expect {expect}")


def check_msix(
    path: Path,
    *,
    expect: str,
    arch: str | None,
    version: str | None,
    check_filename: bool = True,
) -> dict:
    if not zipfile.is_zipfile(path):
        raise PackageError(f"{path.name} is not a zip package")
    want_version = version or _version4()
    with zipfile.ZipFile(path) as zf:
        names = _names(zf)
        if any(item.lower().endswith("appxsignature.p7x") for item in names):
            raise PackageError("package is signed; this build must stay unsigned")
        for required in ("AppxManifest.xml", "AppxBlockMap.xml", "[Content_Types].xml", "resources.pri", "Keyware.exe"):
            if not any(item.lower() == required.lower() for item in names):
                raise PackageError(f"missing {required}")
        blockmap = _read(zf, "AppxBlockMap.xml")
        if b"sha256" not in blockmap.lower():
            raise PackageError("block map does not use SHA-256")
        root = ET.fromstring(_read(zf, "AppxManifest.xml"))
        identity = root.find("m:Identity", NS)
        if identity is None:
            raise PackageError("Identity element is missing")
        got_arch = identity.attrib.get("ProcessorArchitecture", "")
        if arch and got_arch != arch:
            raise PackageError(f"architecture is {got_arch}, expected {arch}")
        if got_arch not in MACHINE:
            raise PackageError(f"architecture {got_arch} is not x64 or arm64")
        _check_identity(
            identity.attrib.get("Name", ""),
            identity.attrib.get("Publisher", ""),
            identity.attrib.get("Version", ""),
            path.name,
            expect,
            want_version,
            check_filename=check_filename,
        )
        props = root.find("m:Properties", NS)
        display = "" if props is None or props.find("m:DisplayName", NS) is None else props.find("m:DisplayName", NS).text or ""
        if expect == "prototype" and "not for Store" not in display:
            raise PackageError("prototype display name does not say it is not for Store")
        family = root.find("m:Dependencies/m:TargetDeviceFamily", NS)
        if family is None or family.attrib.get("Name") != "Windows.Desktop":
            raise PackageError("TargetDeviceFamily is not Windows.Desktop")
        if family.attrib.get("MinVersion") != "10.0.19041.0":
            raise PackageError("MinVersion must stay 10.0.19041.0 so startup parameters work")
        app = root.find("m:Applications/m:Application", NS)
        if app is None:
            raise PackageError("Application is missing")
        if app.attrib.get("Executable") != "Keyware.exe" or app.attrib.get("EntryPoint") != "Windows.FullTrustApplication":
            raise PackageError("the application is not the full-trust Keyware.exe")
        task = root.find("m:Applications/m:Application/m:Extensions/desktop:Extension/desktop:StartupTask", NS)
        if task is None or task.attrib.get("TaskId") != "KeywareStartup":
            raise PackageError("startup task KeywareStartup is missing")
        extension = root.find("m:Applications/m:Application/m:Extensions/desktop:Extension", NS)
        if extension is None or extension.attrib.get("{http://schemas.microsoft.com/appx/manifest/uap/windows10/10}Parameters") != "--background":
            raise PackageError("startup task does not pass --background")
        caps = {node.attrib.get("Name") for node in root.findall("m:Capabilities/m:Capability", NS)}
        restricted = {node.attrib.get("Name") for node in root.findall("m:Capabilities/rescap:Capability", NS)}
        if "internetClient" not in caps:
            raise PackageError("internetClient is not declared")
        if restricted != {"runFullTrust"}:
            raise PackageError(f"restricted capabilities are {sorted(restricted)}")
        machine = pe_machine(_read(zf, "Keyware.exe"))
        if machine != MACHINE[got_arch]:
            raise PackageError(f"Keyware.exe machine is 0x{machine:04X}, manifest says {got_arch}")
        return {
            "name": identity.attrib.get("Name", ""),
            "publisher": identity.attrib.get("Publisher", ""),
            "version": identity.attrib.get("Version", ""),
            "arch": got_arch,
        }


def check_bundle(path: Path, *, expect: str, version: str | None) -> dict:
    if not zipfile.is_zipfile(path):
        raise PackageError(f"{path.name} is not a zip bundle")
    want_version = version or _version4()
    with zipfile.ZipFile(path) as zf:
        names = _names(zf)
        if any(item.lower().endswith(".p7x") for item in names):
            raise PackageError("bundle is signed")
        manifest_name = next((item for item in names if item.lower().endswith("appxbundlemanifest.xml")), None)
        if manifest_name is None:
            raise PackageError("bundle manifest is missing")
        text = zf.read(manifest_name).decode("utf-8")
        if 'Architecture="x64"' not in text or 'Architecture="arm64"' not in text:
            raise PackageError("bundle does not contain both x64 and arm64")
        root = ET.fromstring(text)
        identity = None
        for node in root.iter():
            if node.tag.endswith("Identity"):
                identity = node
                break
        if identity is None:
            raise PackageError("bundle Identity is missing")
        _check_identity(
            identity.attrib.get("Name", ""),
            identity.attrib.get("Publisher", ""),
            identity.attrib.get("Version", ""),
            path.name,
            expect,
            want_version,
            check_filename=True,
        )
        nested = [item for item in names if item.lower().endswith(".msix")]
        if len(nested) < 2:
            raise PackageError("bundle does not contain two packages")
        seen = set()
        payloads = [(item, zf.read(item)) for item in nested]
    with tempfile.TemporaryDirectory() as tmp:
        for index, (item, data) in enumerate(payloads):
            inner_path = Path(tmp) / f"{index}.msix"
            inner_path.write_bytes(data)
            info = check_msix(
                inner_path,
                expect=expect,
                arch=None,
                version=want_version,
                check_filename=False,
            )
            seen.add(info["arch"])
        if seen != {"x64", "arm64"}:
            raise PackageError(f"nested architectures are {sorted(seen)}")
        return {"version": identity.attrib.get("Version", ""), "archs": sorted(seen)}


def check_upload(path: Path, *, expect: str, version: str | None) -> dict:
    if not zipfile.is_zipfile(path):
        raise PackageError(f"{path.name} is not a zip upload")
    if expect == "prototype" and "NOT-STORE-READY" not in path.name:
        raise PackageError("prototype upload name must contain NOT-STORE-READY")
    if expect == "store" and "NOT-STORE-READY" in path.name:
        raise PackageError("store upload name is labelled not ready")
    with zipfile.ZipFile(path) as zf:
        names = _names(zf)
        bundles = [item for item in names if item.lower().endswith(".msixbundle")]
        if len(bundles) != 1 or "/" in bundles[0]:
            raise PackageError(f".msixupload must contain one bundle at its root, found {names}")
        data = zf.read(bundles[0])
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / Path(bundles[0]).name
        bundle.write_bytes(data)
        return check_bundle(bundle, expect=expect, version=version)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    parser.add_argument("--expect", choices=("prototype", "store"), required=True)
    parser.add_argument("--arch", choices=("x64", "arm64"))
    parser.add_argument("--version")
    parser.add_argument("--bundle", action="store_true")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.package)
    try:
        if args.upload:
            info = check_upload(path, expect=args.expect, version=args.version)
        elif args.bundle:
            info = check_bundle(path, expect=args.expect, version=args.version)
        else:
            info = check_msix(path, expect=args.expect, arch=args.arch, version=args.version)
    except (PackageError, ValueError, OSError) as exc:
        print(f"{path.name}: {exc}", file=sys.stderr)
        return 1
    print(f"{path.name}: ok {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
