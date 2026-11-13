/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Both Windows builds. Keep the hashes identical to site/api/_lib/release.js.
  // /download/Keyware.exe is counted, then served from /download/<VERSION>/Keyware.exe.
  // /download/Keyware-arm64.exe is counted, then redirected to the GitHub release asset.
  VERSION: "1.4.1",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.8 MB",
  SHA256: "6EE816FD5706AD91FF13F1C6FA97E6EDA4E9738E884C7D10AD08BBE0A21554A0",
  ARM_URL: "/download/Keyware-arm64.exe",
  ARM_FILE_SIZE: "16.1 MB",
  ARM_SHA256: "D9CEEE6E7AC046E38FC0E736339E011D07175B191E874CCFDAF1DAA460E75E8D",
  // [data-download] opens the thanks page and starts the x64 file.
  // [data-arm] opens the thanks page and starts the ARM64 file.
  // [data-dl-direct] is the file itself, used for "download again" on the thanks page.
  THANKS_URL: "/thanks"
};
