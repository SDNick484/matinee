// Matinee service worker: a phone keeps the app and the last collection it saw, for a shop with no signal.
//
// A classic worker script, not an ES module, so it lives outside static/js/ (whose package.json makes node --check
// read modules). app.py serves it at /sw.js, so its scope is the whole app, with the CONFIG line below replaced by
// the real values: the versioned assets (asset_versions(), the same URLs the page's import map names), whether
// PUBLIC_VIEW is on, and a version that changes with any of them or with this file. A deploy that changes one hash
// therefore changes /sw.js (served no-cache), the browser installs this worker afresh, and activate deletes the
// old shell. node --check reads this file as it is, placeholder and all.
//
// What it keeps:
//   matinee-shell-<version>  "/" (PUBLIC_VIEW=1 only), the stylesheet and every module at their hashes, the fonts
//                            and the favicon.
//   matinee-data             PUBLIC_VIEW=1 only: the last good /api/titles, /api/titles/<id>, /api/facets,
//                            /api/locations and /images/<file>. That is what the public /api serves to anyone;
//                            watch notes, people and attendance are never in it.
// What it never stores or answers: anything under /admin (page or API), a request carrying a key or an Access JWT,
// a response marked no-store or private (app.py marks every /api answer to such a request no-store), a redirect,
// another origin. The one no-store answer it keeps is the public page "/" itself, the shell: it holds no data, and
// its CSP nonce is replaced every time it is served from here. Writes go straight to the server and are never
// queued; offline, static/js/offline.js disables the editor's controls.
"use strict";

const CONFIG = {"version": "dev", "public": false, "assets": [], "extras": []};

const PREFIX = "matinee-";
const SHELL = `${PREFIX}shell-${CONFIG.version}`;
const DATA = `${PREFIX}data`;
const NETWORK_TIMEOUT_MS = 3000;   // poor signal: answer from the saved copy rather than wait longer
const DATA_PATH = /^\/api\/(?:titles(?:\/\d+)?|facets|locations)$/;
const IMAGE_PATH = /^\/images\/[\w.-]+$/;
const KEY_HEADERS = ["authorization", "x-api-key", "cf-access-jwt-assertion"];
const SEARCH_ONLY = new Set(["q", "sort"]);   // a search the saved full list can answer by itself
const STORED_AT = "X-Matinee-Stored-At";

function isAdminPath(pathname) {
  let path;
  try {
    path = decodeURIComponent(pathname);
  } catch {
    return true;   // a path that will not decode is treated as private
  }
  return /^\/+admin(?:\/|$)/i.test(path);
}

function carriesKey(request) {
  return KEY_HEADERS.some((h) => request.headers.has(h));
}

// A same-origin 200 that was not redirected and is not under /admin.
function plain(response) {
  if (!response || response.status !== 200 || response.type !== "basic" || response.redirected) return false;
  const url = new URL(response.url);
  return url.origin === self.location.origin && !isAdminPath(url.pathname);
}

function shareable(response) {
  return plain(response) && !/\b(?:no-store|private)\b/i.test(response.headers.get("Cache-Control") || "");
}

function storable(response) {
  return CONFIG.public && shareable(response);
}

// /api/titles? and /api/titles, or q=x&sort=y and sort=y&q=x, are one entry.
function dataKey(url) {
  const params = [...url.searchParams].filter(([, v]) => v !== "").sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return url.origin + url.pathname + (params.length ? `?${new URLSearchParams(params)}` : "");
}

async function stamped(response) {
  const headers = new Headers(response.headers);
  headers.delete("Content-Encoding");
  headers.delete("Content-Length");
  headers.set(STORED_AT, new Date().toISOString());
  return new Response(await response.blob(), { status: response.status, statusText: response.statusText, headers });
}

function timeout(ms) {
  return new Promise((resolve) => setTimeout(() => resolve(null), ms));
}

// Tell the page whether the data it asked for is live (asOf null) or this phone's copy from asOf.
function tell(event, asOf) {
  if (!event.clientId) return;
  self.clients.get(event.clientId)
    .then((client) => client && client.postMessage({ type: "matinee-data", asOf }))
    .catch(() => {});
}

// The same folding as common.py's fold, words and search_fold, so a saved list finds what the server would.
function fold(s) {
  return String(s || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().trim();
}

function words(s) {
  return fold(s).replace(/[^\p{L}\p{N}_]+/gu, " ").split(" ").filter(Boolean);
}

function searchText(title) {
  return `${words(title).join(" ")} | ${fold(title).replace(/[^\p{L}\p{N}_\s]+/gu, "")}`;
}

// The saved answer for this URL; for a search never made online, the saved full list filtered by the search words.
async function savedCopy(cache, key, url) {
  const exact = await cache.match(key, { ignoreVary: true });
  if (exact) return exact;
  if (url.pathname !== "/api/titles" || [...url.searchParams.keys()].some((k) => !SEARCH_ONLY.has(k))) return null;
  const all = await cache.match(url.origin + "/api/titles", { ignoreVary: true });
  if (!all) return null;
  let data;
  try {
    data = await all.json();
  } catch {
    return null;
  }
  const want = words(url.searchParams.get("q"));
  const titles = (data.titles || []).filter((t) => {
    const hay = searchText(t.title);
    return want.every((w) => hay.includes(w));
  });
  return new Response(JSON.stringify({ titles, count: titles.length, sort: data.sort }), {
    headers: { "Content-Type": "application/json", [STORED_AT]: all.headers.get(STORED_AT) || "" },
  });
}

function offlineAnswer() {
  return new Response(JSON.stringify({ error: "You're offline, and this page isn't saved on this device yet." }), {
    status: 503, headers: { "Content-Type": "application/json" },
  });
}

// Network first with a short timeout, then the last good copy. `store`: keep a good answer (data and posters);
// `data`: tell the page where its data came from.
async function networkFirst(event, url, { cacheName, store, data }) {
  const cache = await caches.open(cacheName);
  const key = store ? dataKey(url) : url.href;
  const network = fetch(event.request).then((response) => {
    if (store && storable(response)) {
      const copy = response.clone();
      event.waitUntil(stamped(copy).then((s) => cache.put(key, s)).catch(() => {}));
    }
    return response;
  });
  event.waitUntil(network.catch(() => null));
  let response = null;
  try {
    response = await Promise.race([network, timeout(NETWORK_TIMEOUT_MS)]);
  } catch {
    response = null;
  }
  if (response && response.status < 500) {
    if (data) tell(event, null);
    return response;
  }
  const saved = await savedCopy(cache, key, url);
  if (saved) {
    if (data) tell(event, saved.headers.get(STORED_AT));
    return saved;
  }
  if (response) return response;   // a server error, and nothing saved
  try {
    const late = await network;      // slow, not failed: wait for it after all
    if (data) tell(event, null);
    return late;
  } catch {
    return data ? offlineAnswer() : Response.error();
  }
}

// The saved shell, with a fresh nonce in its policy and its import map (a nonce is never replayed).
async function renonced(saved) {
  const html = await saved.text();
  const headers = new Headers(saved.headers);
  const csp = headers.get("Content-Security-Policy") || "";
  const old = (/'nonce-([^']+)'/.exec(csp) || [])[1];
  if (!old) return new Response(html, { headers });
  const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  headers.set("Content-Security-Policy", csp.split(`'nonce-${old}'`).join(`'nonce-${nonce}'`));
  return new Response(html.split(`nonce="${old}"`).join(`nonce="${nonce}"`), { headers });
}

async function shellPage(event) {
  const network = fetch(event.request);
  event.waitUntil(network.catch(() => null));
  let response = null;
  try {
    response = await Promise.race([network, timeout(NETWORK_TIMEOUT_MS)]);
  } catch {
    response = null;
  }
  if (response && response.status < 500) return response;   // online: always the server's page, never stored here
  const saved = await caches.match(self.location.origin + "/", { cacheName: SHELL, ignoreVary: true });
  if (saved) return renonced(saved);
  if (response) return response;
  return network;   // nothing saved: whatever the network does, the browser shows
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const shell = await caches.open(SHELL);
    await shell.addAll(CONFIG.assets);   // all or nothing: a shell missing one module could not start
    await Promise.all(CONFIG.extras.map((url) => shell.add(url).catch(() => null)));   // fonts, favicon: nice to have
    if (CONFIG.public) {
      const page = await fetch("/", { cache: "no-store" });
      const html = plain(page) ? await page.clone().text() : "";
      // the page must name exactly the assets above, or a deploy happened mid-install: fail, and retry next visit
      if (!html || !CONFIG.assets.every((url) => html.includes(`"${url}"`))) throw new Error("shell does not match");
      await shell.put(self.location.origin + "/", page);
      const data = await caches.open(DATA);
      await Promise.all(["/api/titles", "/api/facets"].map(async (path) => {
        try {
          const response = await fetch(path);
          if (storable(response)) await data.put(self.location.origin + path, await stamped(response));
        } catch {
          // the grid saves its own copy on the next visit
        }
      }));
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keep = new Set(CONFIG.public ? [SHELL, DATA] : [SHELL]);   // a private collection keeps no data at all
    for (const name of await caches.keys()) {
      if (name.startsWith(PREFIX) && !keep.has(name)) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;   // writes go straight to the server, and fail offline: never queued
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || isAdminPath(url.pathname) || carriesKey(request)) return;
  if (request.mode === "navigate") {
    if (url.pathname === "/" && CONFIG.public) event.respondWith(shellPage(event));
    return;
  }
  if (CONFIG.assets.includes(url.pathname + url.search)) {
    // a versioned URL never changes content: the saved copy, else the network
    event.respondWith(caches.match(request, { cacheName: SHELL, ignoreVary: true }).then((r) => r || fetch(request)));
  } else if (CONFIG.extras.includes(url.pathname) && !url.search) {
    event.respondWith(networkFirst(event, url, { cacheName: SHELL, store: false, data: false }));
  } else if (CONFIG.public && DATA_PATH.test(url.pathname)) {
    event.respondWith(networkFirst(event, url, { cacheName: DATA, store: true, data: true }));
  } else if (CONFIG.public && IMAGE_PATH.test(url.pathname)) {
    event.respondWith(networkFirst(event, url, { cacheName: DATA, store: true, data: false }));
  }
});
