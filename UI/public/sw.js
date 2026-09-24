// Bump this version whenever offline.html changes, so existing clients refresh it.
const OFFLINE_CACHE = 'assozeta-offline-v2';
const OFFLINE_PAGE = '/offline.html';

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(OFFLINE_CACHE)
            .then((cache) => cache.add(new Request(OFFLINE_PAGE, {cache: 'reload'})))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(
                names.filter((name) => name.startsWith('assozeta-offline-') && name !== OFFLINE_CACHE)
                    .map((name) => caches.delete(name))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    if (event.request.mode !== 'navigate' || new URL(event.request.url).origin !== self.location.origin) return;

    // A live 503 response is Caddy's maintenance page; use the offline fallback
    // only when the network request itself fails.
    event.respondWith(
        fetch(event.request).catch(async () =>
            (await (await caches.open(OFFLINE_CACHE)).match(OFFLINE_PAGE)) || Response.error()
        )
    );
});
