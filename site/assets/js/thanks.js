(function () {
  "use strict";
  var cfg = window.KEYWARE_CONFIG || {};
  var status = document.querySelector("[data-dl-text]");
  var title = document.getElementById("thanks-title");
  var arch = "";
  try { arch = new URLSearchParams(window.location.search).get("arch") || ""; } catch (_e) { arch = ""; }

  function say(text) { if (status) status.textContent = text; }
  function label(kind) {
    return kind === "arm64" ? "Your ARM64 download is starting…" : "Your 64-bit (x64) download is starting…";
  }
  function officialUrl(kind) {
    if (kind === "arm64") return "/download/Keyware-arm64.exe";
    if (kind === "x64") return "/download/Keyware.exe";
    return "";
  }
  // Same-origin x64 can load in a hidden frame: a 404 stays in the frame, and an
  // attachment download leaves this page in place. ARM64 302s to GitHub, and the
  // site CSP (default-src 'self') blocks framing that host, so ARM uses a link click.
  // A click on an attachment response also stays on this page. Neither path is a popup.
  function start(kind) {
    var url = officialUrl(kind);
    if (!url) return;
    if (title) title.textContent = "Thanks for downloading Keyware";
    say(label(kind));
    if (kind === "x64") {
      var frame = document.createElement("iframe");
      frame.hidden = true;
      frame.tabIndex = -1;
      frame.title = "Keyware download";
      frame.setAttribute("aria-hidden", "true");
      frame.src = url;
      document.body.appendChild(frame);
      return;
    }
    var a = document.createElement("a");
    a.href = url;
    a.hidden = true;
    a.tabIndex = -1;
    a.setAttribute("aria-hidden", "true");
    document.body.appendChild(a);
    a.click();
  }

  document.querySelectorAll("[data-dl-direct]").forEach(function (el) {
    el.addEventListener("click", function () {
      var kind = el.hasAttribute("data-arm") ? "arm64" : "x64";
      if (title) title.textContent = "Thanks for downloading Keyware";
      say(label(kind));
    });
  });

  if (arch !== "x64" && arch !== "arm64") return;
  start(arch);

  if (arch !== "arm64" || !window.fetch) return;
  fetch("/api/release", { headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (rel) {
      var builds = rel && rel.version === cfg.VERSION ? rel.builds || [] : [];
      var arm = null;
      for (var i = 0; i < builds.length; i++) {
        var b = builds[i];
        if (b.arch === "arm64" && b.version === cfg.VERSION && b.url === "/download/Keyware-arm64.exe" && /^[0-9A-F]{64}$/.test(b.sha256 || "")) arm = b;
      }
      if (!arm) return;
      document.querySelectorAll("[data-arm-sha]").forEach(function (el) { el.textContent = arm.sha256; });
      if (arm.size) document.querySelectorAll("[data-arm-size]").forEach(function (el) { el.textContent = arm.size; });
    })
    .catch(function () { /* the download already uses the published ARM64 URL */ });
})();
