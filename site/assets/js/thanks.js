(function () {
  "use strict";
  var cfg = window.KEYWARE_CONFIG || {};
  var status = document.querySelector("[data-dl-text]");
  var arch = "";
  try { arch = new URLSearchParams(window.location.search).get("arch") || ""; } catch (_e) { arch = ""; }

  function say(text) { if (status) status.textContent = text; }
  function go(url) { window.location.assign(url); }
  function x64() { go(cfg.DOWNLOAD_URL || "/download/Keyware.exe"); }
  function publishedArm() {
    if (cfg.ARM_URL !== "/download/Keyware-arm64.exe") return null;
    if (!/^[0-9A-F]{64}$/.test(cfg.ARM_SHA256 || "")) return null;
    return { file: "Keyware-arm64.exe", size: cfg.ARM_FILE_SIZE, sha256: cfg.ARM_SHA256, url: cfg.ARM_URL };
  }
  function startArm(arm) {
    document.querySelectorAll("[data-file]").forEach(function (el) { el.textContent = arm.file || "Keyware-arm64.exe"; });
    if (arm.size) document.querySelectorAll("[data-size]").forEach(function (el) { el.textContent = arm.size; });
    document.querySelectorAll("[data-sha]").forEach(function (el) { el.textContent = arm.sha256; });
    document.querySelectorAll("[data-download-direct]").forEach(function (el) { el.setAttribute("href", arm.url); });
    say("Your ARM64 download is starting…");
    go(arm.url);
  }

  if (arch === "x64") { x64(); return; }
  if (arch !== "arm64") {
    say("Choose 64-bit (x64) or ARM64. There is no Mac download.");
    return;
  }

  var fallback = publishedArm();
  fetch("/api/release", { headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (rel) {
      var arm = null;
      var builds = rel && rel.version === cfg.VERSION ? rel.builds || [] : [];
      for (var i = 0; i < builds.length; i++) {
        var b = builds[i];
        if (b.arch === "arm64" && b.version === cfg.VERSION && b.url === "/download/Keyware-arm64.exe" && /^[0-9A-F]{64}$/.test(b.sha256 || "")) arm = b;
      }
      if (arm) { startArm(arm); return; }
      if (fallback) { startArm(fallback); return; }
      say("The ARM64 build is not published for this version. The 64-bit download is starting instead.");
      x64();
    })
    .catch(function () {
      if (fallback) { startArm(fallback); return; }
      say("The ARM64 build could not be checked. The 64-bit download is starting instead.");
      x64();
    });
})();
