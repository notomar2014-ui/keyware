"""The Store package tooling must not invent a Partner Center publisher."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from store.validate_msix import (
    PROTOTYPE_NAME,
    UNSIGNED_OID,
    PackageError,
    check_msix,
)
from tools.pe_machine import pe_machine

ROOT = Path(__file__).resolve().parents[1]


def test_template_keeps_partner_center_placeholders():
    text = (ROOT / "store" / "AppxManifest.xml").read_text(encoding="utf-8")
    for token in (
        "@@IDENTITY_NAME@@",
        "@@IDENTITY_PUBLISHER@@",
        "@@PUBLISHER_DISPLAY_NAME@@",
        "@@DISPLAY_NAME@@",
        "@@VERSION@@",
        "@@ARCH@@",
    ):
        assert token in text
    assert "CN=Keyware" not in text
    assert UNSIGNED_OID not in text


def test_prototype_identity_is_the_documented_unsigned_marker():
    script = (ROOT / "store" / "build_msix.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "msix.yml").read_text(encoding="utf-8")
    assert UNSIGNED_OID in script
    assert PROTOTYPE_NAME in script
    assert "NOT-STORE-READY" in script
    assert "New-SelfSignedCertificate" not in script
    assert "New-SelfSignedCertificate" not in workflow
    assert "gh release" not in workflow
    assert "contents: read" in workflow
    assert "windows-11-arm" in workflow
    assert "windows-latest" in workflow


def _pe(machine: int) -> bytes:
    data = bytearray(0x200)
    data[0:2] = b"MZ"
    pe = 0x80
    data[0x3C:0x40] = pe.to_bytes(4, "little")
    data[pe:pe + 4] = b"PE\0\0"
    data[pe + 4:pe + 6] = machine.to_bytes(2, "little")
    return bytes(data)


def _manifest(arch: str, publisher: str, name: str = PROTOTYPE_NAME) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
         xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
  <Identity Name="{name}" Publisher="{publisher}" Version="1.3.8.0" ProcessorArchitecture="{arch}" />
  <Properties>
    <DisplayName>Keyware prototype (not for Store)</DisplayName>
    <PublisherDisplayName>Keyware prototype (not for Store)</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="Keyware" Executable="Keyware.exe" EntryPoint="Windows.FullTrustApplication">
      <Extensions>
        <desktop:Extension Category="windows.startupTask" Executable="Keyware.exe"
                           EntryPoint="Windows.FullTrustApplication" uap10:Parameters="--background">
          <desktop:StartupTask TaskId="KeywareStartup" Enabled="true" DisplayName="Keyware" />
        </desktop:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <Capability Name="internetClient" />
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""


def _package(tmp_path: Path, arch: str, publisher: str, filename: str) -> Path:
    machine = {"x64": 0x8664, "arm64": 0xAA64}[arch]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("AppxManifest.xml", _manifest(arch, publisher))
        zf.writestr("AppxBlockMap.xml", '<BlockMap HashMethod="http://www.w3.org/2001/04/xmlenc#sha256" />')
        zf.writestr("[Content_Types].xml", "<Types />")
        zf.writestr("resources.pri", b"pri")
        zf.writestr("Keyware.exe", _pe(machine))
    path = tmp_path / filename
    path.write_bytes(buffer.getvalue())
    return path


def test_validator_accepts_a_labelled_prototype(tmp_path):
    publisher = f"CN=Keyware Prototype Not For Store, {UNSIGNED_OID}"
    path = _package(tmp_path, "arm64", publisher, "NOT-STORE-READY-Keyware-1.3.8.0-arm64.msix")
    info = check_msix(path, expect="prototype", arch="arm64", version="1.3.8.0")
    assert info["arch"] == "arm64"
    assert pe_machine(_pe(0xAA64)) == 0xAA64


def test_validator_rejects_a_prototype_presented_as_a_store_upload(tmp_path):
    publisher = f"CN=Keyware Prototype Not For Store, {UNSIGNED_OID}"
    path = _package(tmp_path, "x64", publisher, "Keyware-1.3.8.0-x64.msix")
    with pytest.raises(PackageError):
        check_msix(path, expect="store", arch="x64", version="1.3.8.0")


def test_partner_identity_matches_the_supplied_values(tmp_path):
    import json

    from store.validate_msix import check_msix

    saved = json.loads((ROOT / "store" / "partner-identity.json").read_text(encoding="utf-8"))
    assert saved["identity_name"] == "ToolsForYourComputer.Keyware-KeyRemapperforWindows"
    assert saved["publisher"] == "CN=AE8C2F59-F803-4F8E-A325-071CEDD9C69E"
    assert saved["publisher_display_name"] == "Tools For Your Computer"
    assert saved["display_name"] == "Keyware - Key Remapper for Windows"
    assert saved["store_id"] == "9NGDP5BL0105"
    assert saved["publisher_display_name"] == saved["publisher_display_name"].strip()
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
         xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
  <Identity Name="{saved['identity_name']}" Publisher="{saved['publisher']}" Version="1.3.8.0" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>{saved['display_name']}</DisplayName>
    <PublisherDisplayName>{saved['publisher_display_name']}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="Keyware" Executable="Keyware.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="{saved['display_name']}" Description="Keyware" BackgroundColor="transparent"
        Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png" />
      <Extensions>
        <desktop:Extension Category="windows.startupTask" Executable="Keyware.exe"
                           EntryPoint="Windows.FullTrustApplication" uap10:Parameters="--background">
          <desktop:StartupTask TaskId="KeywareStartup" Enabled="true" DisplayName="Keyware" />
        </desktop:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <Capability Name="internetClient" />
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("AppxManifest.xml", manifest)
        zf.writestr("AppxBlockMap.xml", '<BlockMap HashMethod="http://www.w3.org/2001/04/xmlenc#sha256" />')
        zf.writestr("[Content_Types].xml", "<Types />")
        zf.writestr("resources.pri", b"pri")
        zf.writestr("Keyware.exe", _pe(0x8664))
    path = tmp_path / "Keyware-1.3.8.0-x64.msix"
    path.write_bytes(buffer.getvalue())
    info = check_msix(path, expect="store", arch="x64", version="1.3.8.0", partner=saved)
    assert info["name"] == saved["identity_name"]
    saved["publisher"] = "CN=00000000-0000-0000-0000-000000000000"
    with pytest.raises(PackageError, match="Publisher"):
        check_msix(path, expect="store", arch="x64", version="1.3.8.0", partner=saved)


def test_validator_rejects_an_exe_for_the_other_architecture(tmp_path):
    publisher = f"CN=Keyware Prototype Not For Store, {UNSIGNED_OID}"
    path = _package(tmp_path, "x64", publisher, "NOT-STORE-READY-Keyware-1.3.8.0-x64.msix")
    blob = io.BytesIO()
    with zipfile.ZipFile(path, "r") as src, zipfile.ZipFile(blob, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "Keyware.exe":
                data = _pe(0xAA64)
            dst.writestr(item, data)
    labelled = tmp_path / "NOT-STORE-READY-broken.msix"
    labelled.write_bytes(blob.getvalue())
    with pytest.raises(PackageError, match="machine"):
        check_msix(labelled, expect="prototype", arch="x64", version="1.3.8.0")
