/* mobile.js - drawer toggle, PWA registration + install prompt.
 * Progressive enhancement: nothing here is required for the site to work.
 */
(function () {
  "use strict";

  // ---- Off-canvas drawer -------------------------------------------------
  var drawer = document.getElementById("cwm-drawer");
  var backdrop = document.getElementById("cwm-drawer-backdrop");
  var toggle = document.getElementById("cwm-drawer-toggle");

  function openDrawer(e) {
    if (e) { e.preventDefault(); }
    if (!drawer) { return; }
    drawer.classList.add("open");
    backdrop.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    if (toggle) { toggle.setAttribute("aria-expanded", "true"); }
  }

  function closeDrawer() {
    if (!drawer) { return; }
    drawer.classList.remove("open");
    backdrop.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    if (toggle) { toggle.setAttribute("aria-expanded", "false"); }
  }

  if (toggle) { toggle.addEventListener("click", openDrawer); }
  if (backdrop) { backdrop.addEventListener("click", closeDrawer); }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeDrawer(); }
  });

  // ---- Service worker registration ---------------------------------------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      var swUrl = (window.CWM_SW_URL || "/sw.js");
      navigator.serviceWorker.register(swUrl, { updateViaCache: "none" })
        .catch(function (err) { console.warn("SW registration failed", err); });
    });
  }

  // ---- Install prompt ----------------------------------------------------
  var deferredPrompt = null;
  var chip = document.getElementById("cwm-install-chip");
  var DISMISS_KEY = "cwm-install-dismissed";

  function chipDismissed() {
    try { return localStorage.getItem(DISMISS_KEY) === "1"; } catch (e) { return false; }
  }

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    if (chip && !chipDismissed()) { chip.classList.add("show"); }
  });

  if (chip) {
    var accept = document.getElementById("cwm-install-accept");
    var dismiss = chip.querySelector(".cwm-install-dismiss");
    if (accept) {
      accept.addEventListener("click", function () {
        chip.classList.remove("show");
        if (!deferredPrompt) { return; }
        deferredPrompt.prompt();
        deferredPrompt.userChoice.finally(function () { deferredPrompt = null; });
      });
    }
    if (dismiss) {
      dismiss.addEventListener("click", function () {
        chip.classList.remove("show");
        try { localStorage.setItem(DISMISS_KEY, "1"); } catch (e) { /* ignore */ }
      });
    }
  }

  window.addEventListener("appinstalled", function () {
    if (chip) { chip.classList.remove("show"); }
    deferredPrompt = null;
  });

  // ---- Search suggestions (typeahead) ------------------------------------
  var input = document.getElementById("query");
  if (input && window.CWM_SUGGEST_URL) {
    var box = document.createElement("div");
    box.id = "cwm-suggest";
    box.className = "cwm-suggest";
    box.style.display = "none";
    input.parentNode.style.position = "relative";
    input.parentNode.appendChild(box);

    var timer = null, lastQ = "";
    function hide() { box.style.display = "none"; }
    function render(items) {
      if (!items.length) { hide(); return; }
      box.innerHTML = items.map(function (it) {
        return '<a class="cwm-suggest-item" href="' + it.url + '">' +
          '<img src="' + it.cover + '" alt="" loading="lazy">' +
          '<span class="cwm-suggest-text"><span class="cwm-suggest-title">' +
          escapeHtml(it.title) + '</span><span class="cwm-suggest-author">' +
          escapeHtml(it.authors) + '</span></span></a>';
      }).join("");
      box.style.display = "block";
    }
    function escapeHtml(s) {
      return (s || "").replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function fetchSuggest(q) {
      fetch(window.CWM_SUGGEST_URL + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (items) { if (input.value.trim() === q) { render(items); } })
        .catch(hide);
    }
    input.setAttribute("autocomplete", "off");
    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (q === lastQ) { return; }
      lastQ = q;
      if (timer) { clearTimeout(timer); }
      if (q.length < 2) { hide(); return; }
      timer = setTimeout(function () { fetchSuggest(q); }, 150);
    });
    document.addEventListener("click", function (e) {
      if (!box.contains(e.target) && e.target !== input) { hide(); }
    });
  }
})();
