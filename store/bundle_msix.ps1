<#
.SYNOPSIS
  Bundle the x64 and ARM64 Keyware packages and write the upload note.

.DESCRIPTION
  MakeAppx bundle needs a directory that contains only the .msix files.
  The .msixupload file is a zip of that bundle, which is the file shape
  Partner Center prefers. A prototype bundle is named NOT-STORE-READY and
  must not be uploaded.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$InputDir,
  [Parameter(Mandatory = $true)]
  [string]$OutDir
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Find-MakeAppx {
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
  $hit = Get-ChildItem -Path $roots -Filter "makeappx.exe" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\$hostArch\\" } |
    Sort-Object { try { [version]$_.Directory.Parent.Name } catch { [version]"0.0" } } -Descending |
    Select-Object -First 1
  if (-not $hit) { throw "makeappx.exe for $hostArch was not found." }
  return $hit.FullName
}

$packages = Get-ChildItem -Path $InputDir -Filter "*.msix" -Recurse | Where-Object { $_.Name -notlike "*.msixupload" }
if ($packages.Count -lt 2) { throw "Expected the x64 and ARM64 .msix files under $InputDir." }
$prototype = @($packages | Where-Object { $_.Name -like "NOT-STORE-READY-*" })
$store = @($packages | Where-Object { $_.Name -notlike "NOT-STORE-READY-*" })
if ($prototype.Count -gt 0 -and $store.Count -gt 0) {
  throw "Refusing to mix prototype packages with identity packages."
}
$expect = if ($prototype.Count -gt 0) { "prototype" } else { "store" }
$selected = if ($expect -eq "prototype") { $prototype } else { $store }

$stage = Join-Path $OutDir "bundle-input"
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
foreach ($package in $selected) {
  Copy-Item $package.FullName (Join-Path $stage $package.Name)
  python (Join-Path $Root "store\validate_msix.py") $package.FullName --expect $expect
  if ($LASTEXITCODE) { throw "validation failed for $($package.Name)" }
}

$version = $null
foreach ($package in $selected) {
  if ($package.Name -match 'Keyware-([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)-') {
    if ($version -and $version -ne $Matches[1]) { throw "Package versions differ." }
    $version = $Matches[1]
  }
}
if (-not $version) { throw "Could not read the package version from the file names." }

$bundleLeaf = if ($expect -eq "prototype") { "NOT-STORE-READY-Keyware-$version.msixbundle" } else { "Keyware-$version.msixbundle" }
$bundle = Join-Path $OutDir $bundleLeaf
$makeappx = Find-MakeAppx
Write-Host "MakeAppx: $makeappx"
& $makeappx bundle /d $stage /p $bundle /o
if ($LASTEXITCODE) { throw "makeappx bundle failed" }

python (Join-Path $Root "store\validate_msix.py") $bundle --expect $expect --bundle --version $version
if ($LASTEXITCODE) { throw "bundle validation failed" }

$uploadLeaf = [System.IO.Path]::ChangeExtension($bundleLeaf, ".msixupload")
$upload = Join-Path $OutDir $uploadLeaf
$uploadStage = Join-Path $OutDir "upload-input"
New-Item -ItemType Directory -Path $uploadStage | Out-Null
Copy-Item $bundle (Join-Path $uploadStage $bundleLeaf)
# .msixupload is a zip of the bundle. tar -a only picks zip when the name ends in .zip.
$zip = Join-Path $OutDir "upload.zip"
& tar.exe -a -c -f $zip -C $uploadStage $bundleLeaf
if ($LASTEXITCODE) { throw "could not create the upload zip" }
Move-Item $zip $upload
python (Join-Path $Root "store\validate_msix.py") $upload --expect $expect --upload --version $version
if ($LASTEXITCODE) { throw "msixupload validation failed" }

$note = Join-Path $OutDir "WHICH-FILE-TO-UPLOAD.txt"
if ($expect -eq "prototype") {
  @(
    "Keyware $version Microsoft Store packages",
    "",
    "Do not upload any file in this folder to Partner Center.",
    "",
    "These are real MSIX packages of the current Keyware build. Each one contains",
    "that version's executable, logos and manifest. They are not Store-ready.",
    "The publisher inside them is the Windows unsigned-package marker",
    "(OID.2.25.311729368913984317654407730594956997722=1), not the publisher",
    "from a Partner Center account. No such publisher is stored in this repository,",
    "and none was invented. Partner Center rejects a package whose",
    "Package/Identity/Publisher does not match Product identity character for character.",
    "",
    "Files:",
    "- NOT-STORE-READY-Keyware-$version-x64.msix",
    "    64-bit package. The Keyware.exe inside is a native x64 build.",
    "- NOT-STORE-READY-Keyware-$version-arm64.msix",
    "    ARM64 package. The Keyware.exe inside is a native ARM64 build.",
    "- NOT-STORE-READY-Keyware-$version.msixbundle",
    "    Both packages in one bundle. This is the right shape for one Store upload",
    "    once the identity is real.",
    "- NOT-STORE-READY-Keyware-$version.msixupload",
    "    A zip of that bundle. Partner Center prefers this file type for Windows 10",
    "    and Windows 11. This copy is still not upload-ready.",
    "",
    "None of these files is signed. No certificate was created for them.",
    "Windows will not install them by double-click. They are for inspection,",
    "and for showing exactly which file to upload after the identity exists.",
    "",
    "What has to be copied from Partner Center before a real upload exists",
    "Open the reserved app, then Product management, then Product identity.",
    "Copy these four values with no extra spaces:",
    "  1. Package/Identity/Name",
    "  2. Package/Identity/Publisher          (the whole value, starting with CN=)",
    "  3. Package/Properties/PublisherDisplayName",
    "  4. The reserved app name               (this becomes DisplayName)",
    "",
    "Re-run the GitHub Actions workflow named Microsoft Store MSIX and paste",
    "those four values into the form. Leave the form blank only when you want",
    "another labelled prototype.",
    "",
    "The file to upload from that later run is:",
    "  Keyware-$version.msixupload",
    "Partner Center also accepts Keyware-$version.msixbundle, or the separate",
    "x64 and ARM64 .msix files. Upload one of those, not a mix, and not any",
    "file whose name starts with NOT-STORE-READY.",
    "The Store re-signs an MSIX upload. A code-signing certificate is not",
    "required for the Store submission.",
    "",
    "The packages declare Windows.Desktop from 10.0.19041.0, internetClient,",
    "and the restricted runFullTrust capability that a desktop exe needs.",
    "They also declare a startup task. The low-level keyboard hook is not a",
    "manifest capability; certification reviews that behaviour separately.",
    "There is no VC++ framework dependency: the executable is a one-file build."
  ) | Set-Content -Path $note -Encoding ascii
} else {
  @(
    "Keyware $version Store packages",
    "",
    "The identity values passed to this build were written into the manifest.",
    "This run did not check them against Partner Center. Upload only if you",
    "copied them from Product identity yourself.",
    "",
    "Upload this file:",
    "  Keyware-$version.msixupload",
    "Partner Center also accepts Keyware-$version.msixbundle or the two",
    "architecture .msix files. Upload one of those, not all three.",
    "Do not upload a file whose name starts with NOT-STORE-READY.",
    "Leave the package unsigned. The Store re-signs it."
  ) | Set-Content -Path $note -Encoding ascii
}

$lines = @()
Get-ChildItem $OutDir -File | Where-Object { $_.Extension -in ".msix", ".msixbundle", ".msixupload" } | Sort-Object Name | ForEach-Object {
  $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
  Set-Content -Path ($_.FullName + ".sha256") -Value $hash -Encoding ascii
  $lines += "$hash  $($_.Name)"
}
Set-Content -Path (Join-Path $OutDir "SHA256SUMS") -Value $lines -Encoding ascii
Write-Host ($lines -join [Environment]::NewLine)
Write-Host "Wrote $note"
