/* Registers the service worker that makes ContaAutónomo installable (G-8).
 * External file, no inline code, so it works under an enforced CSP. */
'use strict';
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function (err) {
            console.warn('Service worker registration failed:', err);
        });
    });
}
