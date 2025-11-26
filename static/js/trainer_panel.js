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
    const closedLabel =
      btn.dataset.passportLabelClosed || (labelTarget.textContent || "View Passport").trim();
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

  function updatePanelAlert(root, tone, message) {
    const alertBox = root.querySelector("[data-panel-alert]");
    if (!alertBox) return;
    const baseClass = "trainer-panel-alert";
    if (!message) {
      alertBox.textContent = "";
      alertBox.className = baseClass;
      alertBox.setAttribute("hidden", "");
      return;
    }
    alertBox.textContent = message;
    alertBox.className = tone ? `${baseClass} is-${tone}` : baseClass;
    alertBox.removeAttribute("hidden");
  }

  async function refreshTrainerPanel(root, url) {
    if (!root || !url) return false;
    try {
      const response = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!response.ok) {
        throw new Error("Failed to refresh trainer details.");
      }
      const html = await response.text();
      const temp = document.createElement("div");
      temp.innerHTML = html;
      const nextRoot = temp.querySelector("[data-trainer-panel]");
      if (!nextRoot) {
        throw new Error("Trainer panel markup missing.");
      }
      root.dataset.panelMode = nextRoot.dataset.panelMode || root.dataset.panelMode || "";
      root.dataset.panelRefreshUrl = nextRoot.dataset.panelRefreshUrl || url;
      delete root.dataset.panelFormsInit;
      root.innerHTML = nextRoot.innerHTML;
      initTrainerPanel(root);
      return true;
    } catch (err) {
      console.error("Trainer panel refresh failed:", err);
      return false;
    }
  }

  async function submitPanelForm(root, form) {
    if (!root || !form) return;
    if (typeof form.reportValidity === "function" && !form.reportValidity()) {
      return;
    }
    const method = (form.method || "POST").toUpperCase();
    const submitBtn = form.querySelector("[type='submit']");
    const defaultLabel = submitBtn ? submitBtn.textContent : "";
    const loadingLabel = (submitBtn && submitBtn.dataset.loadingLabel) || "Saving…";
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = loadingLabel;
    }
    updatePanelAlert(root, "info", "Saving changes…");

    try {
      const options = {
        method,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      };
      if (method !== "GET") {
        options.body = new FormData(form);
      }
      const response = await fetch(form.action, options);
      let payload = {};
      try {
        payload = await response.json();
      } catch (jsonError) {
        console.error("Trainer panel JSON parse failed:", jsonError);
      }
      const success = response.ok && payload.success !== false;
      const message =
        payload.message ||
        payload.error ||
        (success ? "Changes saved." : "Unable to save changes. Please try again.");

      if (!success) {
        updatePanelAlert(root, "error", message);
        return;
      }

      const nextUrl = payload.panel_url || root.dataset.panelRefreshUrl || "";
      let refreshed = true;
      if (nextUrl) {
        root.dataset.panelRefreshUrl = nextUrl;
        refreshed = await refreshTrainerPanel(root, nextUrl);
      }

      if (!refreshed) {
        updatePanelAlert(
          root,
          "warning",
          "Saved, but we couldn't refresh the panel automatically. Please reopen the trainer."
        );
        return;
      }

      updatePanelAlert(root, "success", message);
    } catch (err) {
      console.error("Trainer panel form submit failed:", err);
      updatePanelAlert(root, "error", "Unable to save changes. Please try again.");
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = defaultLabel;
      }
    }
  }

  function initModalForms(root) {
    const mode = (root.dataset.panelMode || "").toLowerCase();
    if (mode !== "modal") return;
    if (root.dataset.panelFormsInit === "true") return;
    root.dataset.panelFormsInit = "true";
    const forms = root.querySelectorAll("form");
    forms.forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        submitPanelForm(root, form);
      });
    });
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
    initModalForms(root);
  }

  window.initTrainerPanel = initTrainerPanel;
})();
