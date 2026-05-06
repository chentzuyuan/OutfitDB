// OutfitDB service worker.
// Strategy: stale-while-revalidate for static, network-first for HTML, no cache for APIs.
// EXCEPTION: i18n.js + temp_unit.js are network-first because new translation
// keys / temperature helpers ship as data inside the JS file — using a stale
// cached copy would surface raw "closet.foo_bar" keys in the UI when the page
// references a key that only exists in the newer file. Worth the small
// perf cost on cold load.
//
// Bump VERSION on every brand rename + on any non-trivial JS/CSS push
// — installed PWAs hold the cache until VERSION changes, so a stale
// cache otherwise serves the previous brand's <title> + hero text long
// after a rename ships.
const VERSION = 'od-v2';
const SHELL = [
    '/',
    '/static/css/app.css',
    '/static/js/app.js',
    '/static/js/weather.js',
    '/static/manifest.json',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
];
const NETWORK_FIRST_STATIC = new Set([
    '/static/js/i18n.js',
    '/static/js/temp_unit.js',
]);

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL).catch(() => {})));
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(
            keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))
        ))
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);

    // Don't intercept Open-Meteo / external APIs
    if (url.origin !== self.location.origin) return;

    // i18n + temp_unit JS → network-first so new keys land immediately.
    // Falls back to cache only if the network request fails.
    if (NETWORK_FIRST_STATIC.has(url.pathname)) {
        event.respondWith(
            fetch(req).then((res) => {
                if (res.ok) {
                    const copy = res.clone();
                    caches.open(VERSION).then((c) => c.put(req, copy));
                }
                return res;
            }).catch(() => caches.match(req))
        );
        return;
    }

    // Other static assets → stale-while-revalidate (return cache immediately, update in background)
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(req).then((cached) => {
                const fetchPromise = fetch(req).then((res) => {
                    if (res.ok) {
                        const copy = res.clone();
                        caches.open(VERSION).then((c) => c.put(req, copy));
                    }
                    return res;
                }).catch(() => cached);
                return cached || fetchPromise;
            })
        );
        return;
    }

    // HTML pages → network-first, fallback to cache
    if (req.headers.get('accept')?.includes('text/html')) {
        event.respondWith(
            fetch(req).then((res) => {
                const copy = res.clone();
                caches.open(VERSION).then((c) => c.put(req, copy));
                return res;
            }).catch(() => caches.match(req))
        );
        return;
    }

    // Default: network only (APIs always fresh)
});
