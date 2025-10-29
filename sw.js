const CACHE_STATIC = 'str-static-v1';
const CACHE_JSON   = 'str-json-v1';

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_STATIC).then(c => c.addAll([
      '/', // index if you serve index at /
      '/manifest.webmanifest',
      '/icons/apple-touch-icon.png'
      // add other small static files you own if you want
    ]))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => ![CACHE_STATIC, CACHE_JSON].includes(k))
        .map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Stale-while-revalidate for your /data/*.json (fast, then refresh in background)
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // Cache JSON day files
  if (url.pathname.startsWith('/data/news-') && url.pathname.endsWith('.json')) {
    e.respondWith((async () => {
      const cache = await caches.open(CACHE_JSON);
      const cached = await cache.match(e.request);
      const network = fetch(e.request).then(r => {
        if (r.ok) cache.put(e.request, r.clone());
        return r;
      }).catch(() => cached);
      return cached || network;
    })());
    return;
  }

  // Cache-first for small static routes we added above
  if (['/', '/manifest.webmanifest', '/icons/apple-touch-icon.png'].includes(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then(res => res || fetch(e.request))
    );
  }
});