/**
 * mermaid-loader.js — lazy-load Mermaid only on pages that need it.
 *
 * Contract
 * --------
 *  - On page load, query for any `.mermaid` elements (rendered by the
 *    `mermaid_block.html` include).
 *      • If none exist → return silently. No CDN request is made; this
 *        keeps unrelated pages free from a third-party fetch (privacy +
 *        performance).
 *      • If at least one exists → inject a single `<script>` tag pointing
 *        at the pinned Mermaid CDN bundle.
 *  - When the bundle finishes loading, initialize Mermaid in a
 *    deterministic, security-conscious mode and render every `.mermaid`
 *    block on the page.
 *  - If the bundle fails to load (network, CSP, ad-blocker), do not
 *    throw. The accessible ASCII fallback inside the sibling
 *    `<details class="mermaid-fallback">` element remains fully usable.
 *
 * Configuration
 * -------------
 *  - CDN: jsDelivr, Mermaid 10.x major (deliberately not pinned to a full
 *    version so patch/minor updates flow in automatically). Subresource
 *    Integrity (SRI) is intentionally omitted because the resolved file
 *    changes within the major range; we instead rely on `crossorigin`
 *    + the major-version pin.
 *  - `securityLevel: 'strict'` disables `<foreignObject>` HTML injection
 *    and sanitizes user-supplied diagram content. `startOnLoad: false`
 *    lets us call `mermaid.run()` ourselves once the DOM is ready.
 *
 * Requirements: 3.1, 8.5
 *
 * The script is loaded with `defer`, so this IIFE runs after the DOM is
 * parsed; we still guard with a `DOMContentLoaded` listener for robustness
 * in case the script tag is moved earlier in the future.
 */
(() => {
  'use strict';

  /** Pinned Mermaid CDN bundle — major version 10. */
  const MERMAID_CDN_URL = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js';

  /** Initialization options shared by every page that uses Mermaid. */
  const MERMAID_INIT_OPTIONS = Object.freeze({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'strict',
  });

  const start = () => {
    // Bail out early when the page has no Mermaid blocks. This is the
    // hot path for ~all site pages and must not trigger any network I/O.
    if (!document.querySelector('.mermaid')) {
      return;
    }

    // Avoid double-injection if the script somehow runs twice.
    if (document.querySelector('script[data-mermaid-loader]')) {
      return;
    }

    const script = document.createElement('script');
    script.src = MERMAID_CDN_URL;
    script.defer = true;
    script.crossOrigin = 'anonymous';
    script.dataset.mermaidLoader = 'true';

    script.addEventListener('load', () => {
      // `mermaid` is exposed as a global by the UMD bundle.
      const mermaid = /** @type {any} */ (window).mermaid;
      if (!mermaid || typeof mermaid.initialize !== 'function') {
        return;
      }
      try {
        mermaid.initialize(MERMAID_INIT_OPTIONS);
        if (typeof mermaid.run === 'function') {
          // Returns a Promise in Mermaid 10; we ignore rejections so a
          // single bad diagram does not break the page.
          Promise.resolve(mermaid.run({ querySelector: '.mermaid' })).catch(
            () => {
              /* fallback <details> remains visible */
            },
          );
        }
      } catch (_err) {
        /* swallow: ASCII fallback covers this case */
      }
    });

    script.addEventListener('error', () => {
      // Network/CSP/ad-blocker failure. The <details> ASCII fallback
      // inside each .mermaid-block already provides the diagram in
      // plain text, so there is nothing else to do here.
    });

    document.head.appendChild(script);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
