/* Prism service worker — makes the app installable + usable offline.
   Shell: cache-first (instant launch). Data (/api/prism): network-first with a cache
   fallback, so you always get live numbers when online and the last-seen book when not. */
const SHELL = 'prism-shell-v2';
const DATA = 'prism-data-v2';
const SHELL_ASSETS = [
  'prism.html', 'prism.webmanifest',
  'prism-icon-192.png', 'prism-icon-512.png', 'prism-icon-180.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;

  // Data: network-first, fall back to the last cached book.
  if (url.pathname === '/api/prism') {
    e.respondWith(
      fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(DATA).then((c) => c.put(e.request, copy));
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Shell + icons: cache-first, revalidate in the background.
  if (SHELL_ASSETS.some((a) => url.pathname.endsWith('/' + a))) {
    e.respondWith(
      caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy));
        return res;
      }))
    );
  }
});
