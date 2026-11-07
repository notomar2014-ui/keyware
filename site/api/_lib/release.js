'use strict';
// The release the site hands out.
//
// x64 is the file deployed with the site at /download/<version>/Keyware.exe.
// Its SHA-256 is the hash of that published file. ARM64 is not stored in this
// repo. ARM_SHA256 is the published v1.3.8 Keyware-arm64.exe, which the page
// shows next to the x64 hash. The download redirect still checks the live
// GitHub digest, and the page script replaces the ARM hash if that digest
// changes, so the hash on the button matches the file it downloads.

const VERSION = '1.3.9';
const GITHUB_REPO = 'notomar2014-ui/keyware';
const ARM_FILE = 'Keyware-arm64.exe';
const X64_SHA256 = '9B792C19FA2D2684DFD2D64BC252A1F0DF5114084667E1A5AC930DB48063BF09';
const X64_BYTES = 21173481;
const ARM_SHA256 = '84C97246CCB947643E0BD76B75921EDDA047B906A8E558A03B3BEA4142332E6D';
const ARM_BYTES = 16203491;

function formatSize(bytes) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const X64 = {
  arch: 'x64',
  version: VERSION,
  label: '64-bit (x64)',
  file: 'Keyware.exe',
  url: '/download/Keyware.exe',
  path: `/download/${VERSION}/Keyware.exe`,
  bytes: X64_BYTES,
  size: formatSize(X64_BYTES),
  sha256: X64_SHA256,
};

const FILE_PATH = X64.path;
const ARM_PAGE = `https://github.com/${GITHUB_REPO}/releases/download/v${VERSION}/${ARM_FILE}`;

function publicBuild(build) {
  return {
    arch: build.arch,
    version: build.version,
    label: build.label,
    file: build.file,
    url: build.url,
    bytes: build.bytes,
    size: build.size,
    sha256: build.sha256,
  };
}

function armFromAsset(asset) {
  if (!asset || asset.name !== ARM_FILE) return null;
  if (asset.state && asset.state !== 'uploaded') return null;
  const digest = String(asset.digest || '').match(/^sha256:([0-9a-f]{64})$/i);
  if (!digest) return null;
  if (asset.browser_download_url !== ARM_PAGE) return null;
  const bytes = Number(asset.size);
  if (!Number.isFinite(bytes) || bytes < 5_000_000) return null;
  return {
    arch: 'arm64',
    version: VERSION,
    label: 'ARM64',
    file: ARM_FILE,
    url: `/download/${ARM_FILE}`,
    redirect: ARM_PAGE,
    bytes,
    size: formatSize(bytes),
    sha256: digest[1].toUpperCase(),
  };
}

let armCache = { at: 0, arm: undefined, ok: false };

function resetArmCache() {
  armCache = { at: 0, arm: undefined, ok: false };
}

async function lookupArm({ fetchImpl = globalThis.fetch, now = Date.now, cacheMs = 5 * 60 * 1000, missMs = 60 * 1000, cache } = {}) {
  const box = cache || armCache;
  const t = now();
  if (box.arm !== undefined && t - box.at < (box.ok ? cacheMs : missMs)) return box.arm;
  const url = `https://api.github.com/repos/${GITHUB_REPO}/releases/tags/v${VERSION}`;
  const res = await fetchImpl(url, {
    headers: {
      accept: 'application/vnd.github+json',
      'user-agent': 'keyware-site',
      'x-github-api-version': '2022-11-28',
    },
    signal: AbortSignal.timeout(4000),
  });
  if (!res.ok) {
    box.at = t;
    box.arm = null;
    box.ok = false;
    return null;
  }
  const data = await res.json();
  const asset = (data.assets || []).find((item) => item && item.name === ARM_FILE);
  const arm = armFromAsset(asset);
  box.at = t;
  box.arm = arm;
  box.ok = true;
  return arm;
}

module.exports = {
  VERSION,
  GITHUB_REPO,
  ARM_FILE,
  ARM_PAGE,
  X64,
  X64_SHA256,
  X64_BYTES,
  ARM_SHA256,
  ARM_BYTES,
  FILE_PATH,
  formatSize,
  publicBuild,
  armFromAsset,
  lookupArm,
  resetArmCache,
  SHA256: X64_SHA256,
};
