/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Both Windows builds. Keep the hashes identical to site/api/_lib/release.js.
  // /download/Keyware.exe is counted, then served from /download/<VERSION>/Keyware.exe.
  // /download/Keyware-arm64.exe is counted, then redirected to the GitHub release asset.
  VERSION: "1.3.9",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.8 MB",
  SHA256: "C6BE4CA7A748D2315941AB7E717325C197DF03CC3B4D9DBC9DFCED5240008BA8",
  ARM_URL: "/download/Keyware-arm64.exe",
  ARM_FILE_SIZE: "16.0 MB",
  ARM_SHA256: "30A166A9A141EE83D833AE338C7686F94420CA7AC9195E91A2A34EBDE01A4B1C",
  // [data-download] opens the thanks page and starts the x64 file.
  // [data-arm] opens the thanks page and starts the ARM64 file.
  // [data-dl-direct] is the file itself, used for "download again" on the thanks page.
  THANKS_URL: "/thanks"
};
