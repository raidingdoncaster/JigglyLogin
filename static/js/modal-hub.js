(function () {
  const body = document.body;
  const modalRegistry = {};

  function getOpenClass(modal) {
    return modal.dataset.modalOpenClass || 'is-open';
  }

  function getBodyClass(modal) {
    return modal.dataset.modalBodyClass || 'modal-open';
  }

  function isOpen(modal) {
    return modal.classList.contains(getOpenClass(modal));
  }

  function syncBodyClass(modal) {
    const bodyClass = getBodyClass(modal);
    const anyOpenForClass = Object.values(modalRegistry).some((entry) => {
      const target = entry.modal;
      return target && getBodyClass(target) === bodyClass && isOpen(target);
    });
    if (anyOpenForClass) {
      body.classList.add(bodyClass);
    } else {
      body.classList.remove(bodyClass);
    }
  }

  function openModal(modal, trigger) {
    if (!modal) return;
    const openClass = getOpenClass(modal);
    modal.classList.add(openClass);
    modal.removeAttribute('hidden');
    modal.setAttribute('aria-hidden', 'false');
    body.classList.add(getBodyClass(modal));
    if (window.OverlayManager) {
      const id = modal.dataset.modalRoot || modal.id || 'modal';
      window.OverlayManager.open(id, {
        element: modal,
        backdrop: modal,
        onRequestClose: () => closeModal(modal),
        focusTarget: trigger || document.activeElement,
      });
    } else if (modal.dataset.modalLockScroll === 'true' && window.__overlayLock && typeof window.__overlayLock.lock === 'function') {
      window.__overlayLock.lock();
    }
  }

  function closeModal(modal) {
    if (!modal) return;
    const openClass = getOpenClass(modal);
    modal.classList.remove(openClass);
    modal.setAttribute('aria-hidden', 'true');
    if (modal.dataset.modalKeepInDom !== 'true') {
      modal.hidden = true;
    }
    if (window.OverlayManager) {
      const id = modal.dataset.modalRoot || modal.id || 'modal';
      window.OverlayManager.close(id);
    } else if (modal.dataset.modalLockScroll === 'true' && window.__overlayLock && typeof window.__overlayLock.unlock === 'function') {
      window.__overlayLock.unlock();
    }
    syncBodyClass(modal);
  }

  function register(modal) {
    const id = modal.dataset.modalRoot;
    if (!id) return;
    modal.hidden = modal.hidden || modal.getAttribute('aria-hidden') === 'true';
    modal.setAttribute('aria-hidden', modal.hidden ? 'true' : 'false');
    modalRegistry[id] = { modal };

    modal.addEventListener('click', (event) => {
      const shouldClose = event.target.dataset.modalClose !== undefined || event.target.closest('[data-modal-close]');
      const clickedBackdrop = event.target === modal && modal.dataset.modalAllowBackdropClose !== 'false';
      if (shouldClose || clickedBackdrop) {
        closeModal(modal);
      }
    });

    if (window.OverlayManager) {
      window.OverlayManager.register(id, {
        element: modal,
        backdrop: modal,
        onRequestClose: () => closeModal(modal),
        openClass: getOpenClass(modal),
      });
    }
  }

  function bindOpeners(selector, resolveId) {
    document.querySelectorAll(selector).forEach((trigger) => {
      const modalId = resolveId(trigger);
      if (!modalId) return;
      trigger.addEventListener('click', (event) => {
        if (trigger.tagName === 'A' || trigger.tagName === 'BUTTON' || trigger.tagName === 'INPUT') {
          event.preventDefault();
        }
        const entry = modalRegistry[modalId];
        openModal(entry && entry.modal, trigger);
      });
    });
  }

  function setupKeyboardShortcuts() {
    if (window.OverlayManager) {
      return;
    }
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const openModals = Object.values(modalRegistry)
        .map((entry) => entry.modal)
        .filter((modal) => modal && isOpen(modal));
      const topMost = openModals[openModals.length - 1];
      if (topMost) {
        event.preventDefault();
        closeModal(topMost);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-modal-root]').forEach(register);

    bindOpeners('[data-modal-open]', (trigger) => trigger.getAttribute('data-modal-open'));
    bindOpeners('[data-open-modal]', (trigger) => trigger.getAttribute('data-open-modal'));
    bindOpeners('[data-open-account-modal]', () => 'manage-account');
    bindOpeners('[data-open-login-modal]', () => 'bulletin-login');
    bindOpeners('[data-help-trigger]', () => 'login-help');
    bindOpeners('[data-login-trigger]', () => 'login-panel');

    document.querySelectorAll('[data-modal-close-trigger]').forEach((el) => {
      el.addEventListener('click', () => {
        const targetId = el.getAttribute('data-modal-close-trigger');
        const modal = modalRegistry[targetId] && modalRegistry[targetId].modal;
        closeModal(modal);
      });
    });

    setupKeyboardShortcuts();
  });

  window.ModalHub = { open: (id) => openModal(modalRegistry[id] && modalRegistry[id].modal), close: (id) => closeModal(modalRegistry[id] && modalRegistry[id].modal) };
})();
