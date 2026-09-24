const CACHE = "apt-shell-2";
const SHELL = ["/static/offline.html", "/static/css/apt.css", "/static/js/apt.js"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (cache) { return cache.addAll(SHELL); }));
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) { return key !== CACHE; }).map(function (key) { return caches.delete(key); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/api/ping") || url.pathname.startsWith("/api/expense")) return;
  event.respondWith(
    fetch(req).then(function (resp) {
      if (url.pathname.startsWith("/api/pack/") || url.pathname.startsWith("/static/")) {
        const copy = resp.clone();
        caches.open(CACHE).then(function (cache) { cache.put(req, copy); });
      }
      return resp;
    }).catch(function () {
      return caches.match(req).then(function (hit) { return hit || caches.match("/static/offline.html"); });
    })
  );
});
