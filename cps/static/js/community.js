/* community.js - detail page social interactions (ratings, reviews, likes).
 * Uses fetch() with the CSRF token from the hidden #cwm-csrf input.
 */
(function () {
  "use strict";
  var block = document.getElementById("community-block");
  if (!block) { return; }

  var csrf = (document.getElementById("cwm-csrf") || {}).value || "";
  var canInteract = block.getAttribute("data-can-interact") === "1";

  function post(url, payload) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf
      },
      body: JSON.stringify(payload || {})
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    });
  }

  // ---- Star rating -------------------------------------------------------
  var stars = block.querySelectorAll(".cwm-star");
  function paintStars(value) {
    stars.forEach(function (s) {
      var v = parseInt(s.getAttribute("data-value"), 10);
      s.classList.toggle("glyphicon-star", v <= value);
      s.classList.toggle("glyphicon-star-empty", v > value);
      s.setAttribute("aria-checked", v === value ? "true" : "false");
    });
  }
  if (canInteract) {
    stars.forEach(function (s) {
      s.addEventListener("click", function () {
        var value = parseInt(s.getAttribute("data-value"), 10);
        // Clicking the only selected star clears the rating.
        if (s.classList.contains("glyphicon-star") && s.getAttribute("aria-checked") === "true") {
          value = 0;
        }
        post(block.getAttribute("data-url-rating"), { rating: value }).then(function (res) {
          if (!res.ok) { return; }
          paintStars(res.data.your);
          var avg = block.querySelector(".cwm-avg");
          if (avg) {
            avg.innerHTML = res.data.count
              ? "★ " + res.data.avg + " (" + res.data.count + ")"
              : "—";
          }
        });
      });
    });
  }

  // ---- Book like ---------------------------------------------------------
  var likeBtn = block.querySelector(".cwm-like-btn");
  if (likeBtn && canInteract) {
    likeBtn.addEventListener("click", function () {
      post(block.getAttribute("data-url-like"), {}).then(function (res) {
        if (!res.ok) { return; }
        likeBtn.classList.toggle("active", res.data.liked);
        likeBtn.setAttribute("aria-pressed", res.data.liked ? "true" : "false");
        likeBtn.querySelector(".cwm-like-count").textContent = res.data.count;
      });
    });
  }

  // ---- Review save -------------------------------------------------------
  var saveBtn = document.getElementById("cwm-review-save");
  if (saveBtn) {
    saveBtn.addEventListener("click", function () {
      var text = (document.getElementById("cwm-review-text") || {}).value || "";
      post(block.getAttribute("data-url-review"), { text: text }).then(function (res) {
        if (res.ok) { window.location.reload(); }
      });
    });
  }

  // ---- Review likes + delete (event delegation) --------------------------
  block.addEventListener("click", function (e) {
    var likeEl = e.target.closest(".cwm-review-like");
    if (likeEl) {
      post(likeEl.getAttribute("data-url"), {}).then(function (res) {
        if (!res.ok) { return; }
        likeEl.classList.toggle("active", res.data.liked);
        likeEl.setAttribute("aria-pressed", res.data.liked ? "true" : "false");
        likeEl.querySelector(".cwm-rl-count").textContent = res.data.count;
      });
      return;
    }
    var delEl = e.target.closest(".cwm-review-delete");
    if (delEl) {
      if (!window.confirm("Delete this review?")) { return; }
      post(delEl.getAttribute("data-url"), {}).then(function (res) {
        if (res.ok) { window.location.reload(); }
      });
    }
  });
})();
