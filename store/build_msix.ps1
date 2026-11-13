<#
.SYNOPSIS
  Pack one architecture of Keyware into an MSIX from a PyInstaller Keyware.exe.

.DESCRIPTION
  Runs on Windows with the Windows 10/11 SDK (MakeAppx.exe and MakePri.exe).
  GitHub windows-latest and windows-11-arm already have them. The tool used is
  the one that matches the machine doing the packing. It can pack either an
  x64 or an ARM64 package.

  This script never creates a certificate. Pass -PfxPath only when you already
  have a certificate whose subject equals -Publisher. The Microsoft Store
  re-signs uploads, so a Store package is left unsigned.

  -Prototype builds a real unsigned package whose publisher is the Windows
  unsigned-package marker, not a Partner Center publisher. The file name
  starts with NOT-STORE-READY. Do not upload that file.

.EXAMPLE
  # After the four values have been copied from Partner Center > Product identity:
  .\store\build_msix.ps1 -Architecture x64 -SkipExeBuild `
      -IdentityName "12345Example.Keyware" `
      -Publisher "CN=1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D" `
      -PublisherDisplayName "Example" -DisplayName "Keyware"

.EXAMPLE
  # Labelled prototype. Not a Store upload.
  .\store\build_msix.ps1 -Prototype -Architecture arm64 -SkipExeBuild
#>
[CmdletBinding()]
param(
  [ValidateSet("x64", "arm64")]
  [string]$Architecture = "x64",
  [string]$IdentityName,
  [string]$Publisher,
  [string]$PublisherDisplayName,
  [string]$DisplayName,
  [string]$Version,
  [switch]$Prototype,
  [switch]$SkipExeBuild,
  [string]$ExePath,
  [string]$PfxPath,
  [SecureString]$PfxPassword
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Out = Join-Path $Root "dist\msix"
$Work = Join-Path $Out "work-$Architecture"
$Layout = Join-Path $Work "layout"

# Windows requires this exact OID as the last field of Publisher on an unsigned package.
# It is not a Partner Center publisher. See:
# https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/package-identity-overview
$UnsignedOid = "OID.2.25.311729368913984317654407730594956997722=1"
$PrototypeIdentityName = "Keyware.NotForStore"
$PrototypePublisher = "CN=Keyware Prototype Not For Store, $UnsignedOid"
$PrototypePublisherDisplayName = "Keyware prototype (not for Store)"
$PrototypeDisplayName = "Keyware prototype (not for Store)"

if ($Prototype -and $PfxPath) {
  throw "-Prototype packages stay unsigned. Do not pass -PfxPath."
}
if ($Prototype -and ($IdentityName -or $Publisher -or $PublisherDisplayName -or $DisplayName)) {
  throw "Pass -Prototype or the Partner Center identity values, not both."
}
if ($Prototype) {
  $IdentityName = $PrototypeIdentityName
  $Publisher = $PrototypePublisher
  $PublisherDisplayName = $PrototypePublisherDisplayName
  $DisplayName = $PrototypeDisplayName
}

foreach ($pair in @(
    @("IdentityName", $IdentityName),
    @("Publisher", $Publisher),
    @("PublisherDisplayName", $PublisherDisplayName),
    @("DisplayName", $DisplayName)
  )) {
  if (-not $pair[1]) {
    throw "-$($pair[0]) is required. Copy it from Partner Center > Product identity, or pass -Prototype for a package that must not be uploaded."
  }
}
if ($Publisher -notmatch '^CN=') {
  throw "-Publisher must be the full Package/Identity/Publisher value from Partner Center, starting with CN=."
}
if ($IdentityName -notmatch '^[A-Za-z0-9.\-]{3,50}$') {
  throw "-IdentityName '$IdentityName' does not look like a Package/Identity/Name."
}
if (-not $Prototype -and $Publisher.Contains($UnsignedOid)) {
  throw "-Publisher contains the unsigned-package marker. That is not a Partner Center publisher. Use -Prototype for that package."
}
if (-not $Prototype -and $IdentityName -eq $PrototypeIdentityName) {
  throw "-IdentityName is the prototype name. Pass the Package/Identity/Name from Partner Center."
}

if (-not $Version) {
  $line = Select-String -Path (Join-Path $Root "engine\__init__.py") -Pattern '__version__\s*=\s*"([0-9.]+)"'
  if (-not $line) { throw "Could not read __version__ from engine\__init__.py." }
  $Version = $line.Matches[0].Groups[1].Value
}
$parts = @($Version.Split(".") | Where-Object { $_ -ne "" })
while ($parts.Count -lt 3) { $parts += "0" }
foreach ($part in $parts[0..2]) {
  if ($part -notmatch '^[0-9]+$') { throw "Version '$Version' is not numeric." }
}
# Store rule: the fourth part is reserved and must be 0.
$Version4 = "{0}.{1}.{2}.0" -f $parts[0], $parts[1], $parts[2]

function Find-SdkTool([string]$name) {
  $hostArch = switch ($env:PROCESSOR_ARCHITECTURE) {
    "ARM64" { "arm64" }
    "X86" { "x86" }
    default { "x64" }
  }
  $roots = @()
  foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
    if (-not $base) { continue }
    $kits = Join-Path $base "Windows Kits\10\bin"
    if (Test-Path $kits) { $roots += $kits }
  }
  if (-not $roots) { throw "Windows SDK not found. Install the Windows 10/11 SDK (MakeAppx and MakePri)." }
  $hit = Get-ChildItem -Path $roots -Filter $name -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\$hostArch\\" } |
    Sort-Object { try { [version]$_.Directory.Parent.Name } catch { [version]"0.0" } } -Descending |
    Select-Object -First 1
  if (-not $hit) { throw "$name for $hostArch was not found under the Windows SDK." }
  return $hit.FullName
}

$MakeAppx = Find-SdkTool "makeappx.exe"
$MakePri = Find-SdkTool "makepri.exe"
Write-Host "MakeAppx: $MakeAppx"
Write-Host "Package architecture: $Architecture"

if (-not $ExePath) {
  $ExePath = Join-Path $Root "dist\Keyware.exe"
} elseif (-not [System.IO.Path]::IsPathRooted($ExePath)) {
  $ExePath = Join-Path $Root $ExePath
}
if (-not $SkipExeBuild) {
  Push-Location $Root
  try {
    python -m pip install -r requirements-store.txt
    if ($LASTEXITCODE) { throw "pip install failed" }
    $env:KEYWARE_MSIX = "1"
    python build.py
    if ($LASTEXITCODE) { throw "PyInstaller build failed" }
  } finally {
    Remove-Item Env:KEYWARE_MSIX -ErrorAction SilentlyContinue
    Pop-Location
  }
}
if (-not (Test-Path $ExePath)) { throw "Keyware.exe is missing: $ExePath" }

if (Test-Path $Work) { Remove-Item $Work -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $Layout "Assets") | Out-Null
Copy-Item $ExePath (Join-Path $Layout "Keyware.exe")
Copy-Item (Join-Path $PSScriptRoot "Assets\*.png") (Join-Path $Layout "Assets")
Copy-Item (Join-Path $Root "LICENSE") $Layout

$manifest = Get-Content (Join-Path $PSScriptRoot "AppxManifest.xml") -Raw -Encoding UTF8
$esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
$manifest = $manifest.Replace("@@IDENTITY_NAME@@", (& $esc $IdentityName)).
  Replace("@@IDENTITY_PUBLISHER@@", (& $esc $Publisher)).
  Replace("@@PUBLISHER_DISPLAY_NAME@@", (& $esc $PublisherDisplayName)).
  Replace("@@DISPLAY_NAME@@", (& $esc $DisplayName)).
  Replace("@@VERSION@@", $Version4).
  Replace("@@ARCH@@", $Architecture)
if ($manifest -match '@@[A-Z_]+@@') { throw "A placeholder was left in AppxManifest.xml: $($Matches[0])" }
[System.IO.File]::WriteAllText((Join-Path $Layout "AppxManifest.xml"), $manifest, (New-Object System.Text.UTF8Encoding($false)))

$PriConfig = Join-Path $Work "priconfig.xml"
& $MakePri createconfig /cf $PriConfig /dq en-US /pv 10.0.0 /o
if ($LASTEXITCODE) { throw "makepri createconfig failed" }
& $MakePri new /pr $Layout /cf $PriConfig /mn (Join-Path $Layout "AppxManifest.xml") /of (Join-Path $Layout "resources.pri") /o
if ($LASTEXITCODE) { throw "makepri new failed" }

$leaf = if ($Prototype) { "NOT-STORE-READY-Keyware-$Version4-$Architecture.msix" } else { "Keyware-$Version4-$Architecture.msix" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Msix = Join-Path $Out $leaf
& $MakeAppx pack /d $Layout /p $Msix /o
if ($LASTEXITCODE) { throw "makeappx pack failed" }

if ($PfxPath) {
  $SignTool = Find-SdkTool "signtool.exe"
  $plain = if ($PfxPassword) { [System.Net.NetworkCredential]::new("", $PfxPassword).Password } else { "" }
  & $SignTool sign /fd SHA256 /f $PfxPath /p $plain $Msix | Out-Host
  if ($LASTEXITCODE) { throw "signtool failed (the certificate subject must equal -Publisher)" }
}

$expect = if ($Prototype) { "prototype" } else { "store" }
$validate = @(
  (Join-Path $Root "store\validate_msix.py"),
  $Msix,
  "--expect", $expect,
  "--arch", $Architecture,
  "--version", $Version4
)
$IdentityFile = Join-Path $PSScriptRoot "partner-identity.json"
if (-not $Prototype -and (Test-Path $IdentityFile)) {
  $validate += @("--identity", $IdentityFile)
}
python @validate
if ($LASTEXITCODE) { throw "package validation failed" }

if ($Prototype) {
  @(
    "NOT FOR STORE UPLOAD.",
    "This unsigned package was built so the MSIX layout, manifest, architecture and executable can be checked.",
    "Publisher is the Windows unsigned-package marker ($UnsignedOid), not Package/Identity/Publisher from Partner Center.",
    "Partner Center will reject it. Do not rename it and upload it.",
    "Architecture: $Architecture",
    "Version: $Version4"
  ) | Set-Content -Path ($Msix + ".WHY.txt") -Encoding ascii
}

$hash = (Get-FileHash $Msix -Algorithm SHA256).Hash
Set-Content -Path "$Msix.sha256" -Value $hash -Encoding ascii
Write-Host "Built $Msix  version $Version4  architecture $Architecture  sha256 $hash"
