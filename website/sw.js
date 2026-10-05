// Darwish Smart Power service worker: the page shell works offline and opens instantly;
// live data (/api/...) and the APK always come from the network.
const CACHE = "dsp-v12";
const SHELL = ["/", "/panel", "/manifest.webmanifest", "/icons/icon.svg", "/icons/icon-192.png",
               "/icons/icon-512.png", "/icons/maskable-512.png", "/icons/apple-touch-icon.png", "/icons/favicon-32.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin ||
      url.pathname.startsWith("/api/") || url.pathname.endsWith(".apk")) return;
  // network first so updates show up right away; the cache is the offline fallback
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request, {ignoreSearch: true})));
});
