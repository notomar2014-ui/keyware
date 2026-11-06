(function () {
  "use strict";
  document.documentElement.classList.remove("no-js");
  var doc = document.documentElement;
  var cfg = window.KEYWARE_CONFIG || {};
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Config-driven bits */
  if (cfg.VERSION) document.querySelectorAll("[data-version]").forEach(function (el) { el.textContent = cfg.VERSION; });
  if (cfg.FILE_SIZE) document.querySelectorAll("[data-size]").forEach(function (el) { el.textContent = cfg.FILE_SIZE; });
  if (cfg.SHA256) document.querySelectorAll("[data-sha]").forEach(function (el) { el.textContent = cfg.SHA256; });
  if (cfg.DOWNLOAD_URL) document.querySelectorAll("[data-download]").forEach(function (el) { el.setAttribute("href", cfg.DOWNLOAD_URL); });
  var armWraps = document.querySelectorAll("[data-arm-wrap]");
  if (armWraps.length && window.fetch) {
    fetch("/api/release", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rel) {
        if (!rel || rel.version !== cfg.VERSION || !Array.isArray(rel.builds)) return;
        var arm = null;
        for (var i = 0; i < rel.builds.length; i++) if (rel.builds[i].arch === "arm64") arm = rel.builds[i];
        if (!arm || arm.version !== cfg.VERSION || arm.url !== "/download/Keyware-arm64.exe") return;
        if (!/^[0-9A-F]{64}$/.test(arm.sha256 || "")) return;
        document.querySelectorAll("[data-arm-sha]").forEach(function (el) { el.textContent = arm.sha256; });
        if (arm.size) document.querySelectorAll("[data-arm-size]").forEach(function (el) { el.textContent = arm.size; });
        document.querySelectorAll("[data-arm]").forEach(function (el) { el.setAttribute("href", arm.url); });
        armWraps.forEach(function (el) { el.hidden = false; });
      })
      .catch(function () { /* no ARM64 listing until the release asset can be checked */ });
  }
  document.querySelectorAll("[data-support]").forEach(function (el) {
    var url = (cfg.DONATE_URL || "").trim();
    if (url) { el.setAttribute("href", url); el.hidden = false; } else { el.hidden = true; }
  });
  var y = document.getElementById("year"); if (y) y.textContent = new Date().getFullYear();

  /* Nav border on scroll */
  var nav = document.querySelector(".nav");
  var onScroll = function () { if (nav) nav.classList.toggle("is-scrolled", window.scrollY > 8); };
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

  /* Reveal on scroll */
  var items = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window) || reduce) {
    items.forEach(function (el) { el.classList.add("is-in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    items.forEach(function (el, i) {
      var sib = el.parentElement ? Array.prototype.indexOf.call(el.parentElement.children, el) : 0;
      el.style.transitionDelay = Math.min(sib, 5) * 60 + "ms";
      io.observe(el);
    });
  }

  /* Card spotlight */
  document.querySelectorAll(".card").forEach(function (card) {
    card.addEventListener("pointermove", function (e) {
      var r = card.getBoundingClientRect();
      card.style.setProperty("--mx", (e.clientX - r.left) + "px");
      card.style.setProperty("--my", (e.clientY - r.top) + "px");
    });
  });

  /* Text-mode typing demo */
  var typed = document.getElementById("typed");
  if (typed) {
    var gen = document.getElementById("gen"), hint = document.getElementById("hint");
    var box = typed.parentElement, prop = document.getElementById("proposal");
    var f = function (k) { return prop.querySelector('[data-f="' + k + '"]'); };
    var demos = [
      { text: "F2 opens YouTube and searches minecraft", label: "YouTube: minecraft", trigger: "Tap F2", action: "Open URL", k: "URL", detail: "https://www.youtube.com/results?search_query=minecraft", profile: "Global" },
      { text: "Ctrl+Q types my email then presses Enter", label: "Email + Enter", trigger: "Chord Ctrl + Q", action: "Macro", k: "Steps", detail: "Type “you@example.com” → Key Enter", profile: "Global" },
      { text: "double tap F8 to autoclick left 20 times a second until I tap it again", label: "Auto-click 20/s", trigger: "Double-tap F8", action: "Auto-click", k: "Click", detail: "Left · 20 per second · Toggle", profile: "Gaming" },
      { text: "hold G for a second to open GitHub in my browser", label: "Open GitHub", trigger: "Hold G · 1000 ms", action: "Open URL", k: "URL", detail: "https://github.com", profile: "Browser" }
    ];
    var idx = 0, timers = [];
    var later = function (fn, ms) { timers.push(setTimeout(fn, ms)); };
    var fill = function (d) {
      ["label", "trigger", "action", "detail", "profile"].forEach(function (k) { f(k).textContent = d[k]; });
      f("k").textContent = d.k;
    };
    var run = function () {
      var d = demos[idx % demos.length]; idx++;
      prop.classList.remove("is-on"); typed.textContent = ""; box.classList.add("is-focus");
      hint.textContent = "Describe it in plain English";
      var i = 0;
      var step = function () {
        if (i <= d.text.length) {
          typed.textContent = d.text.slice(0, i++);
          later(step, 28 + Math.random() * 46);
        } else {
          later(function () {
            box.classList.remove("is-focus");
            gen.classList.add("is-press");
            later(function () { gen.classList.remove("is-press"); gen.classList.add("is-busy"); gen.textContent = "Generating…"; }, 180);
            later(function () {
              gen.classList.remove("is-busy"); gen.textContent = "Generate";
              hint.textContent = "Filled in by AI · 1.4 s. Review, then Save.";
              fill(d); prop.classList.add("is-on");
              later(run, 4200);
            }, 1200);
          }, 500);
        }
      };
      later(step, 500);
    };
    if (reduce) { typed.textContent = demos[0].text; fill(demos[0]); prop.classList.add("is-on"); hint.textContent = "Filled in by AI. Review, then Save."; }
    else if ("IntersectionObserver" in window) {
      var started = false;
      new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting && !started) { started = true; run(); } }); }, { threshold: 0.3 }).observe(box);
    } else run();
  }

  /* Profiles tabs */
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.profiles__tabs [role="tab"]'));
  if (tabs.length) {
    var appEl = document.getElementById("activeApp"), auto = null;
    var select = function (tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (appEl) appEl.textContent = tab.getAttribute("data-app");
      if (focus) tab.focus();
    };
    var stop = function () { if (auto) { clearInterval(auto); auto = null; } };
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { stop(); select(t); });
      t.addEventListener("keydown", function (e) {
        var n = null;
        if (e.key === "ArrowRight") n = tabs[(i + 1) % tabs.length];
        if (e.key === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
        if (e.key === "Home") n = tabs[0];
        if (e.key === "End") n = tabs[tabs.length - 1];
        if (n) { e.preventDefault(); stop(); select(n, true); }
      });
    });
    if (!reduce) {
      var k = 0;
      auto = setInterval(function () { k = (k + 1) % tabs.length; select(tabs[k]); }, 3200);
      var wrap = document.querySelector(".profiles");
      wrap.addEventListener("pointerenter", stop); wrap.addEventListener("focusin", stop);
    }
  }

  /* Guide: table-of-contents scroll spy + mobile toggle */
  var toc = document.querySelector(".toc");
  if (toc && "IntersectionObserver" in window) {
    var links = {};
    toc.querySelectorAll('a[href^="#"]').forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var current = null;
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          if (current) current.removeAttribute("aria-current");
          current = links[e.target.id]; if (current) current.setAttribute("aria-current", "true");
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    Object.keys(links).forEach(function (id) { var s = document.getElementById(id); if (s) spy.observe(s); });
  }
  var tocBtn = document.querySelector(".toc__toggle");
  if (tocBtn && toc) {
    tocBtn.addEventListener("click", function () {
      var open = tocBtn.getAttribute("aria-expanded") === "true";
      tocBtn.setAttribute("aria-expanded", open ? "false" : "true");
      toc.classList.toggle("is-open", !open);
    });
    toc.addEventListener("click", function (e) {
      if (e.target.closest("a") && window.innerWidth < 980) { tocBtn.setAttribute("aria-expanded", "false"); toc.classList.remove("is-open"); }
    });
  }
})();
