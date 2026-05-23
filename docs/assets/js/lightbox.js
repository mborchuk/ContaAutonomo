/* ============================================================================
 * lightbox.js — ContaAutónomo GitHub Pages site
 * ----------------------------------------------------------------------------
 * Lightbox component for the screenshots gallery.
 *
 * Usage in HTML:
 *
 *   <button type="button"
 *           data-lightbox-src="/assets/img/screenshots/dashboard.png"
 *           data-lightbox-alt="Dashboard with KPIs and invoices"
 *           data-lightbox-caption="Dashboard with monthly summary">
 *     <img src="/assets/img/screenshots/dashboard.png" alt="...">
 *   </button>
 *
 *   <dialog id="lightbox">
 *     <img alt="">
 *     <figcaption></figcaption>
 *     <button type="button" data-lightbox-close>Close</button>
 *   </dialog>
 *
 * Public API:
 *   window.initLightbox(rootSelector = 'body', dialogId = 'lightbox')
 *
 * Behavior:
 *   - Delegated click listener on the root captures any element with the
 *     data-lightbox-src attribute (or its nearest ancestor that has it).
 *   - Native <dialog>: dialog.showModal() is used when available so the
 *     browser handles focus trap, Escape, and ::backdrop for free.
 *   - Polyfill fallback (no showModal): set [open], aria-modal="true",
 *     focus the close button, install a Tab focus trap that cycles between
 *     focusable elements within the dialog, and listen for Escape manually.
 *   - Close on: [data-lightbox-close] click, backdrop click on the dialog
 *     element itself, or Escape key.
 *   - Image onerror replaces the inner <img> with a <div role="img"
 *     aria-label="Missing screenshot: <alt>"> placeholder so the lightbox
 *     never shows a broken-image icon.
 *   - After close, focus returns to the opener button.
 *
 * Requirements: 5.4, 5.5, 5.7.
 * ========================================================================= */

(function () {
  "use strict";

  var FOCUSABLE_SELECTOR = [
    "a[href]",
    "area[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "iframe",
    "object",
    "embed",
    "[tabindex]:not([tabindex='-1'])",
    "[contenteditable='true']",
  ].join(",");

  function supportsNativeDialog() {
    return (
      typeof HTMLDialogElement !== "undefined" &&
      typeof HTMLDialogElement.prototype.showModal === "function"
    );
  }

  function buildPlaceholder(altText) {
    var div = document.createElement("div");
    div.className = "screenshot-placeholder";
    div.setAttribute("role", "img");
    div.setAttribute(
      "aria-label",
      "Missing screenshot: " + (altText || "image unavailable")
    );
    div.textContent = "Missing screenshot: " + (altText || "image unavailable");
    return div;
  }

  function getFocusable(container) {
    if (!container) return [];
    var nodes = container.querySelectorAll(FOCUSABLE_SELECTOR);
    return Array.prototype.filter.call(nodes, function (el) {
      // Exclude elements that are not visible / disabled by attribute.
      return !el.hasAttribute("disabled") && el.offsetParent !== null;
    });
  }

  function initLightbox(rootSelector, dialogId) {
    var root =
      (rootSelector && document.querySelector(rootSelector)) || document.body;
    var dialog = document.getElementById(dialogId || "lightbox");
    if (!root || !dialog) {
      return;
    }

    var native = supportsNativeDialog();
    var openerEl = null;
    var trapHandler = null;
    var escHandler = null;

    function getDialogImg() {
      // The inner image may have been replaced by a placeholder <div>.
      // Always look up the current first <img> or placeholder that we own.
      return dialog.querySelector("img");
    }

    function getCaptionEl() {
      return dialog.querySelector("figcaption");
    }

    function setImage(src, alt) {
      // Restore an <img> element if a placeholder is currently in place.
      var existing =
        dialog.querySelector("img") ||
        dialog.querySelector(".screenshot-placeholder");
      var img = document.createElement("img");
      img.alt = alt || "";
      img.onerror = function () {
        img.replaceWith(buildPlaceholder(alt));
      };
      img.src = src;
      if (existing) {
        existing.replaceWith(img);
      } else {
        dialog.insertBefore(img, dialog.firstChild);
      }
    }

    function setCaption(text) {
      var fig = getCaptionEl();
      if (fig) {
        fig.textContent = text || "";
      }
    }

    function trapFocus(event) {
      if (event.key !== "Tab") return;
      var focusable = getFocusable(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      var active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function openDialog() {
      if (native) {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
        dialog.setAttribute("aria-modal", "true");
        dialog.setAttribute("role", "dialog");
        var closeBtn = dialog.querySelector("[data-lightbox-close]");
        if (closeBtn) closeBtn.focus();
        trapHandler = trapFocus;
        document.addEventListener("keydown", trapHandler, true);
        escHandler = function (e) {
          if (e.key === "Escape") {
            e.preventDefault();
            closeDialog();
          }
        };
        document.addEventListener("keydown", escHandler, true);
      }
    }

    function closeDialog() {
      if (native) {
        if (dialog.open) dialog.close();
      } else {
        dialog.removeAttribute("open");
        dialog.removeAttribute("aria-modal");
        if (trapHandler) {
          document.removeEventListener("keydown", trapHandler, true);
          trapHandler = null;
        }
        if (escHandler) {
          document.removeEventListener("keydown", escHandler, true);
          escHandler = null;
        }
      }
      if (openerEl && typeof openerEl.focus === "function") {
        openerEl.focus();
      }
      openerEl = null;
    }

    // --- Open: delegated click on root -------------------------------------
    root.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || typeof target.closest !== "function") return;
      var trigger = target.closest("[data-lightbox-src]");
      if (!trigger || !root.contains(trigger)) return;
      // Ignore clicks that originate inside the dialog itself.
      if (dialog.contains(trigger)) return;
      event.preventDefault();
      openerEl = trigger;
      var src = trigger.getAttribute("data-lightbox-src") || "";
      var alt = trigger.getAttribute("data-lightbox-alt") || "";
      var caption = trigger.getAttribute("data-lightbox-caption") || "";
      setImage(src, alt);
      setCaption(caption);
      openDialog();
    });

    // --- Close: delegated click on document --------------------------------
    document.addEventListener("click", function (event) {
      var target = event.target;
      if (!target) return;
      // Click on close button (or its child) inside this dialog.
      if (
        target.closest &&
        target.closest("[data-lightbox-close]") &&
        dialog.contains(target)
      ) {
        event.preventDefault();
        closeDialog();
        return;
      }
      // Backdrop click — only when the click target is the dialog element
      // itself (clicks on inner content bubble through but originate on
      // their own targets, not the dialog).
      if (target === dialog) {
        closeDialog();
      }
    });

    // Native <dialog> emits a 'cancel' event on Escape; mirror our cleanup
    // so focus returns to the opener.
    dialog.addEventListener("cancel", function () {
      // The browser will call close() after this event; defer focus restore.
      setTimeout(function () {
        if (openerEl && typeof openerEl.focus === "function") {
          openerEl.focus();
        }
        openerEl = null;
      }, 0);
    });
    dialog.addEventListener("close", function () {
      if (openerEl && typeof openerEl.focus === "function") {
        openerEl.focus();
        openerEl = null;
      }
    });
  }

  // Expose the public API.
  window.initLightbox = initLightbox;

  // Auto-init on DOMContentLoaded when at least one trigger is present.
  document.addEventListener("DOMContentLoaded", function () {
    if (document.querySelector("[data-lightbox-src]")) {
      initLightbox("body", "lightbox");
    }
  });
})();
