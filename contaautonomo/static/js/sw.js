/* ContaAutónomo service worker (G-8).
 *
 * Minimal on purpose: it makes the app installable and receives photos shared
 * from the phone's share menu. It caches NOTHING — no Cache API, no offline
 * copy — so no page, receipt or figure is stored by the worker. Every request
 * except the share target goes straight to the network.
 *
 * Share target: the manifest sends a shared image as a POST to
 * /expenses/capture/share. The worker answers with a redirect to the capture
 * page and keeps the file in memory only until that page asks for it
 * ("capture-ready"), then hands it over with postMessage. If the page does not
 * ask within a minute (for example because the session expired and the login
 * page opened instead), the file is dropped.
 */
'use strict';

const SHARE_PATH = '/expenses/capture/share';
const CAPTURE_URL = '/expenses/capture?shared=1';
const HOLD_MS = 60000;

let pendingShare = null;   // { filePromise, done }

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (event.request.method !== 'POST' || url.origin !== self.location.origin
            || url.pathname !== SHARE_PATH) {
        return;   // not ours: the browser fetches it normally
    }
    const filePromise = event.request.formData()
        .then((data) => data.get('receipt'))
        .catch(() => null);
    event.respondWith(Response.redirect(CAPTURE_URL, 303));
    event.waitUntil(new Promise((resolve) => {
        const entry = { filePromise, done: resolve };
        pendingShare = entry;
        setTimeout(() => {
            if (pendingShare === entry) pendingShare = null;
            resolve();
        }, HOLD_MS);
    }));
});

self.addEventListener('message', (event) => {
    if (!event.data || event.data.type !== 'capture-ready' || !event.source) return;
    const entry = pendingShare;
    pendingShare = null;
    if (!entry) {
        event.source.postMessage({ type: 'shared-receipt', file: null });
        return;
    }
    entry.filePromise.then((file) => {
        event.source.postMessage({ type: 'shared-receipt', file: file || null });
        entry.done();
    });
});
