(function () {
  const registry = new Map();
  const stack = [];
  let manualLocks = 0;

  const getBaseZ = () => {
    try {
      const root = document.documentElement;
      const raw = root ? getComputedStyle(root).getPropertyValue("--z-overlay-base") : "";
      const parsed = parseInt(raw, 10);
      return Number.isNaN(parsed) ? 9000 : parsed;
    } catch (_) {
      return 9000;
    }
  };

  const applyLockState = () => {
    const locked = stack.length > 0 || manualLocks > 0;
    [document.documentElement, document.body].forEach((node) => {
      if (!node) return;
      node.classList.toggle("is-scroll-locked", locked);
    });
  };

  const applyZIndices = () => {
    const base = getBaseZ();
    stack.forEach((id, index) => {
      const cfg = registry.get(id);
      if (!cfg) return;
      const overlayZ = base + index * 10;
      if (cfg.backdrop && cfg.backdrop.style) {
        cfg.backdrop.style.zIndex = String(overlayZ);
      }
      if (cfg.element && cfg.element.style) {
        cfg.element.style.zIndex = String(overlayZ + 1);
      }
    });
  };

  const resolveElement = (selectorOrEl) => {
    if (!selectorOrEl) return null;
    if (selectorOrEl instanceof Element) return selectorOrEl;
    try {
      return document.querySelector(selectorOrEl);
    } catch (_) {
      return null;
    }
  };

  const attachCloseHandlers = (id, entry) => {
    if (!entry || entry._boundCloseHandlers) return;
    const backdrop = entry.backdrop || entry.element;
    if (backdrop) {
      backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop || event.target.closest("[data-overlay-close]")) {
          requestClose(id);
        }
      });
    }
    if (entry.element && entry.element !== backdrop) {
      entry.element.addEventListener("click", (event) => {
        if (event.target.closest("[data-overlay-close]")) {
          requestClose(id);
        }
      });
    }
    entry._boundCloseHandlers = true;
  };

  const register = (id, config) => {
    if (!id) return;
    const incoming = config || {};
    const element = incoming.element || resolveElement(`[data-overlay="${id}"]`) || resolveElement(incoming.selector);
    const backdrop = incoming.backdrop ? resolveElement(incoming.backdrop) : incoming.backdrop === null ? null : element;
    const focusTarget = resolveElement(incoming.focusTarget || (element && element.dataset && element.dataset.overlayFocus));
    const openClass =
      incoming.openClass ||
      (element && element.dataset && (element.dataset.overlayOpenClass || element.dataset.modalOpenClass)) ||
      "is-visible";
    const initiallyHidden = incoming.initiallyHidden != null ? incoming.initiallyHidden : Boolean(element && (element.hidden || element.hasAttribute("hidden")));
    const backdropInitiallyHidden = incoming.backdropInitiallyHidden != null
      ? incoming.backdropInitiallyHidden
      : Boolean(backdrop && (backdrop.hidden || backdrop.hasAttribute("hidden")));

    const existing = registry.get(id) || {};
    const merged = {
      ...existing,
      ...incoming,
      element: element || existing.element || null,
      backdrop: backdrop || existing.backdrop || null,
      focusTarget: focusTarget || existing.focusTarget || null,
      openClass,
      initiallyHidden: initiallyHidden || existing.initiallyHidden || false,
      backdropInitiallyHidden: backdropInitiallyHidden || existing.backdropInitiallyHidden || false,
    };
    registry.set(id, merged);
    attachCloseHandlers(id, merged);
  };

  const defaultOpenBehavior = (entry) => {
    const el = entry.element;
    if (!el) return;
    const backdrop = entry.backdrop || el;
    const cls = entry.openClass || "is-visible";
    if (backdrop) {
      backdrop.classList.add(cls);
      backdrop.removeAttribute("hidden");
      backdrop.hidden = false;
    }
    el.classList.add(cls);
    el.setAttribute("aria-hidden", "false");
    el.removeAttribute("hidden");
    el.hidden = false;
  };

  const defaultCloseBehavior = (entry) => {
    const el = entry.element;
    if (!el) return;
    const backdrop = entry.backdrop || el;
    const cls = entry.openClass || "is-visible";
    if (backdrop) {
      backdrop.classList.remove(cls);
      if (entry.backdropInitiallyHidden || entry.initiallyHidden) {
        backdrop.hidden = true;
        backdrop.setAttribute("hidden", "hidden");
      }
    }
    el.classList.remove(cls);
    el.setAttribute("aria-hidden", "true");
    if (entry.initiallyHidden) {
      el.hidden = true;
      el.setAttribute("hidden", "hidden");
    }
  };

  const open = (id, options) => {
    if (!id) return;
    const existing = registry.get(id);
    if (!existing && !options) {
      console.warn("[OverlayManager] Unknown overlay:", id);
      return;
    }
    const cfg = { ...(existing || {}), ...(options || {}) };
    if (!cfg.element) {
      cfg.element = resolveElement(options && options.element);
    }
    if (!cfg.element) {
      console.warn("[OverlayManager] Missing element for overlay:", id);
      return;
    }
    cfg.backdrop = cfg.backdrop === null ? null : resolveElement(cfg.backdrop) || cfg.backdrop || cfg.element;
    cfg.focusTarget = resolveElement(cfg.focusTarget) || cfg.focusTarget || null;
    cfg.__lastFocus = document.activeElement;
    registry.set(id, cfg);
    attachCloseHandlers(id, cfg);

    defaultOpenBehavior(cfg);

    const existingIndex = stack.indexOf(id);
    if (existingIndex !== -1) {
      stack.splice(existingIndex, 1);
    }
    stack.push(id);
    applyZIndices();
    applyLockState();
    if (console && typeof console.debug === "function") {
      console.debug("[OverlayManager] open:", id);
    }
  };

  const close = (id) => {
    const idx = stack.indexOf(id);
    const cfg = registry.get(id);
    if (!cfg) {
      if (idx === -1) return;
    }
    if (idx !== -1) {
      stack.splice(idx, 1);
    }
    if (cfg) {
      defaultCloseBehavior(cfg);
    }
    applyZIndices();
    applyLockState();
    if (console && typeof console.debug === "function") {
      console.debug("[OverlayManager] close:", id);
    }
    const focusTarget = cfg && cfg.restoreFocus === false ? null : cfg.focusTarget || cfg.__lastFocus;
    if (focusTarget && typeof focusTarget.focus === "function") {
      setTimeout(() => {
        try { focusTarget.focus({ preventScroll: true }); } catch (_) {}
      }, 30);
    }
  };

  const requestClose = (id) => {
    const cfg = registry.get(id);
    if (cfg && typeof cfg.onRequestClose === "function") {
      cfg.onRequestClose();
      return;
    }
    close(id);
  };

  const closeTop = () => {
    const topId = stack[stack.length - 1];
    if (!topId) return;
    requestClose(topId);
  };

  const isOpen = (id) => stack.includes(id);
  const anyOpen = () => stack.length > 0;

  const lock = () => { manualLocks += 1; applyLockState(); };
  const unlock = () => { manualLocks = Math.max(manualLocks - 1, 0); applyLockState(); };

  const autoRegister = () => {
    document.querySelectorAll("[data-overlay]").forEach((el) => {
      const id = (el.dataset && el.dataset.overlay || "").trim();
      if (!id) return;
      register(id, {
        element: el,
        backdrop: el.dataset ? el.dataset.overlayBackdrop : undefined,
        openClass: el.dataset ? (el.dataset.overlayOpenClass || el.dataset.modalOpenClass) : undefined,
        focusTarget: el.dataset ? el.dataset.overlayFocus : undefined,
      });
    });
  };

  document.addEventListener("DOMContentLoaded", autoRegister);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeTop();
    }
  });

  window.OverlayManager = { register, open, close, requestClose, closeTop, isOpen, anyOpen, lock, unlock, stack };
  window.__overlayLock = { lock, unlock, reset: () => { manualLocks = 0; applyLockState(); } };
})();
