<#
.SYNOPSIS
  Build Keyware.msix (Microsoft Store package) from the PyInstaller Keyware.exe.

.DESCRIPTION
  Runs on Windows with the Windows 10/11 SDK installed (MakeAppx.exe, MakePri.exe, SignTool.exe).
  GitHub's windows-latest runner already has them. Steps:
    1. (unless -SkipExeBuild) pip install -r requirements-store.txt and build dist\Keyware.exe with
       KEYWARE_MSIX=1, which adds the WinRT StartupTask bindings the Store build needs.
    2. Stage dist\msix\layout: Keyware.exe + store\Assets + AppxManifest.xml with the placeholders
       filled in.
    3. MakePri: resources.pri so Windows picks the right scale/targetsize logo.
    4. MakeAppx pack: dist\msix\Keyware.msix
    5. (only with -PfxPath) sign it for local sideload testing. Store uploads must NOT be signed by
       you: Partner Center re-signs the package with Microsoft's certificate.

.EXAMPLE
  # Store upload (values from Partner Center > Product identity):
  .\store\build_msix.ps1 -IdentityName "12345Example.Keyware" `
      -Publisher "CN=1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D" -PublisherDisplayName "Example"

.EXAMPLE
  # Local test package, signed with a throwaway self-signed certificate:
  .\store\build_msix.ps1 -Test -PfxPath .\test.pfx -PfxPassword (Read-Host -AsSecureString)
#>
[CmdletBinding()]
param(
  [string]$IdentityName,
  [string]$Publisher,
  [string]$PublisherDisplayName,
  [string]$DisplayName = "Keyware",
  [string]$Version,
  [switch]$Test,                 # fill placeholders with CN=KeywareTest values (sideload testing / CI only)
  [switch]$SkipExeBuild,         # reuse an existing dist\Keyware.exe built with KEYWARE_MSIX=1
  [string]$PfxPath,
  [SecureString]$PfxPassword
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Out = Join-Path $Root "dist\msix"
$Layout = Join-Path $Out "layout"

if ($Test) {
  if (-not $IdentityName) { $IdentityName = "KeywareTest.Keyware" }
  if (-not $Publisher) { $Publisher = "CN=KeywareTest" }
  if (-not $PublisherDisplayName) { $PublisherDisplayName = "Keyware (test build)" }
}
foreach ($pair in @(@("IdentityName", $IdentityName), @("Publisher", $Publisher), @("PublisherDisplayName", $PublisherDisplayName))) {
  if (-not $pair[1]) { throw "-$($pair[0]) is required (copy it from Partner Center > Product identity), or pass -Test." }
}
if ($Publisher -notmatch '^CN=') { throw "-Publisher must be the full value from Partner Center, starting with CN=" }
if ($IdentityName -notmatch '^[A-Za-z0-9.\-]{3,50}$') { throw "-IdentityName '$IdentityName' does not look like a package name." }

if (-not $Version) {
  $line = Select-String -Path (Join-Path $Root "engine\__init__.py") -Pattern '__version__\s*=\s*"([0-9.]+)"'
  $Version = $line.Matches[0].Groups[1].Value
}
$parts = $Version.Split(".")
while ($parts.Count -lt 3) { $parts += "0" }
$Version4 = "{0}.{1}.{2}.0" -f $parts[0], $parts[1], $parts[2]   # Store rule: the fourth part must be 0

function Find-SdkTool([string]$name) {
  $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
  $hit = Get-ChildItem -Path $kits -Filter $name -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\' } |
    Sort-Object { [version]($_.Directory.Parent.Name -replace '[^0-9.]', '0') } -Descending |
    Select-Object -First 1
  if (-not $hit) { throw "$name not found. Install the Windows 10/11 SDK (it ships MakeAppx, MakePri and SignTool)." }
  return $hit.FullName
}
$MakeAppx = Find-SdkTool "makeappx.exe"
$MakePri = Find-SdkTool "makepri.exe"
Write-Host "MakeAppx: $MakeAppx"

# 1. Keyware.exe with the Store extras
$Exe = Join-Path $Root "dist\Keyware.exe"
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
if (-not (Test-Path $Exe)) { throw "dist\Keyware.exe is missing." }

# 2. Layout
if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $Layout "Assets") | Out-Null
Copy-Item $Exe $Layout
Copy-Item (Join-Path $PSScriptRoot "Assets\*.png") (Join-Path $Layout "Assets")
Copy-Item (Join-Path $Root "LICENSE") $Layout

$manifest = Get-Content (Join-Path $PSScriptRoot "AppxManifest.xml") -Raw -Encoding UTF8
$esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
$manifest = $manifest.Replace("@@IDENTITY_NAME@@", (& $esc $IdentityName)).
  Replace("@@IDENTITY_PUBLISHER@@", (& $esc $Publisher)).
  Replace("@@PUBLISHER_DISPLAY_NAME@@", (& $esc $PublisherDisplayName)).
  Replace("@@DISPLAY_NAME@@", (& $esc $DisplayName)).
  Replace("@@VERSION@@", $Version4)
if ($manifest -match '@@[A-Z_]+@@') { throw "A placeholder was left in AppxManifest.xml: $($Matches[0])" }
[System.IO.File]::WriteAllText((Join-Path $Layout "AppxManifest.xml"), $manifest, (New-Object System.Text.UTF8Encoding($false)))

# 3. resources.pri (scale-100..400 and targetsize-16..256 logos)
$PriConfig = Join-Path $Out "priconfig.xml"
& $MakePri createconfig /cf $PriConfig /dq en-US /pv 10.0.0 /o
if ($LASTEXITCODE) { throw "makepri createconfig failed" }
& $MakePri new /pr $Layout /cf $PriConfig /mn (Join-Path $Layout "AppxManifest.xml") /of (Join-Path $Layout "resources.pri") /o
if ($LASTEXITCODE) { throw "makepri new failed" }

# 4. Pack
$Msix = Join-Path $Out "Keyware.msix"
& $MakeAppx pack /d $Layout /p $Msix /o
if ($LASTEXITCODE) { throw "makeappx pack failed" }

# 5. Optional local signing (never for the Store upload)
if ($PfxPath) {
  $SignTool = Find-SdkTool "signtool.exe"
  $plain = if ($PfxPassword) { [System.Net.NetworkCredential]::new("", $PfxPassword).Password } else { "" }
  & $SignTool sign /fd SHA256 /f $PfxPath /p $plain $Msix | Out-Host
  if ($LASTEXITCODE) { throw "signtool failed (the certificate subject must equal -Publisher)" }
}

$hash = (Get-FileHash $Msix -Algorithm SHA256).Hash
Set-Content -Path "$Msix.sha256" -Value $hash -Encoding ascii
Write-Host "Built $Msix  version $Version4  sha256 $hash"
