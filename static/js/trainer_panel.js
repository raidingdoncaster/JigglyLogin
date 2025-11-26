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
  }

  window.initTrainerPanel = initTrainerPanel;
})();
