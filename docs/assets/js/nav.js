/**
 * nav.js — primary navigation toggle for mobile viewports.
 *
 * Contract
 * --------
 *  - Targets:
 *      • `<button class="nav-toggle" aria-expanded aria-controls="primary-menu">`
 *      • `<ul id="primary-menu" class="nav-list" hidden>`
 *    inside `<nav class="site-nav" aria-label="Primary">` (see _includes/header.html).
 *  - If either target is missing, the script returns silently (no-op).
 *
 * Behavior
 * --------
 *  1. Click on `.nav-toggle` toggles the menu:
 *       • aria-expanded "false" → "true"  AND removes `hidden` from menu
 *       • aria-expanded "true"  → "false" AND adds `hidden` to menu
 *  2. `Escape` keypress while the menu is open closes it and restores
 *     focus to the toggle button (keyboard accessibility, WCAG 2.1.2).
 *  3. Click outside `.site-nav` while the menu is open closes it.
 *  4. On window resize ≥ 768px (desktop breakpoint), the menu state is
 *     normalized: aria-expanded="false" + `hidden` re-applied. Desktop
 *     CSS (`@media (min-width: 768px)`) overrides `[hidden]` so the
 *     menu remains visible at desktop widths; this normalization keeps
 *     the mobile state coherent when the viewport shrinks again.
 *
 * Requirements: 1.5, 8.4
 *
 * The script is loaded with `defer`, so the DOM is ready when it runs.
 */
(() => {
  'use strict';

  /** Desktop breakpoint in pixels — keep in sync with --bp-md in CSS. */
  const DESKTOP_BREAKPOINT = 768;

  const toggle = document.querySelector('.nav-toggle');
  const menu = document.getElementById('primary-menu');
  const nav = document.querySelector('.site-nav');

  if (!toggle || !menu) {
    return;
  }

  const isOpen = () => toggle.getAttribute('aria-expanded') === 'true';

  const openMenu = () => {
    toggle.setAttribute('aria-expanded', 'true');
    menu.hidden = false;
  };

  const closeMenu = () => {
    toggle.setAttribute('aria-expanded', 'false');
    menu.hidden = true;
  };

  // 1. Click on toggle
  toggle.addEventListener('click', (event) => {
    event.stopPropagation();
    if (isOpen()) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  // 2. Escape key closes the menu and restores focus to the toggle
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isOpen()) {
      closeMenu();
      toggle.focus();
    }
  });

  // 3. Click outside the nav closes the menu
  document.addEventListener('click', (event) => {
    if (!isOpen()) {
      return;
    }
    const target = event.target;
    if (nav && target instanceof Node && nav.contains(target)) {
      return;
    }
    closeMenu();
  });

  // 4. Normalize state when crossing the desktop breakpoint
  window.addEventListener('resize', () => {
    if (window.innerWidth >= DESKTOP_BREAKPOINT) {
      // Desktop CSS overrides [hidden]; reset mobile state for when the
      // viewport later shrinks back below the breakpoint.
      toggle.setAttribute('aria-expanded', 'false');
      menu.hidden = true;
    }
  });
})();
