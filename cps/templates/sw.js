/* Service worker for calibre-web-mobile (rendered by pwa.py).
 * Strategy summary:
 *   - HTML navigations: network-first, NEVER cached -> avoids stale auth/CSRF.
 *   - /static/**: stale-while-revalidate.
 *   - covers: cache-first, size-capped (URLs carry a cache-buster on change).
 *   - downloads / opds / kobo / readers / ajax: never intercepted.
 */
var CACHE = "{{ cache_version }}";
var STATIC_CACHE = CACHE + "-static";
var COVER_CACHE = CACHE + "-covers";
var OFFLINE_URL = "{{ offline_url }}";
var STATIC_BASE = "{{ static_base }}";
var COVER_MAX = 300;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(function (cache) {
      return cache.addAll([OFFLINE_URL]);
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (key) {
        // Drop every cache that isn't from the current version bundle.
        if (key.indexOf(CACHE) !== 0) { return caches.delete(key); }
        return null;
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

function trimCache(cacheName, maxItems) {
  caches.open(cacheName).then(function (cache) {
    cache.keys().then(function (keys) {
      if (keys.length > maxItems) {
        cache.delete(keys[0]).then(function () { trimCache(cacheName, maxItems); });
      }
    });
  });
}

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") { return; }

  var url = new URL(req.url);
  if (url.origin !== self.location.origin) { return; }

  var path = url.pathname;

  // Never touch these: auth-sensitive, streamed, or protocol endpoints.
  if (path.indexOf("/download/") === 0 ||
      path.indexOf("/send/") === 0 ||
      path.indexOf("/opds") === 0 ||
      path.indexOf("/kobo") === 0 ||
      path.indexOf("/read/") === 0 ||
      path.indexOf("/ajax/") === 0) {
    return;
  }

  // Covers: cache-first, capped.
  if (path.indexOf("/cover/") === 0 || path.indexOf("/series_cover/") === 0) {
    event.respondWith(
      caches.open(COVER_CACHE).then(function (cache) {
        return cache.match(req).then(function (cached) {
          if (cached) { return cached; }
          return fetch(req).then(function (resp) {
            if (resp && resp.status === 200) {
              cache.put(req, resp.clone());
              trimCache(COVER_CACHE, COVER_MAX);
            }
            return resp;
          });
        });
      })
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  if (STATIC_BASE && path.indexOf(STATIC_BASE) === 0) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(function (cache) {
        return cache.match(req).then(function (cached) {
          var network = fetch(req).then(function (resp) {
            if (resp && resp.status === 200) { cache.put(req, resp.clone()); }
            return resp;
          }).catch(function () { return cached; });
          return cached || network;
        });
      })
    );
    return;
  }

  // HTML navigations: network-first, fall back to offline page. Never cached.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(function () {
        return caches.match(OFFLINE_URL);
      })
    );
    return;
  }
});
