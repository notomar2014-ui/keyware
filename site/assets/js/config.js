/* Keyware site config. Edit these values; every page reads them. */
window.KEYWARE_CONFIG = {
  // Footer "Support Keyware" link. Leave empty ("") to hide the link everywhere.
  DONATE_URL: "https://ko-fi.com/keyware_donate",
  // Both Windows builds. Keep the hashes identical to site/api/_lib/release.js.
  // /download/Keyware.exe is counted, then served from /download/<VERSION>/Keyware.exe.
  // /download/Keyware-arm64.exe is counted, then redirected to the GitHub release asset.
  VERSION: "1.4.2",
  DOWNLOAD_URL: "/download/Keyware.exe",
  FILE_SIZE: "20.8 MB",
  SHA256: "D49A9432A1D915C06A50790BDA00051C3431714D763F53EFA3A369EB39DA02D8",
  ARM_URL: "/download/Keyware-arm64.exe",
  ARM_FILE_SIZE: "16.1 MB",
  ARM_SHA256: "FB4AC798CA8455CDEF10366ACBB32C8D02DE2FEED2371DA640C417510B7FDBDA",
  // [data-download] opens the thanks page and starts the x64 file.
  // [data-arm] opens the thanks page and starts the ARM64 file.
  // [data-dl-direct] is the file itself, used for "download again" on the thanks page.
  THANKS_URL: "/thanks"
};
