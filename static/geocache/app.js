const root = document.getElementById("scene-root");

if (!root) {
  throw new Error("Geocache quest root element missing.");
}

const initialPayload = (function () {
  try {
    return JSON.parse(root.dataset.initialState || "{}");
  } catch (error) {
    console.error("Failed to parse geocache initial state:", error);
    return {};
  }
})();

const STORAGE_KEY = "geocache.quest.state.v1";

function loadSavedState() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (error) {
    console.warn("Unable to load quest state:", error);
    return {};
  }
}

function saveState(state) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state || {}));
  } catch (error) {
    console.warn("Unable to save quest state:", error);
  }
}

function clearState() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch (error) {
    console.warn("Unable to clear quest state:", error);
  }
}

const story = initialPayload.story || { scenes: {}, start_scene: null };
const saved = loadSavedState();

const quest = {
  state: {
    view: saved.sceneId ? "scene" : "landing",
    sceneId: saved.sceneId || null,
    history: saved.history || [],
    error: null,
  },
  set(patch) {
    this.state = Object.assign({}, this.state, patch || {});
    render();
  },
};

function getScene(sceneId) {
  return story.scenes ? story.scenes[sceneId] : null;
}

function firstSceneId() {
  if (story.start_scene && getScene(story.start_scene)) {
    return story.start_scene;
  }
  const keys = Object.keys(story.scenes || {});
  return keys.length ? keys[0] : null;
}

function startQuest() {
  const startId = firstSceneId();
  if (!startId) {
    quest.set({
      view: "error",
      error: "No scenes available. Please add content in geocache/story.py.",
    });
    return;
  }
  quest.set({
    view: "scene",
    sceneId: startId,
    history: [startId],
    error: null,
  });
  saveState({ sceneId: startId, history: [startId] });
}

function resumeQuest() {
  if (!quest.state.sceneId) {
    startQuest();
    return;
  }
  quest.set({ view: "scene", error: null });
}

function resetQuest() {
  clearState();
  quest.set({
    view: "landing",
    sceneId: null,
    history: [],
    error: null,
  });
}

function applyOption(option) {
  if (!option) {
    return;
  }
  if (option.action === "reset") {
    resetQuest();
    return;
  }
  if (option.action === "replay") {
    clearState();
    startQuest();
    return;
  }
  if (option.next) {
    const sceneExists = !!getScene(option.next);
    if (!sceneExists) {
      quest.set({
        error: `Scene "${option.next}" is not defined.`,
      });
      return;
    }
    const nextHistory = quest.state.history.concat(option.next);
    quest.set({
      view: "scene",
      sceneId: option.next,
      history: nextHistory,
      error: null,
    });
    saveState({ sceneId: option.next, history: nextHistory });
    return;
  }
}

function renderLanding() {
  const container = createElement("section", { className: "screen" });
  container.appendChild(createElement("h1", { className: "screen__title", text: story.title || "Whispers of the Wild Court" }));
  container.appendChild(
    createElement("p", {
      className: "screen__subtitle",
      text: "A standalone quest experience built directly into the RDAB site. Start fresh or resume from this device.",
    })
  );
  const actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Begin quest", "button button--primary", () => {
      startQuest();
    })
  );
  if (quest.state.history && quest.state.history.length) {
    actions.appendChild(
      createButton("Resume quest", "button button--secondary", () => {
        resumeQuest();
      })
    );
    actions.appendChild(
      createButton("Clear progress", "button button--ghost", () => {
        resetQuest();
      })
    );
  }
  container.appendChild(actions);
  return container;
}

function renderScene() {
  const sceneId = quest.state.sceneId;
  const scene = getScene(sceneId);
  if (!scene) {
    return renderError(`Scene "${sceneId}" is missing. Check geocache/story.py.`);
  }

  const container = createElement("section", { className: "screen story-screen" });
  container.appendChild(
    createElement("p", { className: "story-screen__location", text: `Scene ID: ${sceneId}` })
  );
  container.appendChild(createElement("h1", { className: "story-screen__title", text: scene.title || "Untitled scene" }));
  if (scene.summary) {
    container.appendChild(createElement("p", { className: "story-screen__summary", text: scene.summary }));
  }

  const body = Array.isArray(scene.body) ? scene.body : [];
  body.forEach((paragraph) => {
    container.appendChild(createElement("p", { className: "story-screen__body", text: paragraph }));
  });

  const options = Array.isArray(scene.options) ? scene.options : [];
  if (options.length) {
    const list = createElement("div", { className: "story-screen__options" });
    options.forEach((option) => {
      list.appendChild(
        createButton(option.label || "Continue", "button", () => {
          applyOption(option);
        })
      );
    });
    container.appendChild(list);
  } else {
    const fallback = createElement("div", { className: "story-screen__options" });
    fallback.appendChild(
      createButton("Back to landing", "button button--primary", () => {
        resetQuest();
      })
    );
    container.appendChild(fallback);
  }

  return container;
}

function renderError(message) {
  const container = createElement("section", { className: "screen" });
  container.appendChild(createElement("h1", { className: "screen__title", text: "Something went wrong" }));
  container.appendChild(createElement("p", { className: "screen__message", text: message || "Unknown error." }));
  const actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Back to landing", "button button--primary", () => {
      resetQuest();
    })
  );
  container.appendChild(actions);
  return container;
}

function render() {
  let screen;
  if (quest.state.view === "landing") {
    screen = renderLanding();
  } else if (quest.state.view === "scene") {
    screen = renderScene();
  } else {
    screen = renderError(quest.state.error);
  }

  if (quest.state.error && quest.state.view !== "error") {
    const banner = createElement("div", { className: "screen__error", text: quest.state.error });
    screen.insertBefore(banner, screen.firstChild || null);
  }

  root.innerHTML = "";
  root.appendChild(screen);
}

function createElement(tag, options) {
  const el = document.createElement(tag);
  if (!options) {
    return el;
  }
  if (options.className) {
    el.className = options.className;
  }
  if (typeof options.text === "string") {
    el.textContent = options.text;
  }
  if (typeof options.html === "string") {
    el.innerHTML = options.html;
  }
  if (options.attrs) {
    Object.keys(options.attrs).forEach((key) => {
      const value = options.attrs[key];
      if (value !== undefined && value !== null) {
        el.setAttribute(key, value);
      }
    });
  }
  return el;
}

function createButton(label, className, handler) {
  const button = createElement("button", { className: className || "button", text: label });
  if (typeof handler === "function") {
    button.addEventListener("click", handler);
  }
  return button;
}

render();
