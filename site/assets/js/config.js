/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Both Windows builds. Keep the hashes identical to site/api/_lib/release.js.
  // /download/Keyware.exe is counted, then served from /download/<VERSION>/Keyware.exe.
  // /download/Keyware-arm64.exe is counted, then redirected to the GitHub release asset.
  VERSION: "1.4.3",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.8 MB",
  SHA256: "D113388C26015C74EAD0CC2AAACA472EB75F1662BC6D3595FDE13C3C94591733",
  ARM_URL: "/download/Keyware-arm64.exe",
  ARM_FILE_SIZE: "16.1 MB",
  ARM_SHA256: "2020B1C2BDF356B5D1CE49B3848D608DC11CFAA9BE015FEFF815C3F99C3F5D50",
  // [data-download] opens the thanks page and starts the x64 file.
  // [data-arm] opens the thanks page and starts the ARM64 file.
  // [data-dl-direct] is the file itself, used for "download again" on the thanks page.
  THANKS_URL: "/thanks"
};
