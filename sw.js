// /sw.js  — STR robust SW (safe for GitHub Pages)
const VER = 'v3';
const CACHE_STATIC = `str-static-${VER}`;
const CACHE_SHELL  = `str-shell-${VER}`;
const CACHE_JSON   = `str-json-${VER}`;
const CACHE_IMG    = `str-img-${VER}`;

// Keep these lists small & same-origin
const PRECACHE_URLS = [
  '/',                 // if your root serves index.html
  '/index.html',       // safe to include even if '/' already covers it
  '/manifest.webmanifest',
  '/icons/apple-touch-icon.png'
];

// Runtime cache caps (avoid unbounded growth)
const MAX_JSON_ENTRIES = 30;
const MAX_IMG_ENTRIES  = 40;

// ---------- tiny utils ----------
async function limitCache(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > max) {
    // FIFO eviction: delete oldest first
    await cache.delete(keys[0]);
    return limitCache(cacheName, max);
  }
}
function sameOrigin(url) {
  return new URL(url, self.location.origin).origin === self.location.origin;
}

// ---------- install ----------
self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(CACHE_STATIC);
    await cache.addAll(PRECACHE_URLS);
  })());
  self.skipWaiting();
});

// ---------- activate ----------
self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    try {
      if (self.registration.navigationPreload) {
        await self.registration.navigationPreload.enable();
      }
    } catch {}
    const keep = new Set([CACHE_STATIC, CACHE_SHELL, CACHE_JSON, CACHE_IMG]);
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => !keep.has(k)).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

// Optional: let the page tell the SW to update instantly or purge caches
self.addEventListener('message', (e) => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
  if (e.data === 'PURGE_CACHES') {
    caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k))));
  }
});

// ---------- fetch strategies ----------
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // 1) Daily JSON: stale-while-revalidate (fast + quietly refresh)
  if (sameOrigin(url) && url.pathname.startsWith('/data/news-') && url.pathname.endsWith('.json')) {
    e.respondWith((async () => {
      const cache = await caches.open(CACHE_JSON);
      const cached = await cache.match(req);
      const networkPromise = fetch(req).then(res => {
        if (res.ok) cache.put(req, res.clone()).then(() => limitCache(CACHE_JSON, MAX_JSON_ENTRIES));
        return res;
      }).catch(() => cached);
      return cached || networkPromise;
    })());
    return;
  }

  // 2) Navigations (HTML): network-first with preload; shell fallback offline
  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      // try preload (very fast when available)
      const preload = await e.preloadResponse;
      if (preload) {
        // keep a fresh shell copy if it looks like HTML
        if (preload.headers.get('content-type')?.includes('text/html')) {
          const shell = await caches.open(CACHE_SHELL);
          shell.put('/index.html', preload.clone());
        }
        return preload;
      }

      try {
        const res = await fetch(req);
        if (res.ok && res.headers.get('content-type')?.includes('text/html')) {
          const shell = await caches.open(CACHE_SHELL);
          shell.put('/index.html', res.clone());
        }
        return res;
      } catch {
        // offline fallback to last known shell or precached /
        const shell = await caches.open(CACHE_SHELL);
        return (await shell.match('/index.html'))
            || (await caches.match('/'))
            || new Response('<!doctype html><title>Offline</title><h1>Offline</h1>', {
                 headers: { 'Content-Type': 'text/html; charset=utf-8' }
               });
      }
    })());
    return;
  }

  if (sameOrigin(url) && PRECACHE_URLS.includes(url.pathname)) {
    e.respondWith(caches.match(req).then(r => r || fetch(req)));
    return;
  }

  if (sameOrigin(url) && req.destination === 'image') {
    e.respondWith((async () => {
      const cache = await caches.open(CACHE_IMG);
      const cached = await cache.match(req);
      if (cached) return cached;
      try {
        const res = await fetch(req, { mode: 'same-origin' }); // fail closed for 3rd-party
        if (res.ok) {
          cache.put(req, res.clone()).then(() => limitCache(CACHE_IMG, MAX_IMG_ENTRIES));
        }
        return res;
      } catch {
        return cached || Response.error();
      }
    })());
    return;
  }

});