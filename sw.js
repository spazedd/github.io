self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());  // Activate immediately
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());  // Control pages now
});

self.addEventListener('fetch', event => {
  event.respondWith(fetch(event.request));  // Pass-through to network
});
