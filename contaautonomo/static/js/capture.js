/* Receipt capture page (G-8): take or pick a photo, or receive one shared
 * from the phone, then hand it to the expense form on the same page.
 *
 * When a module implements ai.parse_document the photo goes into the form's
 * "Parse receipt with AI" input, whose handler posts it to that route, prefills the
 * fields and attaches the photo as the receipt. Without it, the photo is only
 * attached and the fields are filled by hand. Nothing is saved until the user
 * presses "Create Expense". External file, no inline code (CSP-ready).
 */
'use strict';
// Runs after the form's own DOMContentLoaded handler, which wires the AI parse input.
document.addEventListener('DOMContentLoaded', function () {
    var status = document.getElementById('capture-status');
    var formEl = document.getElementById('expense-form');

    function say(text) {
        if (status) status.textContent = text;
    }

    function handOver(file) {
        if (!file) {
            say('No photo was received. Take or choose one.');
            return;
        }
        var dt = new DataTransfer();
        dt.items.add(file);
        var aiInput = document.getElementById('ai-parse-file');
        if (aiInput) {
            aiInput.files = dt.files;
            aiInput.dispatchEvent(new Event('change'));
            say('Photo received — reading it with the AI parser…');
        } else {
            var receipt = document.getElementById('file');
            if (receipt) receipt.files = dt.files;
            say('Photo attached. The AI parser is not enabled: fill in the fields below.');
        }
        if (formEl) formEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    ['capture-camera', 'capture-gallery'].forEach(function (id) {
        var input = document.getElementById(id);
        if (!input) return;
        input.addEventListener('change', function () {
            handOver(input.files && input.files[0]);
        });
    });
    document.querySelectorAll('[data-capture-for]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var input = document.getElementById(btn.getAttribute('data-capture-for'));
            if (input) input.click();
        });
    });

    // A photo shared from another app: the service worker holds it until asked.
    var params = new URLSearchParams(window.location.search);
    if (params.get('shared') === '1' && 'serviceWorker' in navigator) {
        say('Receiving the shared photo…');
        navigator.serviceWorker.addEventListener('message', function (event) {
            if (event.data && event.data.type === 'shared-receipt') handOver(event.data.file);
        });
        navigator.serviceWorker.ready.then(function (reg) {
            var worker = navigator.serviceWorker.controller || reg.active;
            if (worker) worker.postMessage({ type: 'capture-ready' });
        });
    }
});
