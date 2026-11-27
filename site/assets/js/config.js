/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Both Windows builds. Keep the hashes identical to site/api/_lib/release.js.
  // /download/Keyware.exe is counted, then served from /download/<VERSION>/Keyware.exe.
  // /download/Keyware-arm64.exe is counted, then redirected to the GitHub release asset.
  VERSION: "1.4.4",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.9 MB",
  SHA256: "FDE40C8EE789BC3FF411A809502226D2B09912243E4E8885D76FD52FFD645FE0",
  ARM_URL: "/download/Keyware-arm64.exe",
  ARM_FILE_SIZE: "16.1 MB",
  ARM_SHA256: "45A019818A38E00FF4A2056078D2873B71DD5C919FB08F4C1810D1A783E26EC1",
  // [data-download] opens the thanks page and starts the x64 file.
  // [data-arm] opens the thanks page and starts the ARM64 file.
  // [data-dl-direct] is the file itself, used for "download again" on the thanks page.
  THANKS_URL: "/thanks"
};
