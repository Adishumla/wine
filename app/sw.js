// Offline support: the app shell and the data files come from the cache when the network fails, and bottle
// photos on this site are kept once seen. Behind Cloudflare Access an expired session answers with a redirect to
// the login page: that must reach the browser as it is, and never be cached as the app or as data.

const VERSION = '__VERSION__'; // stamped at deploy, so every deploy installs a fresh shell
const DEV = VERSION.startsWith('__');
const SHELL = `shell-${VERSION}`;
const DATA = 'data';
const PHOTOS = 'photos';
const SHELL_FILES = ['index.html', 'app.js', 'data.js', 'vlist.js', 'style.css', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/apple-touch-icon.png'];

// Only a plain same-origin 200 is the real thing; a redirect (to the Access login) or an error is not.
const good = r => r && r.ok && r.type === 'basic' && !r.redirected;

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    for (const f of SHELL_FILES) {
      const r = await fetch(new Request(f, { cache: 'reload' }));
      if (!good(r)) throw new Error(`${f}: not cached (HTTP ${r.status}${r.redirected ? ', redirected' : ''})`);
      await cache.put(f, r);
    }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const k of await caches.keys()) if (![SHELL, DATA, PHOTOS].includes(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

// A cached answer, marked so the page can say it's showing saved data.
async function fromCache(cacheName, key) {
  const hit = await (await caches.open(cacheName)).match(key, { ignoreSearch: true });
  if (!hit) return null;
  const headers = new Headers(hit.headers);
  headers.set('x-wine-cache', 'saved');
  return new Response(hit.body, { status: hit.status, statusText: hit.statusText, headers });
}

async function networkFirst(request, cacheName, key) {
  try {
    const r = await fetch(request);
    if (good(r)) {
      (await caches.open(cacheName)).put(key, r.clone());
      return r;
    }
    return (await fromCache(cacheName, key)) ?? r; // e.g. signed out: saved data if there is any
  } catch (err) {
    const saved = await fromCache(cacheName, key);
    if (saved) return saved;
    throw err;
  }
}

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return; // the CDN's detail photos: as is

  if (req.mode === 'navigate') {
    // The network decides whether the page may load: a redirect to the Access login goes to the browser as it is.
    // A good answer gets the saved shell's index.html (it matches the saved scripts); offline, the same.
    event.respondWith((async () => {
      let r;
      try {
        r = await fetch(req);
      } catch {
        return (await fromCache(SHELL, 'index.html')) ?? Response.error();
      }
      if (!good(r) || DEV) return r;
      return (await (await caches.open(SHELL)).match('index.html')) ?? r;
    })());
    return;
  }
  const path = url.pathname.slice(new URL(self.registration.scope).pathname.length);
  if (path.startsWith('data/img/')) {
    event.respondWith((async () => {
      const cache = await caches.open(PHOTOS);
      const hit = await cache.match(req);
      if (hit) return hit; // a photo never changes
      const r = await fetch(req);
      if (good(r)) cache.put(req, r.clone());
      return r;
    })());
  } else if (path.startsWith('data/')) {
    event.respondWith(networkFirst(req, DATA, path));
  } else if (SHELL_FILES.includes(path)) {
    // Shell: the saved set, which a deploy replaces as a whole (a new VERSION installs a new worker), so old and
    // new files never mix. Unstamped (local development): the network first.
    event.respondWith(DEV ? networkFirst(req, SHELL, path)
      : caches.open(SHELL).then(c => c.match(path)).then(hit => hit ?? fetch(req)));
  }
});
