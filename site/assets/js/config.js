/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Current download. /download/Keyware.exe is counted by /api/download, which sends the file
  // from /download/<VERSION>/Keyware.exe (see api/_lib/release.js). Update both together.
  VERSION: "1.3.7",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.2 MB",
  // Download buttons ([data-download]) go here first; /thanks then starts DOWNLOAD_URL.
  THANKS_URL: "/thanks"
};
