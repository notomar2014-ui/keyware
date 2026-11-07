/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Both Windows builds. Keep the hashes identical to site/api/_lib/release.js.
  // /download/Keyware.exe is counted, then served from /download/<VERSION>/Keyware.exe.
  // /download/Keyware-arm64.exe is counted, then redirected to the GitHub release asset.
  VERSION: "1.3.8",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.2 MB",
  SHA256: "9B792C19FA2D2684DFD2D64BC252A1F0DF5114084667E1A5AC930DB48063BF09",
  ARM_URL: "/download/Keyware-arm64.exe",
  ARM_FILE_SIZE: "15.5 MB",
  ARM_SHA256: "84C97246CCB947643E0BD76B75921EDDA047B906A8E558A03B3BEA4142332E6D",
  // [data-download] opens the thanks page and starts the x64 file.
  // [data-arm] opens the thanks page and starts the ARM64 file.
  // [data-dl-direct] is the file itself, used for "download again" on the thanks page.
  THANKS_URL: "/thanks"
};
