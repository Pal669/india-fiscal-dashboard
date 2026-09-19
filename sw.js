// Shell files are cached for offline use; data files are network-first so new figures show up as soon as they exist.
const VERSION = "fiscal-v1";
const SHELL = ["./", "index.html", "style.css", "app.js", "vendor/chart.umd.min.js", "manifest.webmanifest", "icons/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  const isData = req.url.includes("/data/");
  e.respondWith(
    isData
      ? fetch(req).then(r => { const copy = r.clone(); caches.open(VERSION).then(c => c.put(req.url.split("?")[0], copy)); return r; })
          .catch(() => caches.match(req.url.split("?")[0]))
      : caches.match(req, { ignoreSearch: true }).then(hit => hit || fetch(req).then(r => { const copy = r.clone(); caches.open(VERSION).then(c => c.put(req, copy)); return r; }))
  );
});
