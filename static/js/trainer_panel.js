(function () {
  function toggleSection(toggle, body, box, expand) {
    if (!toggle || !body || !box) return;
    if (expand) {
      body.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      box.classList.add("is-open");
    } else {
      body.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      box.classList.remove("is-open");
    }
  }

  function initPassportDrawer(root) {
    const btn = root.querySelector("[data-passport-load]");
    const panel = root.querySelector("[data-passport-panel]");
    const body = root.querySelector("[data-passport-body]");
    if (!btn || !panel || !body || panel.dataset.passportInit === "true") return;
    panel.dataset.passportInit = "true";

    const closeBtn = panel.querySelector("[data-passport-close]");
    const labelTarget = btn.querySelector("[data-passport-label]") || btn;
    const closedLabel = btn.dataset.passportLabelClosed || (labelTarget.textContent || "View Passport").trim();
    const openLabel = btn.dataset.passportLabelOpen || "Hide Passport";
    const url = btn.dataset.passportUrl;

    let loaded = false;
    let loading = false;

    const setExpanded = (open) => {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (labelTarget) {
        labelTarget.textContent = open ? openLabel : closedLabel;
      }
    };

    const loadPassport = () => {
      if (!url || loaded || loading) return;
      loading = true;
      body.innerHTML = '<p class="passport-drawer__placeholder">Loading passport…</p>';
      fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then((resp) => {
          if (!resp.ok) throw new Error("Failed to load passport");
          return resp.text();
        })
        .then((html) => {
          body.innerHTML = html;
          loaded = true;
        })
        .catch(() => {
          body.innerHTML =
            '<p class="passport-drawer__placeholder passport-drawer__placeholder--error">Unable to load passport right now.</p>';
        })
        .finally(() => {
          loading = false;
        });
    };

    const showPanel = () => {
      panel.hidden = false;
      panel.classList.add("is-open");
      setExpanded(true);
      loadPassport();
    };

    const hidePanel = () => {
      panel.classList.remove("is-open");
      setExpanded(false);
      setTimeout(() => {
        if (!panel.classList.contains("is-open")) {
          panel.hidden = true;
        }
      }, 160);
    };

    btn.addEventListener("click", () => {
      if (panel.hasAttribute("hidden")) {
        showPanel();
      } else {
        hidePanel();
      }
    });

    if (closeBtn) {
      closeBtn.addEventListener("click", hidePanel);
    }
  }

  function initTrainerPanel(root) {
    if (!root) return;
    const boxes = root.querySelectorAll("[data-action-box]");
    boxes.forEach((box) => {
      if (box.dataset.panelInit === "true") return;
      const toggle = box.querySelector("[data-action-toggle]");
      const body = box.querySelector("[data-action-body]");
      if (!toggle || !body) return;

      toggleSection(toggle, body, box, false);
      toggle.addEventListener("click", () => {
        const expanded = toggle.getAttribute("aria-expanded") === "true";
        toggleSection(toggle, body, box, !expanded);
      });

      box.dataset.panelInit = "true";
    });

    initPassportDrawer(root);
  }

  window.initTrainerPanel = initTrainerPanel;
})();
