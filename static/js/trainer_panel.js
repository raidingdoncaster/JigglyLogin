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
      const rawText = await response.text();
      let payload = {};
      try {
        payload = rawText ? JSON.parse(rawText) : {};
      } catch (jsonError) {
        console.error("Trainer panel JSON parse failed:", jsonError, rawText);
      }

      const success = response.ok && payload.success !== false;
      const fallbackMessage = rawText
        .replace(/<[^>]+>/g, " ")
        .replace(/\s+/g, " ")
        .trim();
      const message =
        payload.message ||
        payload.error ||
        (success ? "Changes saved." : fallbackMessage) ||
        "Unable to save changes. Please try again.";

      if (!success) {
        updatePanelAlert(root, "error", message);
        return;
      }

      const redirectUrl = payload.redirect_url || payload.redirect;
      if (redirectUrl) {
        window.location.href = redirectUrl;
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

  function initAccountDeleteFlow(root) {
    const flow = root.querySelector("[data-delete-flow]");
    if (!flow || flow.dataset.deleteInit === "true") return;
    flow.dataset.deleteInit = "true";

    const steps = {
      intro: flow.querySelector('[data-delete-step="intro"]'),
      password: flow.querySelector('[data-delete-step="password"]'),
      confirm: flow.querySelector('[data-delete-step="confirm"]'),
    };
    const dialog = flow.querySelector("[data-delete-dialog]");
    const dialogTitle = flow.querySelector("[data-delete-dialog-title]");
    const alertBox = flow.querySelector("[data-delete-alert]");
    const continueBtn = flow.querySelector("[data-delete-continue]");
    const verifyBtn = flow.querySelector("[data-delete-verify]");
    const passwordInput = flow.querySelector("[data-delete-password]");
    const tokenInput = flow.querySelector("[data-delete-token]");
    const passwordForm = steps.password && steps.password.tagName === "FORM" ? steps.password : null;
    const verifyUrl = flow.getAttribute("data-delete-verify-url");

    const globalModalOpen = () => {
      const trainerModal = document.getElementById("trainerPanelModal");
      const massStampOverlay = document.getElementById("massStampOverlay");
      return (
        (trainerModal && !trainerModal.hasAttribute("hidden")) ||
        (massStampOverlay && !massStampOverlay.hasAttribute("hidden"))
      );
    };

    const openDialog = () => {
      if (!dialog) return;
      dialog.hidden = false;
      document.body.classList.add("modal-open");
    };

    const closeDialog = () => {
      if (!dialog) return;
      dialog.hidden = true;
      if (!globalModalOpen()) {
        document.body.classList.remove("modal-open");
      }
    };

    const setStep = (target) => {
      Object.entries(steps).forEach(([name, element]) => {
        if (!element) return;
        element.hidden = name !== target;
      });
      if (target === "intro") {
        closeDialog();
      } else {
        openDialog();
        if (dialogTitle) {
          dialogTitle.textContent = target === "confirm" ? "Final confirmation" : "Verify admin access";
        }
      }
    };

    const setAlert = (tone, message) => {
      if (!alertBox) return;
      if (!message) {
        alertBox.textContent = "";
        alertBox.className = "delete-account-alert";
        alertBox.setAttribute("hidden", "");
        return;
      }
      alertBox.textContent = message;
      alertBox.className = tone ? `delete-account-alert is-${tone}` : "delete-account-alert";
      alertBox.removeAttribute("hidden");
    };

    const setButtonLoading = (button, loading) => {
      if (!button) return;
      if (!button.dataset.defaultLabel) {
        button.dataset.defaultLabel = button.textContent || "";
      }
      if (loading) {
        const loadingLabel = button.dataset.loadingLabel || "Working…";
        button.disabled = true;
        button.textContent = loadingLabel;
      } else {
        button.disabled = false;
        button.textContent = button.dataset.defaultLabel || button.textContent;
      }
    };

    setStep("intro");

    if (continueBtn) {
      continueBtn.addEventListener("click", () => {
        setAlert("", "");
        setStep("password");
        if (passwordInput) {
          passwordInput.focus();
        }
      });
    }

    const backButtons = flow.querySelectorAll("[data-delete-back]");
    backButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-delete-back-step") || "intro";
        if (target === "intro" && passwordInput) {
          passwordInput.value = "";
        }
        if (target !== "confirm" && tokenInput) {
          tokenInput.value = "";
        }
        setAlert("", "");
        setStep(target);
        if (target === "password" && passwordInput) {
          passwordInput.focus();
        }
      });
    });

    const closeButtons = flow.querySelectorAll("[data-delete-close]");
    closeButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        setStep("intro");
        setAlert("", "");
      });
    });

    if (passwordForm && verifyBtn) {
      passwordForm.addEventListener("submit", (event) => {
        event.preventDefault();
        verifyBtn.click();
      });
    }

    if (verifyBtn) {
      verifyBtn.addEventListener("click", async () => {
        if (!verifyUrl) {
          setAlert("error", "Verification endpoint missing.");
          return;
        }
        const passwordValue = (passwordInput ? passwordInput.value : "").trim();
        if (!passwordValue) {
          setAlert("error", "Enter the admin dashboard password.");
          if (passwordInput) {
            passwordInput.focus();
          }
          return;
        }
        setButtonLoading(verifyBtn, true);
        setAlert("info", "Verifying admin password…");
        try {
          const response = await fetch(verifyUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
            body: JSON.stringify({ admin_password: passwordValue }),
          });
          let payload = {};
          try {
            payload = await response.json();
          } catch (err) {
            payload = {};
          }
          if (!response.ok || payload.success === false) {
            const msg =
              payload && payload.message
                ? payload.message
                : "Incorrect admin dashboard password.";
            setAlert("error", msg);
            return;
          }
          if (tokenInput) {
            tokenInput.value = payload.delete_token || "";
          }
          if (passwordInput) {
            passwordInput.value = "";
          }
          setAlert("success", "Password verified. Confirm deletion to continue.");
          setStep("confirm");
        } catch (err) {
          console.error("Trainer delete verification failed:", err);
          setAlert("error", "Unable to verify admin password right now.");
        } finally {
          setButtonLoading(verifyBtn, false);
        }
      });
    }

    if (dialog) {
      dialog.addEventListener("click", (evt) => {
        const backdropClicked = evt.target === dialog;
        if (backdropClicked) {
          setStep("intro");
          setAlert("", "");
        }
      });
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
    initModalForms(root);
    initAccountDeleteFlow(root);
  }

  window.initTrainerPanel = initTrainerPanel;
})();
