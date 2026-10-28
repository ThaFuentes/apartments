const ASSET_V = "51";

const CACHE = "apt-shell-" + ASSET_V;
const SHELL = [
  "/static/offline.html",
  "/static/css/apt.css?v=" + ASSET_V,
  "/static/css/apt-desk.css?v=" + ASSET_V,
  "/static/js/apt.js?v=" + ASSET_V,
  "/static/js/outbox.js?v=" + ASSET_V,
  "/static/js/overlays.js?v=" + ASSET_V
];

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
  const fieldPage = url.pathname === "/" || url.pathname === "/ready" || url.pathname.indexOf("/units/") === 0 || url.pathname.indexOf("/properties/") === 0;
  event.respondWith(
    fetch(req).then(function (resp) {
      var keep = resp.ok && resp.status === 200 && (url.pathname.startsWith("/api/pack/") || url.pathname.startsWith("/static/") || (fieldPage && !url.search));
      if (keep) {
        const copy = resp.clone();
        caches.open(CACHE).then(function (cache) { cache.put(req, copy); });
      }
      return resp;
    }).catch(function () {
      return caches.match(req).then(function (hit) { return hit || caches.match("/static/offline.html"); });
    })
  );
});
