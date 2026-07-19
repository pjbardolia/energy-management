// Mevion PWA service worker — basic app-shell caching.
// Caches the built JS/CSS/HTML so the app shell loads instantly on repeat
// visits and shows a cached view briefly if the network is momentarily
// unavailable. Does NOT cache API responses — live telemetry data always
// comes from the network, never served stale from cache.

const CACHE_NAME = 'mevion-shell-v1';
const APP_SHELL = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never cache API calls — telemetry must always be live.
  // Matches API_BASE = "/api" in src/App.jsx.
  if (url.pathname.startsWith('/api/')) {
    return; // let it hit the network normally
  }

  // App shell: cache-first, falling back to network.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      return cached || fetch(event.request).then((response) => {
        // Cache successful same-origin GET responses for next time.
        if (event.request.method === 'GET' && response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => cached);
    })
  );
});
