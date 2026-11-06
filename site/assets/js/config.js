/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Current x64 download. /download/Keyware.exe is counted by /api/download, which sends
  // the file from /download/<VERSION>/Keyware.exe. SHA256 is that file's hash.
  // Keep these identical to site/api/_lib/release.js. ARM64 is not listed here:
  // the page asks /api/release and shows it only when that same version has one.
  VERSION: "1.3.8",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.2 MB",
  SHA256: "9B792C19FA2D2684DFD2D64BC252A1F0DF5114084667E1A5AC930DB48063BF09",
  // [data-download] starts this file. /thanks starts it too.
  THANKS_URL: "/thanks"
};
