const root = document.getElementById("scene-root");

if (!root) {
  console.error("Geocache quest root element missing.");
  throw new Error("Geocache quest root element missing.");
}

const initialPayload = (() => {
    try {
      return JSON.parse(root.dataset.initialState || "{}");
    } catch (err) {
      console.error("Failed to parse initial quest state:", err);
      return {};
    }
  })();

  class APIError extends Error {
    constructor(message, payload, status) {
      super(message);
      this.payload = payload || {};
      this.status = status || 500;
    }
  }

  const supportsLocalStorage = (() => {
    try {
      const key = "__geocache_test__";
      window.localStorage.setItem(key, "1");
      window.localStorage.removeItem(key);
      return true;
    } catch (err) {
      console.warn("Local storage unavailable:", err);
      return false;
    }
  })();

  const supportsSessionStorage = (() => {
    try {
      const key = "__geocache_session_test__";
      window.sessionStorage.setItem(key, "1");
      window.sessionStorage.removeItem(key);
      return true;
    } catch (err) {
      console.warn("Session storage unavailable:", err);
      return false;
    }
  })();

  const STORAGE_KEY = "geocacheQuestState.v1";
  const PIN_KEY = "geocacheQuestPin";

  const storage = {
    load() {
      if (!supportsLocalStorage) {
        return {};
      }
      try {
        const raw = window.localStorage.getItem(STORAGE_KEY);
        if (!raw) {
          return {};
        }
        const parsed = JSON.parse(raw);
        return {
          profile: parsed.profile || null,
          session: parsed.session || null,
        };
      } catch (err) {
        console.warn("Failed to load quest state:", err);
        return {};
      }
    },
    save({ profile, session }) {
      if (!supportsLocalStorage) {
        return;
      }
      try {
        window.localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({
            profile: profile || null,
            session: session || null,
          })
        );
      } catch (err) {
        console.warn("Failed to persist quest state:", err);
      }
    },
    clear() {
      if (!supportsLocalStorage) {
        return;
      }
      try {
        window.localStorage.removeItem(STORAGE_KEY);
      } catch (err) {
        console.warn("Failed to clear quest state:", err);
      }
    },
  };

  const pinVault = {
    remember(pin) {
      if (!supportsSessionStorage) {
        return;
      }
      try {
        window.sessionStorage.setItem(PIN_KEY, pin);
      } catch (err) {
        console.warn("Failed to store PIN in session storage:", err);
      }
    },
    get() {
      if (!supportsSessionStorage) {
        return null;
      }
      try {
        return window.sessionStorage.getItem(PIN_KEY);
      } catch (err) {
        console.warn("Failed to read PIN from session storage:", err);
        return null;
      }
    },
    clear() {
      if (!supportsSessionStorage) {
        return;
      }
      try {
        window.sessionStorage.removeItem(PIN_KEY);
      } catch (err) {
        console.warn("Failed to clear PIN:", err);
      }
    },
  };

  const LOGIN_MAX_ATTEMPTS = 5;
  const LOGIN_LOCKOUT_SECONDS = 1800;
  const SIGNIN_GUARD_KEY = "geocacheSigninGuard.v1";

  const signinGuard = (() => {
    const defaults = {
      remaining: LOGIN_MAX_ATTEMPTS,
      lockUntil: null,
    };

    const load = () => {
      if (!supportsLocalStorage) {
        return { ...defaults };
      }
      try {
        const raw = window.localStorage.getItem(SIGNIN_GUARD_KEY);
        if (!raw) {
          return { ...defaults };
        }
        const parsed = JSON.parse(raw);
        const remaining = Number.isFinite(parsed?.remaining)
          ? Math.max(0, parseInt(parsed.remaining, 10))
          : defaults.remaining;
        const lockUntil =
          typeof parsed?.lockUntil === "number" && parsed.lockUntil > 0
            ? parsed.lockUntil
            : null;
        return {
          remaining: remaining || defaults.remaining,
          lockUntil,
        };
      } catch (err) {
        console.warn("Failed to load signin guard:", err);
        return { ...defaults };
      }
    };

    let internal = load();

    const persist = () => {
      if (!supportsLocalStorage) {
        return;
      }
      try {
        window.localStorage.setItem(SIGNIN_GUARD_KEY, JSON.stringify(internal));
      } catch (err) {
        console.warn("Failed to persist signin guard:", err);
      }
    };

    const now = () => Date.now();

    const clearIfExpired = () => {
      if (internal.lockUntil && internal.lockUntil <= now()) {
        internal = { ...defaults };
        persist();
      }
    };

    const secondsRemaining = () => {
      if (!internal.lockUntil) {
        return 0;
      }
      const delta = internal.lockUntil - now();
      return delta > 0 ? Math.ceil(delta / 1000) : 0;
    };

    return {
      getState() {
        clearIfExpired();
        return { ...internal };
      },
      reset() {
        internal = { ...defaults };
        persist();
        return { ...internal };
      },
      recordSuccess() {
        return this.reset();
      },
      recordFailure() {
        clearIfExpired();
        const remaining = Math.max((internal.remaining || defaults.remaining) - 1, 0);
        internal.remaining = remaining;
        if (remaining <= 0) {
          internal.lockUntil = now() + LOGIN_LOCKOUT_SECONDS * 1000;
          internal.remaining = defaults.remaining;
        }
        persist();
        return {
          ...internal,
          waitSeconds: secondsRemaining(),
        };
      },
      check() {
        clearIfExpired();
        const waitSeconds = secondsRemaining();
        if (waitSeconds > 0) {
          return {
            allowed: false,
            waitSeconds,
            remaining: internal.remaining,
          };
        }
        return {
          allowed: true,
          waitSeconds: 0,
          remaining: internal.remaining,
        };
      },
    };
  })();

  const createEmptySignup = () => ({
    trainer_name: "",
    detected_name: "",
    pin: "",
    memorable: "",
    age_band: null,
    campfire_name: "",
    campfire_opt_out: false,
  });

  const apiRequest = async (url, options = {}) => {
    const isFormData = options.body instanceof FormData;
    const baseHeaders = options.headers || {};
    const headers = isFormData
      ? baseHeaders
      : {
          "Content-Type": "application/json",
          ...baseHeaders,
        };
    const opts = {
      headers,
      ...options,
    };
    if (opts.body && !isFormData && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
    }

    const response = await fetch(url, opts);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_err) {
      payload = null;
    }

    if (!response.ok) {
      const message =
        (payload && (payload.detail || payload.error)) ||
        `Request failed (${response.status})`;
      throw new APIError(message, payload, response.status);
    }

    return payload || {};
  };

  const messageFromError = (error) => {
    if (!error) {
      return "Unexpected error.";
    }
    if (error instanceof APIError) {
      return (
        error.payload?.detail ||
        error.payload?.error ||
        error.message ||
        "Request failed."
      );
    }
    if (error instanceof Error) {
      return error.message || "Unexpected error.";
    }
    return String(error);
  };

  const resolveAssetUrl = (path) => {
    if (!path || typeof path !== "string") {
      return null;
    }
    if (/^https?:\/\//i.test(path)) {
      return path;
    }
    const normalized = path.replace(/^\/+/, "");
    return `/static/${normalized}`;
  };

  const saved = storage.load();
  const savedPin = pinVault.get();

  const REQUIRED_FLAGS = {
    2: ["compass_found", "compass_repaired"],
    3: [
      "miners_riddle_solved",
      "sigil_dawn_recovered",
      "focus_test_passed",
      "sigil_roots_recovered",
      "oracle_mood_profiled",
    ],
    6: [
      "market_returned",
      "pink_bike_check",
      "illusion_battle_won",
      "sir_nigel_check_in",
      "sigil_might_recovered",
      "order_defeated",
    ],
  };

  const applySessionPayload = (payload, overrides = {}) => {
    if (!payload) {
      return;
    }
    storage.save({
      profile: payload.profile,
      session: payload.session,
    });
    quest.set({
      profile: payload.profile || quest.state.profile,
      session: payload.session || quest.state.session,
      signinForm: {
        ...quest.state.signinForm,
        trainer_name:
          (payload.profile && payload.profile.trainer_name) ||
          quest.state.signinForm.trainer_name ||
          "",
        pin: "",
      },
      signup: createEmptySignup(),
      ...overrides,
    });
  };

  const ensureProfile = () => {
    const { profile } = quest.state;
    if (!profile) {
      quest.set({
        view: "landing",
        error: "No quest profile found. Begin the quest to get started.",
      });
      return null;
    }
    return profile;
  };

  const ensureActivePin = () => {
    const { profile } = quest.state;
    const activePin = quest.state.pin || pinVault.get();
    if (!profile) {
      quest.set({
        view: "landing",
        error: "No quest profile found. Begin the quest to get started.",
      });
      return null;
    }
    if (!activePin) {
      quest.set({
        view: "signin",
        signinForm: {
          ...quest.state.signinForm,
          trainer_name: profile?.trainer_name || quest.state.signinForm.trainer_name || "",
          pin: "",
        },
        error: "Enter your 4-digit quest PIN to continue.",
      });
      return null;
    }
    return activePin;
  };

  const postSessionUpdate = async ({ state, event, reset } = {}, { keepView = false } = {}) => {
    const profile = ensureProfile();
    const activePin = ensureActivePin();
    if (!profile || !activePin) {
      return null;
    }

    quest.set({ busy: true, error: null });
    try {
      const response = await apiRequest("/geocache/session", {
        method: "POST",
        body: {
          profile_id: profile.id,
          pin: activePin,
          state,
          event,
          reset: Boolean(reset),
        },
      });
      applySessionPayload(response, {
        busy: false,
        error: null,
        pin: activePin,
        view: keepView ? quest.state.view : "act",
      });
      return response;
    } catch (error) {
      if (error instanceof APIError && error.status === 401) {
        pinVault.clear();
        const retryName = quest.state.profile?.trainer_name || profile.trainer_name;
        quest.set({
          busy: false,
          pin: null,
          view: "signin",
          signinForm: {
            trainer_name: retryName,
            pin: "",
          },
          error: "PIN incorrect or expired. Please re-enter to continue.",
        });
      } else {
        quest.set({
          busy: false,
          error: messageFromError(error),
        });
      }
      throw error;
    }
  };

  const postMinigame = async (endpoint, body = {}) => {
    const profile = ensureProfile();
    const activePin = ensureActivePin();
    if (!profile || !activePin) {
      return null;
    }

    quest.set({ busy: true, error: null });
    try {
      const response = await apiRequest(endpoint, {
        method: "POST",
        body: {
          profile_id: profile.id,
          pin: activePin,
          ...body,
        },
      });
      applySessionPayload(response, {
        busy: false,
        error: null,
        pin: activePin,
        view: "act",
      });
      return response;
    } catch (error) {
      if (error instanceof APIError && error.status === 401) {
        pinVault.clear();
        const retryName = quest.state.profile?.trainer_name || profile.trainer_name;
        quest.set({
          busy: false,
          pin: null,
          view: "signin",
          signinForm: {
            trainer_name: retryName,
            pin: "",
          },
          error: "PIN incorrect or expired. Please re-enter to continue.",
        });
      } else {
        quest.set({
          busy: false,
          error: messageFromError(error),
        });
      }
      throw error;
    }
  };

  const quest = {
    state: {
      view: "loading",
      status: null,
      story: initialPayload.story || null,
      profile: saved.profile || null,
      session: saved.session || null,
      pin: savedPin || null,
      busy: false,
      error: null,
      signinForm: {
        trainer_name: saved.profile?.trainer_name || "",
        pin: "",
      },
      signup: createEmptySignup(),
    },
    set(patch) {
      this.state = {
        ...this.state,
        ...patch,
      };
      render();
    },
  };

  const hud = {
    container: null,
    header: null,
    overviewButton: null,
    settingsButton: null,
    actTitle: null,
    actStatus: null,
    progressFill: null,
    canvas: null,
    canvasLayer: null,
    canvasCharacter: null,
    canvasContent: null,
    actions: null,
    overview: null,
    overviewBody: null,
    overviewClose: null,
    settingsSheet: null,
    settingsClose: null,
    settingsActions: null,
  };

  const resetHudRefs = () => {
    Object.keys(hud).forEach((key) => {
      hud[key] = null;
    });
  };

  const teardownHud = () => {
    resetHudRefs();
  };

  const ensureHudStructure = () => {
    if (hud.container) {
      return;
    }

    teardownHud();
    root.innerHTML = "";

    const container = document.createElement("div");
    container.className = "hud";

    const header = document.createElement("header");
    header.className = "hud__top";

    const overviewButton = document.createElement("button");
    overviewButton.type = "button";
    overviewButton.className = "hud__top-button hud__top-button--overview";
    overviewButton.setAttribute("aria-label", "Quest overview");
    overviewButton.textContent = "☰";

    const actMeta = document.createElement("div");
    actMeta.className = "hud__act-meta";

    const actTitle = document.createElement("h1");
    actTitle.className = "hud__act-title";
    actTitle.textContent = "Quest";

    const actStatus = document.createElement("p");
    actStatus.className = "hud__act-status";
    actStatus.textContent = "Act progress";

    const progressBar = document.createElement("div");
    progressBar.className = "hud__progress-bar";

    const progressFill = document.createElement("span");
    progressFill.className = "hud__progress-fill";
    progressBar.appendChild(progressFill);

    actMeta.appendChild(actTitle);
    actMeta.appendChild(actStatus);
    actMeta.appendChild(progressBar);

    const settingsButton = document.createElement("button");
    settingsButton.type = "button";
    settingsButton.className = "hud__top-button hud__top-button--settings";
    settingsButton.setAttribute("aria-label", "Quest settings");
    settingsButton.textContent = "⚙";

    header.appendChild(overviewButton);
    header.appendChild(actMeta);
    header.appendChild(settingsButton);

    const canvas = document.createElement("div");
    canvas.className = "hud__canvas";

    const canvasLayer = document.createElement("div");
    canvasLayer.className = "hud__canvas-layer";
    canvasLayer.dataset.layer = "background";
    canvasLayer.dataset.asset = "";

    const canvasCharacter = document.createElement("div");
    canvasCharacter.className = "hud__canvas-character";
    canvasCharacter.dataset.layer = "character";
    canvasCharacter.dataset.asset = "";

    const canvasContent = document.createElement("div");
    canvasContent.className = "hud__canvas-content";

    canvas.appendChild(canvasLayer);
    canvas.appendChild(canvasCharacter);
    canvas.appendChild(canvasContent);

    const actions = document.createElement("footer");
    actions.className = "hud__actions";

    const overviewOverlay = document.createElement("div");
    overviewOverlay.className = "hud__overlay";
    overviewOverlay.dataset.overlay = "overview";

    const overlayPanel = document.createElement("div");
    overlayPanel.className = "hud__overlay-panel";

    const overlayClose = document.createElement("button");
    overlayClose.type = "button";
    overlayClose.className = "hud__overlay-close";
    overlayClose.setAttribute("aria-label", "Close overview");
    overlayClose.textContent = "×";

    const overlayBody = document.createElement("div");
    overlayBody.className = "hud__overlay-body";

    overlayPanel.appendChild(overlayClose);
    overlayPanel.appendChild(overlayBody);
    overviewOverlay.appendChild(overlayPanel);

    const settingsSheet = document.createElement("div");
    settingsSheet.className = "hud__sheet";
    settingsSheet.dataset.sheet = "settings";

    const sheetHeader = document.createElement("div");
    sheetHeader.className = "hud__sheet-header";

    const sheetTitle = document.createElement("h2");
    sheetTitle.className = "hud__sheet-title";
    sheetTitle.textContent = "Quest Settings";

    const sheetClose = document.createElement("button");
    sheetClose.type = "button";
    sheetClose.className = "hud__overlay-close hud__overlay-close--sheet";
    sheetClose.setAttribute("aria-label", "Close settings");
    sheetClose.textContent = "×";

    sheetHeader.appendChild(sheetTitle);
    sheetHeader.appendChild(sheetClose);

    const sheetActions = document.createElement("div");
    sheetActions.className = "hud__sheet-actions";

    settingsSheet.appendChild(sheetHeader);
    settingsSheet.appendChild(sheetActions);

    container.appendChild(header);
    container.appendChild(canvas);
    container.appendChild(actions);
    container.appendChild(overviewOverlay);
    container.appendChild(settingsSheet);

    root.appendChild(container);

    hud.container = container;
    hud.header = header;
    hud.overviewButton = overviewButton;
    hud.settingsButton = settingsButton;
    hud.actTitle = actTitle;
    hud.actStatus = actStatus;
    hud.progressFill = progressFill;
    hud.canvas = canvas;
    hud.canvasLayer = canvasLayer;
    hud.canvasCharacter = canvasCharacter;
    hud.canvasContent = canvasContent;
    hud.actions = actions;
    hud.overview = overviewOverlay;
    hud.overviewBody = overlayBody;
    hud.overviewClose = overlayClose;
    hud.settingsSheet = settingsSheet;
    hud.settingsClose = sheetClose;
    hud.settingsActions = sheetActions;

    const closeOverview = () => {
      overviewOverlay.classList.remove("is-open");
    };

    const openOverview = () => {
      overviewOverlay.classList.add("is-open");
    };

    const closeSettings = () => {
      settingsSheet.classList.remove("is-open");
    };

    const openSettings = () => {
      settingsSheet.classList.add("is-open");
    };

    overviewButton.addEventListener("click", () => {
      if (overviewOverlay.classList.contains("is-open")) {
        closeOverview();
      } else {
        openOverview();
      }
    });

    overlayClose.addEventListener("click", closeOverview);
    overviewOverlay.addEventListener("click", (event) => {
      if (event.target === overviewOverlay) {
        closeOverview();
      }
    });

    settingsButton.addEventListener("click", () => {
      if (settingsSheet.classList.contains("is-open")) {
        closeSettings();
      } else {
        openSettings();
      }
    });

    sheetClose.addEventListener("click", closeSettings);
  };

  const setHudActions = (nodes = [], { align = "space-between" } = {}) => {
    if (!hud.actions) {
      return;
    }
    hud.actions.innerHTML = "";
    hud.actions.classList.toggle("hud__actions--center", align === "center");
    nodes.forEach((node) => {
      hud.actions.appendChild(node);
    });
  };

  const createHexButton = (label, onClick, { disabled = false } = {}) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "hud__hex-button";
    btn.textContent = label;
    btn.disabled = disabled || Boolean(quest.state.busy);
    btn.addEventListener("click", () => {
      if (btn.disabled || quest.state.busy) {
        return;
      }
      if (typeof onClick === "function") {
        onClick();
      }
    });
    return btn;
  };

  const createPrimaryButton = (label, onClick, { disabled = false } = {}) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "hud__primary-button";
    btn.textContent = label;
    btn.disabled = disabled || Boolean(quest.state.busy);
    btn.addEventListener("click", () => {
      if (btn.disabled || quest.state.busy) {
        return;
      }
      if (typeof onClick === "function") {
        onClick();
      }
    });
    return btn;
  };

  const updateHudTopBar = (context) => {
    if (!hud.actTitle) {
      return;
    }

    const ctx = context || buildQuestContext();
    const story = ctx.story || {};
    const acts = ctx.acts || [];
    const activeAct = ctx.activeAct || acts[ctx.actIndex] || acts[0] || null;

    hud.actTitle.textContent = activeAct?.title || story.title || "Quest";

    if (hud.actStatus) {
      hud.actStatus.textContent =
        acts.length > 0
          ? `Act ${Math.min(ctx.actIndex + 1, acts.length)} of ${acts.length}`
          : "Act progress";
    }

    if (hud.progressFill) {
      const completedActs = Math.max(ctx.actIndex, 0);
      const progress =
        acts.length > 0
          ? ((completedActs + (ctx.currentScene ? 1 : 0)) / acts.length) * 100
          : 0;
      hud.progressFill.style.width = `${Math.min(100, Math.max(8, progress))}%`;
    }
  };

  const renderOverviewContent = (context) => {
    if (!hud.overviewBody) {
      return;
    }
    const body = hud.overviewBody;
    body.innerHTML = "";

    const ctx = context || buildQuestContext();
    const story = ctx.story || {};
    const profile = ctx.profile || {};
    const session = ctx.session || {};
    const acts = ctx.acts || [];
    const activeActId = ctx.activeActId;

    const heading = document.createElement("h2");
    heading.textContent = "Quest Progress";
    body.appendChild(heading);

    const campfireSource =
      (profile.campfire_name && profile.campfire_name) ||
      (profile.metadata && profile.metadata.campfire_username) ||
      null;
    const campfireDisplay =
      campfireSource && campfireSource.toLowerCase() !== "not on campfire"
        ? campfireSource
        : "Not linked";

    const lastUpdateRaw =
      session.updated_at ||
      session.created_at ||
      (profile.metadata && profile.metadata.last_login_at) ||
      null;
    const lastUpdateDisplay = lastUpdateRaw
      ? new Date(lastUpdateRaw).toLocaleString()
      : "Not started";

    const stats = document.createElement("div");
    stats.className = "hud-overview-stats";
    stats.innerHTML = `
      <p><strong>Trainer:</strong> ${profile.trainer_name || "Unknown"}</p>
      <p><strong>Campfire:</strong> ${campfireDisplay}</p>
      <p><strong>Last Act Update:</strong> ${lastUpdateDisplay}</p>
    `;
    body.appendChild(stats);

    if (acts.length) {
      const list = document.createElement("ul");
      list.className = "hud-overview-acts";
      acts.forEach((act, idx) => {
        const item = document.createElement("li");
        item.className = "hud-overview-act";
        if (act.id === activeActId) {
          item.classList.add("is-active");
        } else if (ctx.actIndex >= 0 && idx < ctx.actIndex) {
          item.classList.add("is-complete");
        }
        item.innerHTML = `
          <span class="hud-overview-act__index">Act ${idx + 1}</span>
          <span class="hud-overview-act__title">${act.title}</span>
        `;
        list.appendChild(item);
      });
      body.appendChild(list);
    }
  };

  const renderSettingsActions = () => {
    if (!hud.settingsActions) {
      return;
    }
    const busy = Boolean(quest.state.busy);
    hud.settingsActions.innerHTML = "";

    const makeButton = (label, onClick) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = label;
      btn.disabled = busy;
      btn.addEventListener("click", onClick);
      return btn;
    };

    const closeSheet = () => {
      if (hud.settingsSheet) {
        hud.settingsSheet.classList.remove("is-open");
      }
    };

    const reloadBtn = makeButton("Reload save slot", () => {
      closeSheet();
      quest.set({
        view: quest.state.profile ? "resume" : "landing",
        error: null,
      });
    });

    const refreshBtn = makeButton("Check for quest updates", () => {
      closeSheet();
      refreshSession();
    });

    const logoutBtn = makeButton("Log out", () => {
      closeSheet();
      storage.clear();
      pinVault.clear();
      quest.set({
        profile: null,
        session: null,
        pin: null,
        view: "landing",
        error: null,
      });
    });

    hud.settingsActions.appendChild(reloadBtn);
    hud.settingsActions.appendChild(refreshBtn);
    hud.settingsActions.appendChild(logoutBtn);
  };

  const parseActNumber = (actId) => {
    if (!actId) {
      return null;
    }
    const match = String(actId).match(/act\s*(\d+)/i);
    if (!match) {
      return null;
    }
    const value = parseInt(match[1], 10);
    return Number.isNaN(value) ? null : value;
  };

  const buildQuestContext = () => {
    const story = quest.state.story || {};
    const session = quest.state.session || {};
    const profile = quest.state.profile || null;

    const acts = Array.isArray(story.acts) ? story.acts : [];
    const sceneMap = story.scenes || {};

    let activeActId = null;
    if (session.current_act) {
      activeActId = `act${session.current_act}`;
    } else if (acts.length) {
      activeActId = acts[0]?.id || null;
    }

    let actIndex = 0;
    if (activeActId) {
      const matched = acts.findIndex((act) => act.id === activeActId);
      if (matched >= 0) {
        actIndex = matched;
      }
    }

    const activeAct = acts[actIndex] || null;
    const sceneIds = Array.isArray(activeAct?.scenes) ? activeAct.scenes : [];

    const scenes = sceneIds
      .map((id) => {
        const source = sceneMap[id];
        if (!source) {
          return null;
        }
        return {
          id,
          act_id: activeAct?.id || null,
          ...source,
        };
      })
      .filter(Boolean);

    let sceneIndex = 0;
    if (session.last_scene) {
      const matched = scenes.findIndex((scene) => scene.id === session.last_scene);
      if (matched >= 0) {
        sceneIndex = matched;
      }
    }

    const currentScene = scenes[sceneIndex] || null;
    const prevScene = sceneIndex > 0 ? scenes[sceneIndex - 1] : null;
    const nextScene = sceneIndex < scenes.length - 1 ? scenes[sceneIndex + 1] : null;
    const nextAct = actIndex < acts.length - 1 ? acts[actIndex + 1] : null;

    return {
      story,
      session,
      profile,
      acts,
      activeAct,
      activeActId,
      actIndex,
      scenes,
      sceneIndex,
      currentScene,
      prevScene,
      nextScene,
      nextAct,
    };
  };

  const navigateToScene = (context, targetScene, options = {}) => {
    if (!targetScene) {
      return;
    }
    const updates = {
      last_scene: targetScene.id,
    };
    const targetActId = options.actId || targetScene.act_id || context.activeActId;
    const actNumber = parseActNumber(targetActId);
    if (
      actNumber &&
      quest.state.session &&
      quest.state.session.current_act !== actNumber
    ) {
      updates.current_act = actNumber;
    }
    postSessionUpdate(
      {
        state: updates,
      },
      { keepView: true }
    );
  };

  const navigateToActStart = (context, targetAct) => {
    if (!targetAct) {
      return;
    }
    const actNumber = parseActNumber(targetAct.id);
    const sceneIds = Array.isArray(targetAct.scenes) ? targetAct.scenes : [];
    const firstSceneId = sceneIds[0];
    const updates = {};
    if (firstSceneId) {
      updates.last_scene = firstSceneId;
    }
    if (actNumber) {
      updates.current_act = actNumber;
    }
    if (Object.keys(updates).length === 0) {
      return;
    }
    postSessionUpdate(
      {
        state: updates,
      },
      { keepView: true }
    );
  };

  const getSceneMode = (scene) => {
    if (!scene) {
      return "empty";
    }
    const sceneType = (scene.type || "").toLowerCase();
    if (sceneType === "minigame") {
      const kind = (scene.minigame?.kind || scene.kind || "").toLowerCase();
      if (["location", "checkin"].includes(kind)) {
        return "location";
      }
      if (["artifact_scan", "sigil", "scan"].includes(kind)) {
        return "artifact";
      }
      if (["quiz", "riddle", "focus", "mosaic"].includes(kind)) {
        return "activity";
      }
      return "activity";
    }
    if (sceneType === "celebration" || sceneType === "reward") {
      return "celebration";
    }
    return "dialogue";
  };

  const applySceneBackground = (scene, fallbackAct) => {
    if (!hud.canvasLayer) {
      return;
    }
    const backgroundAsset =
      scene?.background ||
      scene?.art ||
      scene?.backdrop ||
      fallbackAct?.background ||
      fallbackAct?.art ||
      null;
    const imageUrl = resolveAssetUrl(backgroundAsset);
    const current = hud.canvasLayer.dataset.asset || "";

    if (imageUrl) {
      if (imageUrl !== current) {
        hud.canvasLayer.classList.remove("is-visible");
        window.requestAnimationFrame(() => {
          hud.canvasLayer.style.backgroundImage = `url(${imageUrl})`;
          hud.canvasLayer.dataset.asset = imageUrl;
          window.requestAnimationFrame(() => {
            hud.canvasLayer.classList.add("is-visible");
          });
        });
      } else if (!hud.canvasLayer.classList.contains("is-visible")) {
        hud.canvasLayer.classList.add("is-visible");
      }
    } else {
      hud.canvasLayer.dataset.asset = "";
      hud.canvasLayer.style.backgroundImage = "none";
      hud.canvasLayer.classList.remove("is-visible");
    }
  };

  const applySceneCharacter = (context) => {
    if (!hud.canvasCharacter) {
      return;
    }
    const scene = context.currentScene;
    const minigame = scene?.minigame || {};

    const characterConfig =
      scene?.character ||
      minigame?.character ||
      context.activeAct?.character ||
      {};

    const sources = [
      characterConfig.asset,
      characterConfig.image,
      characterConfig.src,
      characterConfig.url,
      scene?.character_asset,
      scene?.character_image,
      minigame?.character_asset,
      minigame?.character_image,
      context.activeAct?.character_asset,
      context.activeAct?.character_image,
    ];

    const asset = sources.find((value) => typeof value === "string" && value.trim()) || null;
    const side =
      (characterConfig.side ||
        scene?.character_side ||
        minigame?.character_side ||
        context.activeAct?.character_side ||
        "right")
        .toString()
        .toLowerCase();

    const imageUrl = resolveAssetUrl(asset);
    const element = hud.canvasCharacter;
    const current = element.dataset.asset || "";

    if (imageUrl) {
      if (imageUrl !== current) {
        element.classList.remove("is-visible");
        window.requestAnimationFrame(() => {
          element.style.backgroundImage = `url(${imageUrl})`;
          element.dataset.asset = imageUrl;
          window.requestAnimationFrame(() => {
            element.classList.add("is-visible");
          });
        });
      } else if (!element.classList.contains("is-visible")) {
        element.classList.add("is-visible");
      }
      element.classList.toggle("is-left", side === "left" || side === "west");
    } else {
      element.dataset.asset = "";
      element.style.backgroundImage = "none";
      element.classList.remove("is-visible");
      element.classList.remove("is-left");
    }
  };

  const buildEmptyScene = (context) => {
    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--empty";

    const message = document.createElement("p");
    message.className = "hud-scene__placeholder";
    if (context.activeAct?.intro) {
      message.textContent = context.activeAct.intro;
    } else {
      message.textContent =
        "Trainer, the Wild Court is preparing the next chapter. Check for quest updates soon.";
    }
    container.appendChild(message);

    return container;
  };

  const renderDialogueScene = (context) => {
    const scene = context.currentScene;
    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--dialogue";

    if (scene.subtitle) {
      const subtitle = document.createElement("p");
      subtitle.className = "hud-scene__subtitle";
      subtitle.textContent = scene.subtitle;
      container.appendChild(subtitle);
    }

    const dialogueBox = document.createElement("div");
    dialogueBox.className = "hud-dialogue";

    const speakerLabel = document.createElement("div");
    speakerLabel.className = "hud-dialogue__speaker";
    speakerLabel.textContent = scene.speaker || "Narrator";
    dialogueBox.appendChild(speakerLabel);

    const textWrapper = document.createElement("div");
    textWrapper.className = "hud-dialogue__text";
    const lines = Array.isArray(scene.text) ? scene.text : [scene.text || ""];
    lines
      .filter((line) => Boolean(line && line.trim()))
      .forEach((line) => {
        const paragraph = document.createElement("p");
        paragraph.textContent = line;
        textWrapper.appendChild(paragraph);
      });
    if (!textWrapper.children.length) {
      const paragraph = document.createElement("p");
      paragraph.textContent =
        "The Wild Court observes in silence, awaiting your next move.";
      textWrapper.appendChild(paragraph);
    }
    dialogueBox.appendChild(textWrapper);
    container.appendChild(dialogueBox);

    const actions = [];
    if (context.prevScene) {
      actions.push(
        createHexButton("←", () => navigateToScene(context, context.prevScene))
      );
    }

    if (context.nextScene) {
      actions.push(
        createHexButton("→", () => navigateToScene(context, context.nextScene))
      );
    } else if (context.nextAct) {
      actions.push(
        createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct))
      );
    } else {
      actions.push(
        createPrimaryButton("Check for updates", () => {
          refreshSession();
        })
      );
    }

    return {
      node: container,
      actions,
      actionsAlign: actions.length === 1 ? "center" : "space-between",
    };
  };

  const renderActivityScene = (context) => {
    const scene = context.currentScene;
    const minigame = scene?.minigame || {};
    const kind = (minigame.kind || scene?.kind || "").toLowerCase();
    const flagKey = minigame.success_flag || scene?.success_flag || scene?.id;

    if (!flagKey) {
      return null;
    }

    if (!["quiz", "riddle", "mosaic"].includes(kind)) {
      return null;
    }

    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--activity";

    const card = document.createElement("div");
    card.className = "hud-card hud-card--activity";

    const title = document.createElement("h2");
    title.className = "hud-card__title";
    title.textContent =
      scene.title || minigame.title || (kind === "mosaic" ? "Puzzle challenge" : "Quest activity");
    card.appendChild(title);

    if (minigame.question) {
      const subtitle = document.createElement("p");
      subtitle.className = "hud-card__subtitle";
      subtitle.textContent = minigame.question;
      card.appendChild(subtitle);
    }

    const description = document.createElement("p");
    description.className = "hud-card__body";
    description.textContent =
      (Array.isArray(scene.text) ? scene.text[0] : scene.text) ||
      minigame.prompt ||
      "Solve this to advance the quest.";
    card.appendChild(description);

    let statusLine = null;

    if (kind === "mosaic") {
      statusLine = document.createElement("p");
      statusLine.className = "hud-card__meta";
      statusLine.textContent = "Complete the on-site puzzle, then confirm below.";
      card.appendChild(statusLine);

      const submitButton = document.createElement("button");
      submitButton.type = "button";
      submitButton.className = "hud-card__cta";
      submitButton.textContent = "Mark puzzle complete";
      submitButton.addEventListener("click", async () => {
        if (quest.state.busy) {
          return;
        }
        submitButton.disabled = true;
        try {
          await postMinigame("/geocache/minigame/mosaic", {
            puzzle_id: minigame.puzzle_id || scene.id,
            success_flag: flagKey,
            scene_id: scene.id,
            success_token: crypto?.randomUUID
              ? crypto.randomUUID()
              : `token-${Date.now()}`,
          });
        } catch (_) {
          submitButton.disabled = false;
        }
      });
      card.appendChild(submitButton);
    } else {
      const choices = Array.isArray(minigame.choices) ? minigame.choices : [];
      if (!choices.length) {
        return null;
      }

      const choiceGrid = document.createElement("div");
      choiceGrid.className = "hud-choice-grid";

      statusLine = document.createElement("p");
      statusLine.className = "hud-card__error";
      statusLine.style.display = "none";

      choices.forEach((choice, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "hud-choice-button";
        button.textContent =
          choice.label ||
          choice.text ||
          `${String.fromCharCode(65 + index)}. ${choice.id || "Option"}`;
        button.addEventListener("click", async () => {
          if (quest.state.busy) {
            return;
          }
          if (!choice.correct) {
            statusLine.textContent =
              minigame.failure_message || "That answer doesn't unlock the cache. Try again.";
            statusLine.style.display = "block";
            return;
          }

          statusLine.style.display = "none";
          button.disabled = true;
          try {
            await postSessionUpdate(
              {
                state: {
                  progress_flags: {
                    [flagKey]: {
                      status: "solved",
                      choice_id: choice.id || choice.value || `choice-${index}`,
                      validated_at: new Date().toISOString(),
                    },
                  },
                  last_scene: scene.id,
                },
                event: {
                  event_type: "activity_solved",
                  payload: {
                    scene_id: scene.id,
                    choice_id: choice.id || choice.value || `choice-${index}`,
                    kind,
                  },
                },
              },
              { keepView: true }
            );
          } catch (_) {
            button.disabled = false;
          }
        });
        choiceGrid.appendChild(button);
      });

      card.appendChild(choiceGrid);
      card.appendChild(statusLine);
    }

    container.appendChild(card);

    const actions = [];
    if (context.prevScene) {
      actions.push(createHexButton("←", () => navigateToScene(context, context.prevScene)));
    }
    if (context.nextScene) {
      actions.push(createHexButton("→", () => navigateToScene(context, context.nextScene)));
    } else if (context.nextAct) {
      actions.push(createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct)));
    }

    return {
      node: container,
      actions,
      actionsAlign: actions.length === 1 ? "center" : "space-between",
    };
  };

  const renderFocusScene = (context) => {
    const scene = context.currentScene;
    const minigame = scene?.minigame || {};
    const flagKey = minigame.success_flag || scene?.success_flag || scene?.id;
    if (!flagKey) {
      return null;
    }

    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--focus";

    const card = document.createElement("div");
    card.className = "hud-card hud-card--focus";

    const title = document.createElement("h2");
    title.className = "hud-card__title";
    title.textContent = scene.title || minigame.title || "Focus test";
    card.appendChild(title);

    const description = document.createElement("p");
    description.className = "hud-card__body";
    description.textContent =
      (Array.isArray(scene.text) ? scene.text[0] : scene.text) ||
      minigame.prompt ||
      "Five orbs will appear. Tap each one before it fades to prove your focus.";
    card.appendChild(description);

    const statusLine = document.createElement("p");
    statusLine.className = "hud-card__meta";
    statusLine.style.display = "none";
    card.appendChild(statusLine);

    const orbArea = document.createElement("div");
    orbArea.className = "focus-area";
    card.appendChild(orbArea);

    const startButton = document.createElement("button");
    startButton.type = "button";
    startButton.className = "hud-card__cta";
    startButton.textContent = minigame.start_label || "Start focus test";

    const totalOrbs = minigame.orbs || 5;
    const windowMs = minigame.window_ms || 4000;

    let hits = 0;
    let active = false;
    let timerId = null;

    const cleanup = (message = null) => {
      active = false;
      hits = 0;
      orbArea.innerHTML = "";
      if (timerId) {
        clearTimeout(timerId);
        timerId = null;
      }
      startButton.disabled = false;
      if (message) {
        statusLine.textContent = message;
        statusLine.style.display = "block";
      }
    };

    const completeFocus = async () => {
      try {
        await postSessionUpdate(
          {
            state: {
              progress_flags: {
                [flagKey]: {
                  status: "completed",
                  hits: totalOrbs,
                  validated_at: new Date().toISOString(),
                },
              },
              last_scene: scene.id,
            },
            event: {
              event_type: "focus_test",
              payload: { scene_id: scene.id, hits: totalOrbs },
            },
          },
          { keepView: true }
        );
      } catch (_) {
        cleanup("Sync failed. Try again.");
      }
    };

    const spawnOrb = () => {
      if (!active) {
        return;
      }
      orbArea.innerHTML = "";
      const orb = document.createElement("button");
      orb.type = "button";
      orb.className = "focus-orb";
      orb.textContent = minigame.orb_label || "Tap!";
      orb.addEventListener("click", () => {
        hits += 1;
        if (hits >= totalOrbs) {
          cleanup();
          completeFocus();
          return;
        }
        statusLine.textContent = `Great! ${totalOrbs - hits} to go.`;
        statusLine.style.display = "block";
        spawnOrb();
      });
      orbArea.appendChild(orb);
      if (timerId) {
        clearTimeout(timerId);
      }
      timerId = setTimeout(() => {
        cleanup("The orb faded away. Try the focus test again.");
      }, windowMs);
    };

    startButton.addEventListener("click", () => {
      if (quest.state.busy) {
        return;
      }
      cleanup();
      statusLine.textContent = "Stay sharp!";
      statusLine.style.display = "block";
      startButton.disabled = true;
      active = true;
      hits = 0;
      spawnOrb();
    });

    card.appendChild(startButton);
    container.appendChild(card);

    const actions = [];
    if (context.prevScene) {
      actions.push(createHexButton("←", () => navigateToScene(context, context.prevScene)));
    }
    if (context.nextScene) {
      actions.push(createHexButton("→", () => navigateToScene(context, context.nextScene)));
    } else if (context.nextAct) {
      actions.push(createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct)));
    }

    return {
      node: container,
      actions,
      actionsAlign: actions.length === 1 ? "center" : "space-between",
    };
  };

  const renderIllusionScene = (context) => {
    const scene = context.currentScene;
    const minigame = scene?.minigame || {};
    const flagKey = minigame.success_flag || scene?.success_flag || scene?.id;
    if (!flagKey) {
      return null;
    }

    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--illusion";

    const card = document.createElement("div");
    card.className = "hud-card hud-card--illusion";

    const title = document.createElement("h2");
    title.className = "hud-card__title";
    title.textContent = scene.title || minigame.title || "Illusion duel";
    card.appendChild(title);

    const description = document.createElement("p");
    description.className = "hud-card__body";
    description.textContent =
      (Array.isArray(scene.text) ? scene.text[0] : scene.text) ||
      minigame.prompt ||
      "Break Eldarni’s false lights by dispelling each illusion quickly.";
    card.appendChild(description);

    const dialogueLine = document.createElement("p");
    dialogueLine.className = "hud-card__meta";
    dialogueLine.style.display = "none";
    card.appendChild(dialogueLine);

    const orbArea = document.createElement("div");
    orbArea.className = "focus-area";
    card.appendChild(orbArea);

    const startButton = document.createElement("button");
    startButton.type = "button";
    startButton.className = "hud-card__cta";
    startButton.textContent = minigame.start_label || "Begin illusion duel";

    const lines = Array.isArray(minigame.lines) ? minigame.lines : [];
    const goal = minigame.orbs || 5;
    const windowMs = minigame.window_ms || 3500;

    let hits = 0;
    let active = false;
    let timerId = null;

    const reset = (message = null) => {
      active = false;
      hits = 0;
      orbArea.innerHTML = "";
      if (timerId) {
        clearTimeout(timerId);
        timerId = null;
      }
      startButton.disabled = false;
      if (message) {
        dialogueLine.textContent = message;
        dialogueLine.style.display = "block";
      }
    };

    const completeIllusion = async () => {
      try {
        await postSessionUpdate(
          {
            state: {
              progress_flags: {
                [flagKey]: {
                  status: "won",
                  hits: goal,
                  validated_at: new Date().toISOString(),
                },
              },
              last_scene: scene.id,
            },
            event: {
              event_type: "illusion_battle",
              payload: {
                scene_id: scene.id,
                hits: goal,
              },
            },
          },
          { keepView: true }
        );
      } catch (_) {
        reset("The illusion reformed. Try again.");
      }
    };

    const spawnOrb = () => {
      if (!active) {
        return;
      }
      orbArea.innerHTML = "";
      const orb = document.createElement("button");
      orb.type = "button";
      orb.className = "focus-orb focus-orb--illusion";
      orb.textContent = minigame.orb_label || "Dispel!";
      orb.addEventListener("click", () => {
        hits += 1;
        const line = lines[hits - 1];
        if (line) {
          dialogueLine.textContent = line;
          dialogueLine.style.display = "block";
        }
        if (hits >= goal) {
          reset();
          completeIllusion();
          return;
        }
        spawnOrb();
      });
      orbArea.appendChild(orb);
      if (timerId) {
        clearTimeout(timerId);
      }
      timerId = setTimeout(() => {
        reset("The illusion slips away. Try again.");
      }, windowMs);
    };

    startButton.addEventListener("click", () => {
      if (quest.state.busy) {
        return;
      }
      reset();
      startButton.disabled = true;
      active = true;
      hits = 0;
      dialogueLine.textContent = "Eldarni cackles. Break the lights!";
      dialogueLine.style.display = "block";
      spawnOrb();
    });

    card.appendChild(startButton);
    container.appendChild(card);

    const actions = [];
    if (context.prevScene) {
      actions.push(createHexButton("←", () => navigateToScene(context, context.prevScene)));
    }
    if (context.nextScene) {
      actions.push(createHexButton("→", () => navigateToScene(context, context.nextScene)));
    } else if (context.nextAct) {
      actions.push(createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct)));
    }

    return {
      node: container,
      actions,
      actionsAlign: actions.length === 1 ? "center" : "space-between",
    };
  };

  const renderCombatScene = (context) => {
    const scene = context.currentScene;
    const minigame = scene?.minigame || {};
    const flagKey = minigame.success_flag || scene?.success_flag || scene?.id;
    if (!flagKey) {
      return null;
    }

    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--combat";

    const card = document.createElement("div");
    card.className = "hud-card hud-card--combat";

    const title = document.createElement("h2");
    title.className = "hud-card__title";
    title.textContent = scene.title || minigame.title || "Final battle";
    card.appendChild(title);

    const description = document.createElement("p");
    description.className = "hud-card__body";
    description.textContent =
      (Array.isArray(scene.text) ? scene.text[0] : scene.text) ||
      minigame.prompt ||
      "Match the symbols as they appear to overwhelm Dr Nat L Order.";
    card.appendChild(description);

    const symbolDisplay = document.createElement("div");
    symbolDisplay.className = "combat-symbol";
    symbolDisplay.textContent = "—";

    const buttonsWrapper = document.createElement("div");
    buttonsWrapper.className = "combat-buttons";

    const statusLine = document.createElement("p");
    statusLine.className = "hud-card__meta";
    statusLine.textContent = "";

    const arena = document.createElement("div");
    arena.className = "combat-area";
    arena.appendChild(symbolDisplay);
    arena.appendChild(buttonsWrapper);
    card.appendChild(arena);
    card.appendChild(statusLine);

    const symbols = Array.isArray(minigame.symbols) && minigame.symbols.length
      ? minigame.symbols
      : ["⚡", "🌿", "🔥"];
    const rounds = minigame.rounds || 5;
    const lines = Array.isArray(minigame.lines) ? minigame.lines : [];

    let sequence = [];
    let index = 0;
    let active = false;

    const resetBattle = (message = null) => {
      active = false;
      sequence = [];
      index = 0;
      symbolDisplay.textContent = "—";
      statusLine.textContent = message || "";
      buttonsWrapper.querySelectorAll("button").forEach((btn) => {
        btn.disabled = true;
      });
    };

    const completeBattle = async () => {
      try {
        await postSessionUpdate(
          {
            state: {
              progress_flags: {
                [flagKey]: {
                  status: "won",
                  sequence,
                  rounds,
                  validated_at: new Date().toISOString(),
                },
              },
              last_scene: scene.id,
            },
            event: {
              event_type: "final_battle",
              payload: {
                scene_id: scene.id,
                sequence,
              },
            },
          },
          { keepView: true }
        );
        statusLine.textContent = minigame.victory_line || "Dr Nat L Order’s illusion shatters!";
      } catch (_) {
        resetBattle("The illusion surges. Try again!");
      }
    };

    const advance = () => {
      if (index >= sequence.length) {
        completeBattle();
        return;
      }
      symbolDisplay.textContent = sequence[index];
      buttonsWrapper.querySelectorAll("button").forEach((btn) => {
        btn.disabled = false;
      });
    };

    const handleChoice = (symbol) => {
      if (!active) {
        return;
      }
      if (symbol === sequence[index]) {
        const line = lines[index];
        statusLine.textContent = line || "";
        index += 1;
        buttonsWrapper.querySelectorAll("button").forEach((btn) => {
          btn.disabled = true;
        });
        advance();
      } else {
        resetBattle(minigame.failure_line || "Dr Nat L Order grins. Try again!");
      }
    };

    symbols.forEach((symbol) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "combat-button";
      btn.textContent = symbol;
      btn.disabled = true;
      btn.addEventListener("click", () => handleChoice(symbol));
      buttonsWrapper.appendChild(btn);
    });

    const startButton = document.createElement("button");
    startButton.type = "button";
    startButton.className = "hud-card__cta";
    startButton.textContent = minigame.start_label || "Begin combat";
    startButton.addEventListener("click", () => {
      if (quest.state.busy) {
        return;
      }
      sequence = Array.from({ length: rounds }, () => {
        const idx = Math.floor(Math.random() * symbols.length);
        return symbols[idx];
      });
      index = 0;
      active = true;
      statusLine.textContent = minigame.start_line || "Dr Nat L Order lunges!";
      startButton.disabled = true;
      buttonsWrapper.querySelectorAll("button").forEach((btn) => {
        btn.disabled = true;
      });
      advance();
    });

    card.appendChild(startButton);
    container.appendChild(card);

    const actions = [];
    if (context.prevScene) {
      actions.push(createHexButton("←", () => navigateToScene(context, context.prevScene)));
    }
    if (context.nextScene) {
      actions.push(createHexButton("→", () => navigateToScene(context, context.nextScene)));
    } else if (context.nextAct) {
      actions.push(createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct)));
    }

    return {
      node: container,
      actions,
      actionsAlign: actions.length === 1 ? "center" : "space-between",
    };
  };

  const renderLocationScene = (context) => {
    const scene = context.currentScene;
    const minigame = scene?.minigame || {};
    const flagKey = minigame.success_flag || scene?.success_flag || scene?.id;
    if (!flagKey) {
      return null;
    }

    const flagStatus =
      context.session?.progress_flags?.[flagKey] ||
      context.session?.progress_flags?.[flagKey?.toUpperCase?.()] ||
      null;
    const isComplete = Boolean(flagStatus && (flagStatus.status || flagStatus.validated_at));

    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--location";

    const card = document.createElement("div");
    card.className = "hud-card hud-card--location";

    const title = document.createElement("h2");
    title.className = "hud-card__title";
    title.textContent = scene.title || minigame.title || "Objective";
    card.appendChild(title);

    if (!isComplete) {
      const description = document.createElement("p");
      description.className = "hud-card__body";
      description.textContent =
        (Array.isArray(scene.text) ? scene.text[0] : scene.text) ||
        minigame.prompt ||
        "Travel to the marked location and tap check-in.";
      card.appendChild(description);

      const metaBlock = document.createElement("div");
      metaBlock.className = "hud-card__meta-block";

      if (typeof minigame.radius_m === "number") {
        const radius = document.createElement("p");
        radius.className = "hud-card__meta";
        radius.textContent = `Check-in radius: ${Math.round(minigame.radius_m)} m`;
        metaBlock.appendChild(radius);
      }

      if (
        typeof minigame.latitude === "number" &&
        typeof minigame.longitude === "number" &&
        (minigame.latitude !== 0 || minigame.longitude !== 0)
      ) {
        const coords = document.createElement("p");
        coords.className = "hud-card__meta";
        coords.textContent = `Target: ${minigame.latitude.toFixed(5)}, ${minigame.longitude.toFixed(5)}`;
        metaBlock.appendChild(coords);
      }

      if (metaBlock.children.length) {
        card.appendChild(metaBlock);
      }

      const statusLine = document.createElement("p");
      statusLine.className = "hud-card__meta";
      statusLine.style.display = "none";
      card.appendChild(statusLine);

      const button = document.createElement("button");
      button.type = "button";
      button.className = "hud-card__cta";
      button.textContent = minigame.cta_label || "I'm here!";
      button.addEventListener("click", () => {
        if (quest.state.busy || button.disabled) {
          return;
        }
        if (!navigator.geolocation) {
          quest.set({
            error: "Location access not supported in this browser. Please enable GPS manually.",
          });
          return;
        }
        button.disabled = true;
        statusLine.textContent = "Requesting location…";
        statusLine.style.display = "block";

        navigator.geolocation.getCurrentPosition(
          async (pos) => {
            try {
              await postMinigame("/geocache/minigame/location", {
                location_id: minigame.location_id || scene.id,
                success_flag: flagKey,
                scene_id: scene.id,
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude,
                accuracy_m: pos.coords.accuracy,
                precision: minigame.precision || 4,
              });
            } catch (_) {
              statusLine.textContent = "Sync failed. Try again in a moment.";
              button.disabled = false;
            }
          },
          (error) => {
            statusLine.textContent = error.message || "Unable to fetch location. Please grant permission.";
            button.disabled = false;
          },
          {
            enableHighAccuracy: true,
            timeout: 10000,
            maximumAge: 0,
          }
        );
      });
      card.appendChild(button);
      container.appendChild(card);

      const actions = [];
      if (context.prevScene) {
        actions.push(createHexButton("←", () => navigateToScene(context, context.prevScene)));
      }
      if (context.nextScene) {
        actions.push(createHexButton("→", () => navigateToScene(context, context.nextScene)));
      } else if (context.nextAct) {
        actions.push(createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct)));
      }

      return {
        node: container,
        actions,
        actionsAlign: actions.length === 1 ? "center" : "space-between",
      };
    }

    // ✅ Success state
    card.classList.add("hud-card--checkin");
    container.classList.add("hud-scene--checkin");

    const successDescription = document.createElement("p");
    successDescription.className = "hud-card__body";
    successDescription.textContent =
      minigame.success_message ||
      "Aligning with local resonance… The Wild Court feels your presence.";
    card.appendChild(successDescription);

    const compassWrapper = document.createElement("div");
    compassWrapper.className = "hud-compass";

    const compassFace = document.createElement("div");
    compassFace.className = "hud-compass__face";
    compassWrapper.appendChild(compassFace);

    const compassNeedle = document.createElement("div");
    compassNeedle.className = "hud-compass__needle";
    compassWrapper.appendChild(compassNeedle);

    card.appendChild(compassWrapper);

    const resonanceLine = document.createElement("p");
    resonanceLine.className = "hud-card__status";
    resonanceLine.textContent =
      minigame.success_status ||
      "Resonance confirmed. Safe check-in recorded.";
    card.appendChild(resonanceLine);

    container.appendChild(card);

    const continueButton = createPrimaryButton("Continue", () => {
      if (context.nextScene) {
        navigateToScene(context, context.nextScene);
      } else if (context.nextAct) {
        navigateToActStart(context, context.nextAct);
      } else {
        refreshSession();
      }
    });
    continueButton.disabled = true;
    continueButton.classList.add("is-waiting");
    window.setTimeout(() => {
      continueButton.disabled = false;
      continueButton.classList.remove("is-waiting");
      continueButton.classList.add("is-active");
    }, minigame.success_delay_ms || 1400);

    return {
      node: container,
      actions: [continueButton],
      actionsAlign: "center",
    };
  };

  const renderArtifactScene = (context) => {
    const scene = context.currentScene;
    const minigame = scene?.minigame || {};
    const flagKey = minigame.success_flag || scene?.success_flag || scene?.id;
    if (!flagKey) {
      return null;
    }

    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--artifact";

    const card = document.createElement("div");
    card.className = "hud-card hud-card--artifact";

    const title = document.createElement("h2");
    title.className = "hud-card__title";
    title.textContent = scene.title || minigame.title || "Sigil hunt";
    card.appendChild(title);

    if (minigame.subtitle) {
      const subtitle = document.createElement("p");
      subtitle.className = "hud-card__subtitle";
      subtitle.textContent = minigame.subtitle;
      card.appendChild(subtitle);
    }

    if (scene.asset || minigame.asset || minigame.image) {
      const assetUrl = resolveAssetUrl(scene.asset || minigame.asset || minigame.image);
      if (assetUrl) {
        const image = document.createElement("img");
        image.className = "hud-card__image";
        image.src = assetUrl;
        image.alt = scene.title || "Artifact image";
        card.appendChild(image);
      }
    }

    const description = document.createElement("p");
    description.className = "hud-card__body";
    description.textContent =
      (Array.isArray(scene.text) ? scene.text[0] : scene.text) ||
      minigame.prompt ||
      "Tap your phone to the artifact or enter the code below.";
    card.appendChild(description);

    const form = document.createElement("form");
    form.className = "hud-form";

    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "numeric";
    input.pattern = "\\d{4,8}";
    input.maxLength = 8;
    input.placeholder = minigame.code_hint || "Enter 4-digit code";
    input.className = "hud-form__input";
    form.appendChild(input);

    if (minigame.note) {
      const note = document.createElement("p");
      note.className = "hud-form__note";
      note.textContent = minigame.note;
      form.appendChild(note);
    }

    const submitButton = document.createElement("button");
    submitButton.type = "submit";
    submitButton.className = "hud-card__cta";
    submitButton.textContent = minigame.submit_label || "Submit code";
    form.appendChild(submitButton);

    const errorLine = document.createElement("p");
    errorLine.className = "hud-card__error";
    errorLine.style.display = "none";
    form.appendChild(errorLine);

    if (minigame.clue) {
      const clue = document.createElement("p");
      clue.className = "hud-card__hint";
      clue.textContent = minigame.clue;
      clue.style.display = "none";

      const clueToggle = document.createElement("button");
      clueToggle.type = "button";
      clueToggle.className = "hud-card__cta";
      clueToggle.textContent = "Need a clue?";
      clueToggle.addEventListener("click", () => {
        const isHidden = clue.style.display === "none";
        clue.style.display = isHidden ? "block" : "none";
        clueToggle.textContent = isHidden ? "Hide clue" : "Need a clue?";
      });

      card.appendChild(clueToggle);
      card.appendChild(clue);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (quest.state.busy) {
        return;
      }
      const codeValue = (input.value || "").trim();
      if (!codeValue) {
        errorLine.textContent = "Enter the artifact code before submitting.";
        errorLine.style.display = "block";
        return;
      }
      submitButton.disabled = true;
      errorLine.style.display = "none";
      try {
        await postMinigame("/geocache/minigame/artifact", {
          artifact_slug: minigame.artifact_slug || minigame.slug || scene.id,
          success_flag: flagKey,
          scene_id: scene.id,
          code: codeValue,
        });
        input.value = "";
      } catch (_) {
        submitButton.disabled = false;
        errorLine.textContent = "Invalid code. Try again.";
        errorLine.style.display = "block";
      }
    });

    card.appendChild(form);
    container.appendChild(card);

    const actions = [];
    if (context.prevScene) {
      actions.push(createHexButton("←", () => navigateToScene(context, context.prevScene)));
    }
    if (context.nextScene) {
      actions.push(createHexButton("→", () => navigateToScene(context, context.nextScene)));
    } else if (context.nextAct) {
      actions.push(createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct)));
    }

    return {
      node: container,
      actions,
      actionsAlign: actions.length === 1 ? "center" : "space-between",
    };
  };

  const renderCelebrationScene = (context) => {
    const scene = context.currentScene;

    const container = document.createElement("div");
    container.className = "hud-scene hud-scene--celebration";

    const card = document.createElement("div");
    card.className = "hud-card hud-card--celebration";

    const icon = document.createElement("div");
    icon.className = "hud-celebration__icon";
    icon.textContent = scene.emoji || "✨";
    card.appendChild(icon);

    const title = document.createElement("h2");
    title.className = "hud-celebration__title";
    title.textContent = scene.title || "Objective complete";
    card.appendChild(title);

    const text = document.createElement("p");
    text.className = "hud-celebration__text";
    text.textContent =
      (Array.isArray(scene.text) ? scene.text.join(" ") : scene.text) ||
      "The Wild Court acknowledges your progress.";
    card.appendChild(text);

    container.appendChild(card);

    const actions = [];

    if (context.nextScene) {
      actions.push(createPrimaryButton("Continue", () => navigateToScene(context, context.nextScene)));
    } else if (context.nextAct) {
      actions.push(createPrimaryButton("Continue", () => navigateToActStart(context, context.nextAct)));
    } else {
      actions.push(createPrimaryButton("Check for updates", () => refreshSession()));
    }

    return {
      node: container,
      actions,
      actionsAlign: "center",
    };
  };

  const renderSceneContent = (context) => {
    const scene = context.currentScene;

    if (!scene) {
      return {
        node: buildEmptyScene(context),
        actions: [
          createPrimaryButton("Check for quest updates", () => refreshSession()),
        ],
        actionsAlign: "center",
      };
    }

    const mode = getSceneMode(scene);
    const minigame = scene?.minigame || {};
    const minigameKind = (minigame.kind || scene?.kind || "").toLowerCase();

    if (minigameKind === "focus") {
      const result = renderFocusScene(context);
      if (result) {
        return result;
      }
    }

    if (minigameKind === "illusion") {
      const result = renderIllusionScene(context);
      if (result) {
        return result;
      }
    }

    if (minigameKind === "combat") {
      const result = renderCombatScene(context);
      if (result) {
        return result;
      }
    }

    if (mode === "dialogue") {
      return renderDialogueScene(context);
    }

    if (mode === "activity") {
      const result = renderActivityScene(context);
      if (result) {
        return result;
      }
    }

    if (mode === "location") {
      const result = renderLocationScene(context);
      if (result) {
        return result;
      }
    }

    if (mode === "artifact") {
      const result = renderArtifactScene(context);
      if (result) {
        return result;
      }
    }

    if (mode === "celebration") {
      const result = renderCelebrationScene(context);
      if (result) {
        return result;
      }
    }

    return {
      useLegacy: true,
    };
  };

  const renderQuestHud = ({ busy, error }) => {
    ensureHudStructure();

    const context = buildQuestContext();

    if (
      !busy &&
      !quest.state.busy &&
      context.scenes.length > 0 &&
      !context.currentScene &&
      context.activeActId
    ) {
      const firstScene = context.scenes[0];
      const actNumber = parseActNumber(context.activeActId) || 1;
      postSessionUpdate(
        {
          state: {
            current_act: actNumber,
            last_scene: firstScene.id,
          },
        },
        { keepView: true }
      );
      return;
    }

    updateHudTopBar(context);
    renderOverviewContent(context);
    renderSettingsActions();
    applySceneBackground(context.currentScene, context.activeAct);
    applySceneCharacter(context);

    if (!hud.canvasContent) {
      return;
    }

    hud.overview?.classList.remove("is-open");
    hud.settingsSheet?.classList.remove("is-open");

    hud.canvasContent.innerHTML = "";

    const sceneResult = renderSceneContent(context);

    if (!sceneResult || sceneResult.useLegacy) {
      const legacyActScreen = buildLegacyActScreen();
      legacyActScreen.classList.add("hud__legacy-screen");

      if (error) {
        const errorBanner = document.createElement("div");
        errorBanner.className = "screen__error hud__error-banner";
        errorBanner.textContent = error;
        legacyActScreen.insertBefore(errorBanner, legacyActScreen.firstChild || null);
      }

      if (busy) {
        legacyActScreen.classList.add("screen--busy");
        const busyNote = document.createElement("div");
        busyNote.className = "screen__busy";
        busyNote.textContent = "Syncing…";
        legacyActScreen.appendChild(busyNote);
      }

      hud.canvasContent.appendChild(legacyActScreen);

      const fallbackButton = createPrimaryButton("Check for quest updates", () => {
        refreshSession();
      });
      fallbackButton.disabled = busy;
      setHudActions([fallbackButton], { align: "center" });
      return;
    }

    const sceneNode = sceneResult.node;
    if (sceneNode) {
      if (busy) {
        sceneNode.classList.add("is-busy");
      }
      if (error) {
        const errorBanner = document.createElement("div");
        errorBanner.className = "screen__error hud__error-banner";
        errorBanner.textContent = error;
        sceneNode.insertBefore(errorBanner, sceneNode.firstChild || null);
      }
      hud.canvasContent.appendChild(sceneNode);
    }

    const actions = Array.isArray(sceneResult.actions) ? sceneResult.actions : [];
    if (actions.length) {
      setHudActions(actions, { align: sceneResult.actionsAlign || "space-between" });
    } else {
      setHudActions(
        [
          createPrimaryButton("Check for quest updates", () => {
            refreshSession();
          }),
        ],
        { align: "center" }
      );
    }
  };

  const render = () => {
    const { view, busy, error } = quest.state;

    if (view === "act") {
      renderQuestHud({ busy, error });
      return;
    }

    root.innerHTML = "";
    const screen = (() => {
      switch (view) {
        case "loading":
          return renderLoading();
        case "offline":
          return renderOffline();
        case "landing":
          return renderLanding();
        case "resume":
          return renderResume();
        case "signin":
          return renderSignin();
        case "signup_upload":
          return renderSignupUpload();
        case "signup_confirm":
          return renderSignupConfirm();
        case "signup_age":
          return renderSignupAge();
        case "signup_campfire":
          return renderSignupCampfire();
        case "signup_kids":
          return renderSignupKids();
        case "error":
        default:
          return renderFatal();
      }
    })();

    if (busy) {
      screen.classList.add("screen--busy");
    }

    if (error) {
      const errorBox = document.createElement("div");
      errorBox.className = "screen__error";
      errorBox.textContent = error;
      screen.insertBefore(errorBox, screen.firstChild || null);
    }

    if (busy) {
      const busyNote = document.createElement("div");
      busyNote.className = "screen__busy";
      busyNote.textContent = "Syncing…";
      screen.appendChild(busyNote);
    }

    root.appendChild(screen);
  };

  const renderLoading = () => {
    const screen = document.createElement("section");
    screen.className = "screen";

    const title = document.createElement("h1");
    title.className = "screen__title";
    title.textContent = "Whispers of the Wild Court";

    const message = document.createElement("p");
    message.className = "screen__message";
    message.textContent = "Preparing the quest...";

    screen.appendChild(title);
    screen.appendChild(message);
    return screen;
  };

  const renderOffline = () => {
    const screen = document.createElement("section");
    screen.className = "screen";

    const title = document.createElement("h1");
    title.className = "screen__title";
    title.textContent = "Quest Offline";

    const message = document.createElement("p");
    message.className = "screen__message";
    message.textContent =
      "The Wild Court sleeps for now. Check back soon when the quest awakens.";

    screen.appendChild(title);
    screen.appendChild(message);
    return screen;
  };

  const renderLanding = () => {
    const screen = document.createElement("section");
    screen.className = "screen";

    const title = document.createElement("h1");
    title.className = "screen__title";
    title.textContent = "Whispers of the Wild Court";

    const subtitle = document.createElement("p");
    subtitle.className = "screen__subtitle";
    subtitle.textContent =
      "A mobile geocache quest built for live play. Sign in with your RDAB account or create a new quest pass.";

    const actions = document.createElement("div");
    actions.className = "screen__actions";

    const signInButton = document.createElement("button");
    signInButton.type = "button";
    signInButton.className = "button button--primary";
    signInButton.textContent = "Sign in with RDAB app";
    signInButton.addEventListener("click", () => {
      const prefill =
        quest.state.profile?.trainer_name ||
        quest.state.signinForm.trainer_name ||
        "";
      quest.set({
        view: "signin",
        error: null,
        signinForm: {
          trainer_name: prefill,
          pin: "",
        },
      });
    });
    actions.appendChild(signInButton);

    const createButton = document.createElement("button");
    createButton.type = "button";
    createButton.className = "button button--secondary";
    createButton.textContent = "Create quest pass";
    createButton.addEventListener("click", () => {
      signinGuard.reset();
      quest.set({
        view: "signup_upload",
        error: null,
        signup: createEmptySignup(),
      });
    });
    actions.appendChild(createButton);

    if (quest.state.profile) {
      const resumeButton = document.createElement("button");
      resumeButton.type = "button";
      resumeButton.className = "button button--secondary";
      resumeButton.textContent = "Reload save";
      resumeButton.addEventListener("click", () => {
        quest.set({
          view: "resume",
          error: null,
        });
      });
      actions.appendChild(resumeButton);
    }

    screen.appendChild(title);
    screen.appendChild(subtitle);
    screen.appendChild(actions);
    return screen;
  };

  const renderResume = () => {
    const screen = document.createElement("section");
    screen.className = "screen";

    const title = document.createElement("h1");
    title.className = "screen__title";
    title.textContent = "Reload save";

    const message = document.createElement("p");
    message.className = "screen__message";
    message.textContent = quest.state.profile
      ? `Trainer ${quest.state.profile.trainer_name}, ready to continue the quest?`
      : "No save data detected.";

    const actions = document.createElement("div");
    actions.className = "screen__actions";

    const continueButton = document.createElement("button");
    continueButton.type = "button";
    continueButton.className = "button button--primary";
    continueButton.textContent = "Enter PIN to continue";
    continueButton.addEventListener("click", () => {
      const prefill =
        quest.state.profile?.trainer_name ||
        quest.state.signinForm.trainer_name ||
        "";
      quest.set({
        view: "signin",
        signinForm: {
          trainer_name: prefill,
          pin: "",
        },
        error: null,
      });
    });

    const resetButton = document.createElement("button");
    resetButton.type = "button";
    resetButton.className = "button button--ghost";
    resetButton.textContent = "Forget this device";
    resetButton.addEventListener("click", () => {
      storage.clear();
      pinVault.clear();
      quest.set({
        profile: null,
        session: null,
        pin: null,
        view: "landing",
        signinForm: {
          trainer_name: "",
          pin: "",
        },
        signup: createEmptySignup(),
        error: null,
      });
    });

    actions.appendChild(continueButton);
    actions.appendChild(resetButton);

    screen.appendChild(title);
    screen.appendChild(message);
    screen.appendChild(actions);
    return screen;
  };

  const completeQuestSignup = async () => {
    const data = quest.state.signup || createEmptySignup();
    const trainerName = (data.trainer_name || "").trim();
    const pinValue = (data.pin || "").trim();
    const memorableValue = (data.memorable || "").trim();

    if (!trainerName || !pinValue || !memorableValue) {
      quest.set({
        error: "Signup details are incomplete. Please start again.",
      });
      return;
    }

    const requestBody = {
      trainer_name: trainerName,
      pin: pinValue,
      memorable: memorableValue,
      age_band: data.age_band || "13plus",
      campfire_name: data.campfire_opt_out ? null : (data.campfire_name || null),
      campfire_opt_out: Boolean(data.campfire_opt_out),
    };

    quest.set({ busy: true, error: null });
    try {
      const response = await apiRequest("/geocache/signup/complete", {
        method: "POST",
        body: requestBody,
      });
      signinGuard.recordSuccess();
      pinVault.remember(pinValue);
      applySessionPayload(response, {
        busy: false,
        error: null,
        pin: pinValue,
        view: "act",
      });
    } catch (error) {
      quest.set({
        busy: false,
        error: messageFromError(error),
      });
    }
  };

  const renderSignin = () => {
    const screen = document.createElement("section");
    screen.className = "screen";

    const heading = document.createElement("h1");
    heading.className = "screen__title";
    heading.textContent = "Sign in with RDAB app";
    screen.appendChild(heading);

    const subtitle = document.createElement("p");
    subtitle.className = "screen__subtitle";
    subtitle.textContent = "Enter your trainer name and 4-digit RDAB PIN to continue the quest.";
    screen.appendChild(subtitle);

    const guardStatus = signinGuard.check();
    const locked = !guardStatus.allowed;
    const signinState = quest.state.signinForm || { trainer_name: "", pin: "" };

    const form = document.createElement("form");
    form.className = "form";

    const trainerField = document.createElement("div");
    trainerField.className = "field";
    const trainerLabel = document.createElement("label");
    trainerLabel.setAttribute("for", "signin_trainer_name");
    trainerLabel.textContent = "Trainer name";
    const trainerInput = document.createElement("input");
    trainerInput.className = "input";
    trainerInput.id = "signin_trainer_name";
    trainerInput.name = "trainer_name";
    trainerInput.type = "text";
    trainerInput.placeholder = "e.g. WildCourtSeeker";
    trainerInput.maxLength = 32;
    trainerInput.autocomplete = "username";
    trainerInput.required = true;
    trainerInput.value = signinState.trainer_name || "";
    trainerField.appendChild(trainerLabel);
    trainerField.appendChild(trainerInput);

    const pinField = document.createElement("div");
    pinField.className = "field";
    const pinLabel = document.createElement("label");
    pinLabel.setAttribute("for", "signin_pin");
    pinLabel.textContent = "4-digit RDAB PIN";
    const pinInput = document.createElement("input");
    pinInput.className = "input";
    pinInput.id = "signin_pin";
    pinInput.name = "pin";
    pinInput.type = "password";
    pinInput.inputMode = "numeric";
    pinInput.pattern = "\\d{4}";
    pinInput.placeholder = "••••";
    pinInput.autocomplete = "current-password";
    pinInput.maxLength = 4;
    pinInput.required = true;
    pinField.appendChild(pinLabel);
    pinField.appendChild(pinInput);

    const attemptHint = document.createElement("p");
    attemptHint.className = "form__hint";
    if (locked) {
      attemptHint.textContent = `Locked for security. Try again in ${guardStatus.waitSeconds} seconds.`;
    } else {
      const remaining = guardStatus.remaining ?? LOGIN_MAX_ATTEMPTS;
      attemptHint.textContent = `${remaining} attempt${remaining === 1 ? "" : "s"} before a lock.`;
    }

    const submitButton = document.createElement("button");
    submitButton.type = "submit";
    submitButton.className = "button button--primary";
    submitButton.textContent = "Continue";

    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "button button--ghost";
    backButton.textContent = "Back";
    backButton.addEventListener("click", () => {
      quest.set({
        view: "landing",
        error: null,
      });
    });

    const signupButton = document.createElement("button");
    signupButton.type = "button";
    signupButton.className = "button button--ghost";
    signupButton.textContent = "Need a quest pass?";
    signupButton.addEventListener("click", () => {
      signinGuard.reset();
      quest.set({
        view: "signup_upload",
        error: null,
        signup: createEmptySignup(),
      });
    });

    form.appendChild(trainerField);
    form.appendChild(pinField);
    form.appendChild(attemptHint);
    form.appendChild(submitButton);
    form.appendChild(backButton);
    form.appendChild(signupButton);

    if (locked || quest.state.busy) {
      trainerInput.disabled = true;
      pinInput.disabled = true;
      submitButton.disabled = true;
    }

    window.requestAnimationFrame(() => {
      if (!locked && !quest.state.busy) {
        if (!trainerInput.value) {
          trainerInput.focus();
        } else {
          pinInput.focus();
        }
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (quest.state.busy) {
        return;
      }

      const guardCheck = signinGuard.check();
      if (!guardCheck.allowed) {
        quest.set({
          error: `Too many incorrect attempts. Try again in ${guardCheck.waitSeconds} seconds.`,
        });
        return;
      }

      const trainerName = (trainerInput.value || "").trim();
      const pinValue = (pinInput.value || "").trim();

      if (!trainerName || !pinValue) {
        quest.set({ error: "Trainer name and PIN are required." });
        return;
      }

      if (!/^\d{4}$/.test(pinValue)) {
        quest.set({ error: "PIN must be exactly 4 digits." });
        return;
      }

      quest.set({
        busy: true,
        error: null,
        signinForm: {
          trainer_name: trainerName,
          pin: "",
        },
      });

      try {
        const response = await apiRequest("/geocache/profile", {
          method: "POST",
          body: {
            trainer_name: trainerName,
            pin: pinValue,
            create_if_missing: false,
            metadata: {
              auth_mode: "signin",
              source: "geocache",
            },
          },
        });
        signinGuard.recordSuccess();
        pinVault.remember(pinValue);
        applySessionPayload(response, {
          busy: false,
          error: null,
          pin: pinValue,
          view: "act",
        });
      } catch (error) {
        pinVault.clear();
        if (error instanceof APIError) {
          if (error.status === 401 || error.payload?.error === "invalid_pin") {
            const guardState = signinGuard.recordFailure();
            const waitSeconds = guardState.waitSeconds || 0;
            const remaining = guardState.remaining ?? LOGIN_MAX_ATTEMPTS;
            quest.set({
              busy: false,
              error:
                waitSeconds > 0
                  ? `Too many incorrect attempts. Try again in ${waitSeconds} seconds.`
                  : `Wrong PIN. ${remaining} attempt${remaining === 1 ? "" : "s"} remaining.`,
              signinForm: {
                trainer_name: trainerName,
                pin: "",
              },
            });
            return;
          }
          if (error.status === 404 || error.payload?.error === "trainer_not_found") {
            quest.set({
              busy: false,
              error: "We couldn't find that trainer. Create a quest pass first.",
              signinForm: {
                trainer_name: trainerName,
                pin: "",
              },
            });
            return;
          }
        }
        quest.set({
          busy: false,
          error: messageFromError(error),
          signinForm: {
            trainer_name: trainerName,
            pin: "",
          },
        });
      }
    });

    screen.appendChild(form);
    return screen;
  };

  const renderSignupUpload = () => {
    const screen = document.createElement("section");
    screen.className = "screen";

    const heading = document.createElement("h1");
    heading.className = "screen__title";
    heading.textContent = "Create your quest pass";
    screen.appendChild(heading);

    const subtitle = document.createElement("p");
    subtitle.className = "screen__subtitle";
    subtitle.textContent =
      "Upload a clear screenshot of your Pokémon GO trainer profile, set a 4-digit PIN, and choose a memorable password.";
    screen.appendChild(subtitle);

    const signupState = quest.state.signup || createEmptySignup();

    const form = document.createElement("form");
    form.className = "form";

    const screenshotField = document.createElement("div");
    screenshotField.className = "field";
    const screenshotLabel = document.createElement("label");
    screenshotLabel.setAttribute("for", "signup_screenshot");
    screenshotLabel.textContent = "Trainer profile screenshot";
    const screenshotInput = document.createElement("input");
    screenshotInput.className = "input";
    screenshotInput.id = "signup_screenshot";
    screenshotInput.name = "profile_screenshot";
    screenshotInput.type = "file";
    screenshotInput.accept = "image/*";
    screenshotInput.required = true;
    screenshotField.appendChild(screenshotLabel);
    screenshotField.appendChild(screenshotInput);

    const pinField = document.createElement("div");
    pinField.className = "field";
    const pinLabel = document.createElement("label");
    pinLabel.setAttribute("for", "signup_pin");
    pinLabel.textContent = "Choose a 4-digit PIN";
    const pinInput = document.createElement("input");
    pinInput.className = "input";
    pinInput.id = "signup_pin";
    pinInput.name = "pin";
    pinInput.type = "password";
    pinInput.inputMode = "numeric";
    pinInput.pattern = "\\d{4}";
    pinInput.maxLength = 4;
    pinInput.placeholder = "1234";
    pinInput.required = true;
    pinInput.value = signupState.pin || "";
    pinField.appendChild(pinLabel);
    pinField.appendChild(pinInput);

    const memorableField = document.createElement("div");
    memorableField.className = "field";
    const memorableLabel = document.createElement("label");
    memorableLabel.setAttribute("for", "signup_memorable");
    memorableLabel.textContent = "Memorable password (for recovery)";
    const memorableInput = document.createElement("input");
    memorableInput.className = "input";
    memorableInput.id = "signup_memorable";
    memorableInput.name = "memorable";
    memorableInput.type = "text";
    memorableInput.placeholder = "e.g. PikachuStorm";
    memorableInput.required = true;
    memorableInput.maxLength = 64;
    memorableInput.value = signupState.memorable || "";
    memorableField.appendChild(memorableLabel);
    memorableField.appendChild(memorableInput);

    const submitButton = document.createElement("button");
    submitButton.type = "submit";
    submitButton.className = "button button--primary";
    submitButton.textContent = "Detect trainer name";

    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "button button--ghost";
    backButton.textContent = "Back";
    backButton.addEventListener("click", () => {
      quest.set({
        view: "landing",
        error: null,
        signup: createEmptySignup(),
      });
    });

    form.appendChild(screenshotField);
    form.appendChild(pinField);
    form.appendChild(memorableField);
    form.appendChild(submitButton);
    form.appendChild(backButton);

    if (quest.state.busy) {
      form.querySelectorAll("input, button").forEach((el) => {
        el.disabled = true;
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (quest.state.busy) {
        return;
      }

      const file = screenshotInput.files && screenshotInput.files[0];
      const pinValue = (pinInput.value || "").trim();
      const memorableValue = (memorableInput.value || "").trim();

      if (!file) {
        quest.set({ error: "Upload your trainer profile screenshot." });
        return;
      }
      if (!/^\d{4}$/.test(pinValue)) {
        quest.set({ error: "PIN must be exactly 4 digits." });
        return;
      }
      if (!memorableValue) {
        quest.set({ error: "Memorable password is required." });
        return;
      }

      const formData = new FormData();
      formData.append("profile_screenshot", file);

      quest.set({ busy: true, error: null });

      try {
        const result = await apiRequest("/geocache/signup/detect", {
          method: "POST",
          body: formData,
        });
        const detected = (result.trainer_name || "").trim();
        quest.set({
          busy: false,
          error: null,
          signup: {
            trainer_name: detected || signupState.trainer_name || "",
            detected_name: detected,
            pin: pinValue,
            memorable: memorableValue,
            age_band: null,
            campfire_name: "",
            campfire_opt_out: false,
          },
          view: "signup_confirm",
        });
      } catch (error) {
        quest.set({
          busy: false,
          error: messageFromError(error),
          signup: {
            ...signupState,
            pin: pinValue,
            memorable: memorableValue,
          },
        });
      }
    });

    screen.appendChild(form);
    return screen;
  };

  const renderSignupConfirm = () => {
    const signupState = quest.state.signup || createEmptySignup();
    const screen = document.createElement("section");
    screen.className = "screen";

    const heading = document.createElement("h1");
    heading.className = "screen__title";
    heading.textContent = "Confirm trainer name";
    screen.appendChild(heading);

    const subtitle = document.createElement("p");
    subtitle.className = "screen__subtitle";
    if (signupState.detected_name) {
      subtitle.textContent = `Is your trainer name "${signupState.detected_name}"? Adjust it if something looks off.`;
    } else {
      subtitle.textContent = "We couldn't read the name from the screenshot. Type it exactly as it appears in Pokémon GO.";
    }
    screen.appendChild(subtitle);

    const form = document.createElement("form");
    form.className = "form";

    const nameField = document.createElement("div");
    nameField.className = "field";
    const nameLabel = document.createElement("label");
    nameLabel.setAttribute("for", "signup_trainer_name");
    nameLabel.textContent = "Trainer name";
    const nameInput = document.createElement("input");
    nameInput.className = "input";
    nameInput.id = "signup_trainer_name";
    nameInput.name = "trainer_name";
    nameInput.type = "text";
    nameInput.required = true;
    nameInput.maxLength = 32;
    nameInput.placeholder = "Type your trainer name";
    nameInput.value =
      signupState.trainer_name ||
      signupState.detected_name ||
      "";
    nameField.appendChild(nameLabel);
    nameField.appendChild(nameInput);

    const continueButton = document.createElement("button");
    continueButton.type = "submit";
    continueButton.className = "button button--primary";
    continueButton.textContent = "Continue";

    const retryButton = document.createElement("button");
    retryButton.type = "button";
    retryButton.className = "button button--ghost";
    retryButton.textContent = "Re-upload screenshot";
    retryButton.addEventListener("click", () => {
      quest.set({
        view: "signup_upload",
        error: null,
      });
    });

    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "button button--ghost";
    backButton.textContent = "Back";
    backButton.addEventListener("click", () => {
      quest.set({
        view: "signup_upload",
        error: null,
      });
    });

    form.appendChild(nameField);
    form.appendChild(continueButton);
    form.appendChild(retryButton);
    form.appendChild(backButton);

    if (quest.state.busy) {
      form.querySelectorAll("input, button").forEach((el) => {
        el.disabled = true;
      });
    } else {
      window.requestAnimationFrame(() => {
        nameInput.focus();
      });
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (quest.state.busy) {
        return;
      }
      const trainerName = (nameInput.value || "").trim();
      if (!trainerName) {
        quest.set({ error: "Trainer name is required." });
        return;
      }
      quest.set({
        signup: {
          ...signupState,
          trainer_name: trainerName,
        },
        view: "signup_age",
        error: null,
      });
    });

    screen.appendChild(form);
    return screen;
  };

  const renderSignupAge = () => {
    const signupState = quest.state.signup || createEmptySignup();
    const screen = document.createElement("section");
    screen.className = "screen";

    const heading = document.createElement("h1");
    heading.className = "screen__title";
    heading.textContent = "How old are you?";
    screen.appendChild(heading);

    const subtitle = document.createElement("p");
    subtitle.className = "screen__subtitle";
    subtitle.textContent = "We use this to follow Pokémon player safeguarding guidance.";
    screen.appendChild(subtitle);

    const actions = document.createElement("div");
    actions.className = "screen__actions";

    const adultButton = document.createElement("button");
    adultButton.type = "button";
    adultButton.className = "button button--primary";
    adultButton.textContent = "I'm 13 or older";
    adultButton.addEventListener("click", () => {
      quest.set({
        signup: {
          ...signupState,
          age_band: "13plus",
          campfire_opt_out: false,
        },
        view: "signup_campfire",
        error: null,
      });
    });

    const kidButton = document.createElement("button");
    kidButton.type = "button";
    kidButton.className = "button button--secondary";
    kidButton.textContent = "I'm under 13";
    kidButton.addEventListener("click", () => {
      quest.set({
        signup: {
          ...signupState,
          age_band: "under13",
          campfire_name: "",
          campfire_opt_out: true,
        },
        view: "signup_kids",
        error: null,
      });
    });

    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "button button--ghost";
    backButton.textContent = "Back";
    backButton.addEventListener("click", () => {
      quest.set({
        view: "signup_confirm",
        error: null,
      });
    });

    actions.appendChild(adultButton);
    actions.appendChild(kidButton);
    actions.appendChild(backButton);
    screen.appendChild(actions);
    return screen;
  };

  const renderSignupCampfire = () => {
    const signupState = quest.state.signup || createEmptySignup();
    const screen = document.createElement("section");
    screen.className = "screen";

    const heading = document.createElement("h1");
    heading.className = "screen__title";
    heading.textContent = "Campfire username";
    screen.appendChild(heading);

    const subtitle = document.createElement("p");
    subtitle.className = "screen__subtitle";
    subtitle.textContent =
      "Link your Campfire username so we can sync check-ins and rewards.";
    screen.appendChild(subtitle);

    const form = document.createElement("form");
    form.className = "form";

    const campfireField = document.createElement("div");
    campfireField.className = "field";
    const campfireLabel = document.createElement("label");
    campfireLabel.setAttribute("for", "signup_campfire");
    campfireLabel.textContent = "Campfire username";
    const campfireInput = document.createElement("input");
    campfireInput.className = "input";
    campfireInput.id = "signup_campfire";
    campfireInput.name = "campfire_name";
    campfireInput.type = "text";
    campfireInput.placeholder = "e.g. Trainer123";
    campfireInput.pattern = "[^@]+";
    campfireInput.maxLength = 32;
    campfireInput.value = signupState.campfire_name || "";
    campfireField.appendChild(campfireLabel);
    campfireField.appendChild(campfireInput);

    const optOutWrap = document.createElement("label");
    optOutWrap.className = "checkbox";
    const optOutInput = document.createElement("input");
    optOutInput.type = "checkbox";
    optOutInput.name = "campfire_opt_out";
    optOutInput.checked = Boolean(signupState.campfire_opt_out);
    const optOutText = document.createElement("span");
    optOutText.textContent = "I'm not on Campfire";
    optOutWrap.appendChild(optOutInput);
    optOutWrap.appendChild(optOutText);
    campfireField.appendChild(optOutWrap);

    optOutInput.addEventListener("change", () => {
      const optOut = optOutInput.checked;
      campfireInput.disabled = optOut;
      if (optOut) {
        campfireInput.value = "";
      }
    });
    if (optOutInput.checked) {
      campfireInput.disabled = true;
    }

    const submitButton = document.createElement("button");
    submitButton.type = "submit";
    submitButton.className = "button button--primary";
    submitButton.textContent = "Create quest pass";

    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "button button--ghost";
    backButton.textContent = "Back";
    backButton.addEventListener("click", () => {
      quest.set({
        view: "signup_age",
        error: null,
      });
    });

    form.appendChild(campfireField);
    form.appendChild(submitButton);
    form.appendChild(backButton);

    if (quest.state.busy) {
      form.querySelectorAll("input, button").forEach((el) => {
        el.disabled = true;
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (quest.state.busy) {
        return;
      }
      const optOut = Boolean(optOutInput.checked);
      const campfireName = optOut ? "" : (campfireInput.value || "").trim();
      if (!optOut && !campfireName) {
        quest.set({ error: "Campfire username is required or mark that you're not on Campfire." });
        return;
      }
      const nextSignup = {
        ...signupState,
        campfire_name: campfireName,
        campfire_opt_out: optOut,
      };
      quest.set({
        signup: nextSignup,
      });
      await completeQuestSignup();
    });

    screen.appendChild(form);
    return screen;
  };

  const renderSignupKids = () => {
    const signupState = quest.state.signup || createEmptySignup();
    const screen = document.createElement("section");
    screen.className = "screen";

    const heading = document.createElement("h1");
    heading.className = "screen__title";
    heading.textContent = "Create kids quest pass";
    screen.appendChild(heading);

    const message = document.createElement("p");
    message.className = "screen__message";
    message.textContent = `We'll create a Kids Account for trainer ${signupState.trainer_name}. Campfire can be linked later by a guardian.`;
    screen.appendChild(message);

    const actions = document.createElement("div");
    actions.className = "screen__actions";

    const createButton = document.createElement("button");
    createButton.type = "button";
    createButton.className = "button button--primary";
    createButton.textContent = "Create kids quest pass";
    createButton.addEventListener("click", async () => {
      if (quest.state.busy) {
        return;
      }
      quest.set({
        signup: {
          ...signupState,
          campfire_name: "",
          campfire_opt_out: true,
        },
      });
      await completeQuestSignup();
    });

    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "button button--ghost";
    backButton.textContent = "Back";
    backButton.addEventListener("click", () => {
      quest.set({
        view: "signup_age",
        error: null,
      });
    });

    if (quest.state.busy) {
      createButton.disabled = true;
      backButton.disabled = true;
    }

    actions.appendChild(createButton);
    actions.appendChild(backButton);
    screen.appendChild(actions);
    return screen;
  };

  const buildLegacyActScreen = () => {
    const { profile, session, story } = quest.state;
    const screen = document.createElement("section");
    screen.className = "screen";

    const title = document.createElement("h1");
    title.className = "screen__title";
    title.textContent = session?.current_act
      ? `Act ${session.current_act}`
      : "Quest ready";

    const actData = (() => {
      if (!story?.acts || !story.acts.length) {
        return null;
      }
      const actIdFromSession = session?.current_act
        ? `act${session.current_act}`
        : null;
      const matched = story.acts.find((act) => {
        if (actIdFromSession) {
          return act.id === actIdFromSession;
        }
        return act.id === "act1";
      });
      return matched || story.acts[0];
    })();

    const message = document.createElement("p");
    message.className = "screen__message";
    message.textContent = actData?.intro
      ? actData.intro
      : profile
      ? `Trainer ${profile.trainer_name}, your quest profile is synced. The first chapter unlocks shortly.`
      : "Quest profile ready.";

    let objectiveTitle = null;
    let objectiveList = null;
    if (actData?.objectives?.length) {
      objectiveTitle = document.createElement("p");
      objectiveTitle.className = "screen__subtitle";
      objectiveTitle.textContent = "Quest objectives";

      objectiveList = document.createElement("ul");
      objectiveList.className = "objectives";
      actData.objectives.forEach((objective) => {
        const item = document.createElement("li");
        item.textContent = objective;
        objectiveList.appendChild(item);
      });
    }

    const sceneLookup = (id) => (story?.scenes || {})[id];
    const minigameScenes =
      (actData?.scenes || [])
        .map((sceneId) => sceneLookup(sceneId))
        .filter((scene) => scene?.type === "minigame") || [];

    const tasksPanel = document.createElement("div");
    tasksPanel.className = "tasks";

    minigameScenes.forEach((scene) => {
      const minigame = scene?.minigame || {};
      const kind = (minigame.kind || scene?.kind || "").toLowerCase();
      const flagKey = minigame.success_flag || scene?.success_flag || scene?.id;
      const flagStatus = flagKey ? session?.progress_flags?.[flagKey] : null;

      const card = document.createElement("div");
      card.className = "task-card";
      if (flagStatus) {
        card.classList.add("task-card--done");
      }

      const heading = document.createElement("h2");
      heading.className = "task-card__title";
      heading.textContent =
        scene?.title ||
        (kind === "artifact_scan"
          ? "Locate the artifact"
          : kind === "mosaic"
          ? "Complete the puzzle"
          : "Quest task");

      const description = document.createElement("p");
      description.className = "task-card__description";
      description.textContent =
        (scene?.text && scene.text[0]) ||
        minigame.prompt ||
        (kind === "artifact_scan"
          ? "Scan the object on-site or enter the 4-digit code etched on it."
          : kind === "mosaic"
          ? "Solve the puzzle to restore the relic."
          : kind === "location"
          ? "Share your location when you arrive at the marked spot."
          : kind === "riddle"
          ? "Choose the correct answer to unlock the cache."
          : kind === "focus"
          ? "Pass Adellion’s reflex check by tapping the orbs."
          : kind === "quiz"
          ? "Answer the Oracles’ questions to record your mood."
          : "Complete the required interaction to advance.");

      card.appendChild(heading);
      card.appendChild(description);

      if (flagStatus) {
        const status = document.createElement("p");
        status.className = "task-card__status";
        const validatedAt = flagStatus.validated_at
          ? new Date(flagStatus.validated_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })
          : null;
        status.textContent = validatedAt
          ? `Completed — ${validatedAt}`
          : "Completed";
      card.appendChild(status);

      if (flagStatus.lat && flagStatus.lng) {
        const coords = document.createElement("p");
        coords.className = "task-card__meta";
        coords.textContent = `Check-in recorded at ${flagStatus.lat}, ${flagStatus.lng}`;
        card.appendChild(coords);
      }

      if (Array.isArray(flagStatus.epilogue) && flagStatus.epilogue.length) {
        const epilogueBlock = document.createElement("div");
        epilogueBlock.className = "task-card__epilogue";
        flagStatus.epilogue.forEach((line) => {
          const paragraph = document.createElement("p");
          paragraph.className = "task-card__meta";
          paragraph.textContent = line;
          epilogueBlock.appendChild(paragraph);
        });
        card.appendChild(epilogueBlock);
      }
    } else if (kind === "artifact_scan") {
      const form = document.createElement("form");
      form.className = "task-form";

        const input = document.createElement("input");
        input.type = "text";
        input.inputMode = "numeric";
        input.pattern = "\\d{4}";
        input.maxLength = 8;
        input.placeholder = minigame.code_hint || "Enter 4-digit code";
        input.className = "input input--compact";

        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "button button--primary";
        submit.textContent = "Submit code";

        form.appendChild(input);
        form.appendChild(submit);

        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (quest.state.busy) {
            return;
          }
          const codeValue = (input.value || "").trim();
          if (!codeValue) {
            quest.set({
              error: "Enter the artifact code before submitting.",
            });
            return;
          }
          try {
            await postMinigame("/geocache/minigame/artifact", {
              artifact_slug: minigame.artifact_slug || minigame.slug || scene.id,
              success_flag: flagKey,
              scene_id: scene.id,
              code: codeValue,
            });
            input.value = "";
          } catch (_) {
            // Handled in postMinigame
          }
        });

        card.appendChild(form);
      } else if (kind === "mosaic") {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button button--primary";
        button.textContent = "Mark puzzle complete";
        button.addEventListener("click", async () => {
          if (quest.state.busy) {
            return;
          }
          try {
            await postMinigame("/geocache/minigame/mosaic", {
              puzzle_id: minigame.puzzle_id || scene.id,
              success_flag: flagKey,
              scene_id: scene.id,
              success_token: crypto?.randomUUID
                ? crypto.randomUUID()
                : `token-${Date.now()}`,
            });
          } catch (_) {
            // Handled in postMinigame
          }
        });
        card.appendChild(button);
      } else if (kind === "location") {
        const prompt = document.createElement("p");
        prompt.className = "task-card__meta";
        prompt.textContent =
          minigame.prompt ||
          "When you reach the location, tap the button below to share your position.";
        card.appendChild(prompt);

        const metaList = document.createElement("div");
        metaList.className = "task-card__meta-block";
        const radiusValue =
          typeof minigame.radius_m === "number" && minigame.radius_m > 0
            ? Math.round(minigame.radius_m)
            : null;
        if (radiusValue) {
          const line = document.createElement("p");
          line.className = "task-card__meta";
          line.textContent = `Check-in radius: ${radiusValue} m`;
          metaList.appendChild(line);
        }
        if (
          typeof minigame.latitude === "number" &&
          typeof minigame.longitude === "number" &&
          (minigame.latitude !== 0 || minigame.longitude !== 0)
        ) {
          const line = document.createElement("p");
          line.className = "task-card__meta";
          line.textContent = `Target: ${minigame.latitude.toFixed(
            5
          )}, ${minigame.longitude.toFixed(5)}`;
          metaList.appendChild(line);
        }
        if (metaList.children.length) {
          card.appendChild(metaList);
        }

        const button = document.createElement("button");
        button.type = "button";
        button.className = "button button--primary";
        button.textContent = "Check in here";
        button.addEventListener("click", async () => {
          if (quest.state.busy) {
            return;
          }
          if (!navigator.geolocation) {
            quest.set({
              error:
                "Location access not supported in this browser. Please allow GPS manually.",
            });
            return;
          }
          button.disabled = true;
          navigator.geolocation.getCurrentPosition(
            async (pos) => {
              try {
                await postMinigame("/geocache/minigame/location", {
                  location_id: minigame.location_id || scene.id,
                  success_flag: flagKey,
                  scene_id: scene.id,
                  latitude: pos.coords.latitude,
                  longitude: pos.coords.longitude,
                  accuracy_m: pos.coords.accuracy,
                  precision: minigame.precision || 4,
                });
              } catch (_) {
                // handled in postMinigame
              } finally {
                button.disabled = false;
              }
            },
            (error) => {
              button.disabled = false;
              quest.set({
                error:
                  error.message ||
                  "Unable to fetch location. Please ensure permissions are granted.",
              });
            },
            {
              enableHighAccuracy: true,
              timeout: 10000,
              maximumAge: 0,
            }
          );
        });
        card.appendChild(button);
      } else if (kind === "riddle") {
        const form = document.createElement("form");
        form.className = "task-form";

        const choiceList = document.createElement("div");
        choiceList.className = "task-choice-group";

        const errorLine = document.createElement("p");
        errorLine.className = "task-card__error";
        errorLine.style.display = "none";

        (minigame.choices || []).forEach((choice) => {
          const label = document.createElement("label");
          label.className = "checkbox";
          const input = document.createElement("input");
          input.type = "radio";
          input.name = `riddle-${scene.id}`;
          input.value = choice.id;
          label.appendChild(input);
          label.appendChild(document.createTextNode(choice.label));
          choiceList.appendChild(label);
        });

        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "button button--primary";
        submit.textContent = "Submit answer";

        form.appendChild(choiceList);
        form.appendChild(submit);
        form.appendChild(errorLine);

        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (quest.state.busy) {
            return;
          }
          const selected = form.querySelector("input[type=radio]:checked");
          if (!selected) {
            errorLine.textContent = "Choose an answer before submitting.";
            errorLine.style.display = "block";
            return;
          }

          const choice = (minigame.choices || []).find(
            (item) => item.id === selected.value
          );
          if (!choice || !choice.correct) {
            errorLine.textContent =
              minigame.failure_message ||
              "That answer doesn’t unlock the cache. Try again.";
            errorLine.style.display = "block";
            return;
          }

          errorLine.style.display = "none";
          try {
            await postSessionUpdate(
              {
                state: {
                  progress_flags: {
                    [flagKey]: {
                      status: "solved",
                      choice_id: choice.id,
                      validated_at: new Date().toISOString(),
                    },
                  },
                  last_scene: scene.id,
                },
                event: {
                  event_type: "riddle_solved",
                  payload: {
                    scene_id: scene.id,
                    choice_id: choice.id,
                  },
                },
              },
              { keepView: true }
            );
          } catch (_) {
            // handled upstream
          }
        });

        card.appendChild(form);
      } else if (kind === "illusion") {
        const info = document.createElement("p");
        info.className = "task-card__meta";
        info.textContent =
          "Shatter Eldarni’s false lights before they fade. Each tap breaks another illusion.";
        card.appendChild(info);

        const orbArea = document.createElement("div");
        orbArea.className = "focus-area";

        const lineDisplay = document.createElement("p");
        lineDisplay.className = "illusion-line";
        lineDisplay.textContent = "";

        const startButton = document.createElement("button");
        startButton.type = "button";
        startButton.className = "button button--primary";
        startButton.textContent = "Begin illusion duel";

        let hits = 0;
        let active = false;
        let timeoutId = null;

        const resetState = () => {
          active = false;
          hits = 0;
          orbArea.innerHTML = "";
          lineDisplay.textContent = "";
          if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
          }
        };

        const completeIllusion = async () => {
          try {
            await postSessionUpdate(
              {
                state: {
                  progress_flags: {
                    [flagKey]: {
                      status: "won",
                      hits,
                      validated_at: new Date().toISOString(),
                    },
                  },
                  last_scene: scene.id,
                },
                event: {
                  event_type: "illusion_battle",
                  payload: {
                    scene_id: scene.id,
                    hits,
                  },
                },
              },
              { keepView: true }
            );
          } catch (_) {
            // handled upstream
          }
        };

        const spawnOrb = () => {
          if (!active) {
            return;
          }
          orbArea.innerHTML = "";
          const orb = document.createElement("button");
          orb.type = "button";
          orb.className = "focus-orb focus-orb--illusion";
          orb.textContent = "Dispel!";
          orb.addEventListener("click", () => {
            hits += 1;
            const line = (minigame.lines || [])[hits - 1];
            if (line) {
              lineDisplay.textContent = line;
            }
            if (hits >= (minigame.orbs || 5)) {
              resetState();
              completeIllusion();
              return;
            }
            spawnOrb();
          });
          orbArea.appendChild(orb);
          if (timeoutId) {
            clearTimeout(timeoutId);
          }
          timeoutId = setTimeout(() => {
            resetState();
            quest.set({
              error: "The illusion reformed. Try again and tap faster.",
            });
          }, minigame.timeout_ms || 4000);
        };

        startButton.addEventListener("click", () => {
          if (quest.state.busy) {
            return;
          }
          resetState();
          active = true;
          spawnOrb();
        });

        card.appendChild(startButton);
        card.appendChild(lineDisplay);
        card.appendChild(orbArea);
      } else if (kind === "combat") {
        const info = document.createElement("p");
        info.className = "task-card__meta";
        info.textContent =
          "Match each symbol as it appears to shatter Dr Nat L Order’s illusion.";
        card.appendChild(info);

        const arena = document.createElement("div");
        arena.className = "combat-area";

        const symbolDisplay = document.createElement("div");
        symbolDisplay.className = "combat-symbol";
        symbolDisplay.textContent = "—";
        arena.appendChild(symbolDisplay);

        const statusLine = document.createElement("p");
        statusLine.className = "combat-status";
        statusLine.textContent = "";

        const symbols = minigame.symbols && minigame.symbols.length
          ? minigame.symbols
          : ["⚡", "🌿", "🔥"];
        const rounds = minigame.rounds || 5;
        const lines = minigame.lines || [];

        const buttonsWrapper = document.createElement("div");
        buttonsWrapper.className = "combat-buttons";

        let sequence = [];
        let index = 0;
        let active = false;

        const resetBattle = (message) => {
          active = false;
          sequence = [];
          index = 0;
          symbolDisplay.textContent = "—";
          statusLine.textContent = message || "";
          buttonsWrapper
            .querySelectorAll("button")
            .forEach((btn) => (btn.disabled = true));
          startButton.disabled = false;
        };

        const completeBattle = async () => {
          try {
            await postSessionUpdate(
              {
                state: {
                  progress_flags: {
                    [flagKey]: {
                      status: "won",
                      sequence,
                      rounds,
                      validated_at: new Date().toISOString(),
                    },
                  },
                  last_scene: scene.id,
                },
                event: {
                  event_type: "final_battle",
                  payload: {
                    scene_id: scene.id,
                    sequence,
                  },
                },
              },
              { keepView: true }
            );
            statusLine.textContent = "Dr Nat L Order’s illusion shatters!";
          } catch (_) {
            // handled upstream
          } finally {
            resetBattle();
          }
        };

        const advance = () => {
          if (index >= sequence.length) {
            completeBattle();
            return;
          }
          symbolDisplay.textContent = sequence[index];
          buttonsWrapper
            .querySelectorAll("button")
            .forEach((btn) => (btn.disabled = false));
        };

        const handleChoice = (symbol) => {
          if (!active) {
            return;
          }
          if (symbol === sequence[index]) {
            const line = lines[index];
            if (line) {
              statusLine.textContent = line;
            } else {
              statusLine.textContent = "";
            }
            index += 1;
            buttonsWrapper
              .querySelectorAll("button")
              .forEach((btn) => (btn.disabled = true));
            advance();
          } else {
            resetBattle("Dr Nat L Order grins. Try again!");
          }
        };

        symbols.forEach((symbol) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "combat-button";
          btn.textContent = symbol;
          btn.disabled = true;
          btn.addEventListener("click", () => handleChoice(symbol));
          buttonsWrapper.appendChild(btn);
        });

        const startButton = document.createElement("button");
        startButton.type = "button";
        startButton.className = "button button--primary";
        startButton.textContent = "Begin combat";
        startButton.addEventListener("click", () => {
          if (quest.state.busy) {
            return;
          }
          sequence = Array.from({ length: rounds }, () => {
            const idx = Math.floor(Math.random() * symbols.length);
            return symbols[idx];
          });
          index = 0;
          active = true;
          statusLine.textContent = "Dr Nat L Order lunges!";
          startButton.disabled = true;
          buttonsWrapper
            .querySelectorAll("button")
            .forEach((btn) => (btn.disabled = true));
          advance();
        });

        arena.appendChild(buttonsWrapper);

        card.appendChild(arena);
        card.appendChild(startButton);
        card.appendChild(statusLine);
      } else if (kind === "focus") {
        const info = document.createElement("p");
        info.className = "task-card__meta";
        info.textContent =
          "Five orbs will appear. Tap each one before it fades to impress Adellion.";
        card.appendChild(info);

        const orbArea = document.createElement("div");
        orbArea.className = "focus-area";

        const startButton = document.createElement("button");
        startButton.type = "button";
        startButton.className = "button button--primary";
        startButton.textContent = "Start focus test";

        let hits = 0;
        let active = false;
        let timeoutId = null;

        const cleanup = () => {
          active = false;
          hits = 0;
          orbArea.innerHTML = "";
          if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
          }
        };

        const spawnOrb = () => {
          if (!active) {
            return;
          }
          orbArea.innerHTML = "";
          const orb = document.createElement("button");
          orb.type = "button";
          orb.className = "focus-orb";
          orb.textContent = "Tap!";
          orb.addEventListener("click", () => {
            hits += 1;
            if (hits >= (minigame.orbs || 5)) {
              cleanup();
              completeFocus();
              return;
            }
            spawnOrb();
          });
          orbArea.appendChild(orb);
          timeoutId = setTimeout(() => {
            cleanup();
            quest.set({
              error: "The orb faded away. Try the focus test again.",
            });
          }, minigame.window_ms || 4000);
        };

        const completeFocus = async () => {
          try {
            await postSessionUpdate(
              {
                state: {
                  progress_flags: {
                    [flagKey]: {
                      status: "completed",
                      hits,
                      validated_at: new Date().toISOString(),
                    },
                  },
                  last_scene: scene.id,
                },
                event: {
                  event_type: "focus_test",
                  payload: { scene_id: scene.id, hits },
                },
              },
              { keepView: true }
            );
          } catch (_) {
            // handled upstream
          }
        };

        startButton.addEventListener("click", () => {
          if (quest.state.busy) {
            return;
          }
          cleanup();
          active = true;
          hits = 0;
          spawnOrb();
        });

        card.appendChild(startButton);
        card.appendChild(orbArea);
      } else if (kind === "quiz") {
        const form = document.createElement("form");
        form.className = "task-form";
        const answers = {};

        (minigame.questions || []).forEach((question) => {
          const block = document.createElement("div");
          block.className = "task-question";

          const prompt = document.createElement("p");
          prompt.className = "task-card__meta";
          prompt.textContent = question.prompt;
          block.appendChild(prompt);

          (question.options || []).forEach((option) => {
            const label = document.createElement("label");
            label.className = "checkbox";
            const input = document.createElement("input");
            input.type = "radio";
            input.name = `quiz-${scene.id}-${question.id}`;
            input.value = option.id;
            label.appendChild(input);
            label.appendChild(document.createTextNode(option.label));
            label.addEventListener("change", () => {
              answers[question.id] = option.id;
            });
            block.appendChild(label);
          });

          form.appendChild(block);
        });

        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "button button--primary";
        submit.textContent = "Submit answers";
        form.appendChild(submit);

        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (quest.state.busy) {
            return;
          }
          const total = (minigame.questions || []).length;
          if (
            total &&
            Object.keys(answers).length !== total
          ) {
            quest.set({
              error: "Answer every question before submitting.",
            });
            return;
          }
          try {
            await postSessionUpdate(
              {
                state: {
                  progress_flags: {
                    [flagKey]: {
                      status: "completed",
                      responses: answers,
                      validated_at: new Date().toISOString(),
                    },
                  },
                  last_scene: scene.id,
                },
                event: {
                  event_type: "mood_quiz",
                  payload: {
                    scene_id: scene.id,
                    responses: answers,
                  },
                },
              },
              { keepView: true }
            );
          } catch (_) {
            // handled upstream
          }
        });

        card.appendChild(form);
      } else if (kind === "ending") {
        const description = document.createElement("p");
        description.className = "task-card__meta";
        description.textContent =
          "Choose who receives the sigils’ power. Your decision writes the final chapter.";
        card.appendChild(description);

        const statusLine = document.createElement("p");
        statusLine.className = "combat-status";
        statusLine.textContent = "";

        const optionsWrapper = document.createElement("div");
        optionsWrapper.className = "ending-options";

        (minigame.options || []).forEach((option) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "ending-button";
          button.textContent = option.label;
          button.addEventListener("click", async () => {
            if (quest.state.busy) {
              return;
            }
            try {
              await postSessionUpdate(
                {
                  state: {
                    progress_flags: {
                      [flagKey]: {
                        status: "selected",
                        choice_id: option.id,
                        ending_id: option.ending_id || option.id,
                        epilogue: option.epilogue || [],
                        validated_at: new Date().toISOString(),
                      },
                    },
                    ending_choice: option.ending_id || option.id,
                    last_scene: scene.id,
                  },
                  event: {
                    event_type: "ending_choice",
                    payload: {
                      scene_id: scene.id,
                      ending: option.ending_id || option.id,
                    },
                  },
                },
                { keepView: true }
              );
              statusLine.textContent = "Choice recorded. The sigils respond.";
            } catch (_) {
              // handled upstream
            }
          });

          if (Array.isArray(option.epilogue) && option.epilogue.length) {
            button.dataset.epilogue = option.epilogue.join("\n");
          }

          optionsWrapper.appendChild(button);
        });

        card.appendChild(optionsWrapper);
        card.appendChild(statusLine);
      }

      tasksPanel.appendChild(card);
    });

    const progressFlags = session?.progress_flags || {};
    const currentActNumber = session?.current_act || 1;
    const nextActId = actData?.next_act;
    let nextActNumber = null;
    if (nextActId) {
      const match = /^act(\d+)/i.exec(nextActId);
      if (match) {
        nextActNumber = parseInt(match[1], 10);
      }
    }
    const requiredForNext = nextActNumber
      ? REQUIRED_FLAGS[nextActNumber] || []
      : [];
    const missingFlags = requiredForNext.filter(
      (flag) => !progressFlags || !progressFlags[flag]
    );

    const meta = document.createElement("p");
    meta.className = "screen__meta";
    meta.textContent = session
      ? `Progress checkpoint: ${session.last_scene || "Act I start"}`
      : "No quest progress yet.";

    const actions = document.createElement("div");
    actions.className = "screen__actions";

    if (nextActNumber) {
      const advanceButton = document.createElement("button");
      advanceButton.type = "button";
      advanceButton.className = "button button--secondary";
      advanceButton.textContent = `Advance to Act ${nextActNumber}`;
      advanceButton.disabled = missingFlags.length > 0 || quest.state.busy;
      advanceButton.addEventListener("click", async () => {
        if (quest.state.busy || missingFlags.length > 0) {
          return;
        }
        try {
          await postSessionUpdate(
            {
              state: {
                current_act: nextActNumber,
                last_scene: nextActId,
              },
            },
            { keepView: false }
          );
        } catch (_) {
          // handled in helper
        }
      });
      actions.appendChild(advanceButton);
    }

    const refreshButton = document.createElement("button");
    refreshButton.type = "button";
    refreshButton.className = "button button--primary";
    refreshButton.textContent = "Check for quest updates";
    refreshButton.disabled = quest.state.busy;
    refreshButton.addEventListener("click", () => {
      refreshSession();
    });

    const logoutButton = document.createElement("button");
    logoutButton.type = "button";
    logoutButton.className = "button button--ghost";
    logoutButton.textContent = "Log out";
    logoutButton.disabled = quest.state.busy;
    logoutButton.addEventListener("click", () => {
      storage.clear();
      pinVault.clear();
      quest.set({
        profile: null,
        session: null,
        pin: null,
        view: "landing",
        signinForm: {
          trainer_name: "",
          pin: "",
        },
        signup: createEmptySignup(),
        error: null,
      });
    });

    actions.appendChild(refreshButton);
    actions.appendChild(logoutButton);

    screen.appendChild(title);
    screen.appendChild(message);
    if (objectiveTitle && objectiveList) {
      screen.appendChild(objectiveTitle);
      screen.appendChild(objectiveList);
    }
    if (tasksPanel.children.length > 0) {
      screen.appendChild(tasksPanel);
    }
    if (nextActNumber && missingFlags.length > 0) {
      const notice = document.createElement("p");
      notice.className = "screen__meta screen__meta--warning";
      notice.textContent =
        "Complete the tasks above to unlock the next act.";
      screen.appendChild(notice);
    }
    screen.appendChild(meta);
    screen.appendChild(actions);
    return screen;
  };

  const renderFatal = () => {
    const screen = document.createElement("section");
    screen.className = "screen";

    const title = document.createElement("h1");
    title.className = "screen__title";
    title.textContent = "Something went wrong";

    const message = document.createElement("p");
    message.className = "screen__message";
    message.textContent =
      quest.state.error ||
      "We couldn’t load the quest data. Please try refreshing.";

    const retryButton = document.createElement("button");
    retryButton.type = "button";
    retryButton.className = "button button--primary";
    retryButton.textContent = "Retry";
    retryButton.addEventListener("click", () => {
      quest.set({ view: "loading", error: null });
      initialize();
    });

    screen.appendChild(title);
    screen.appendChild(message);
    screen.appendChild(retryButton);
    return screen;
  };

  const refreshSession = async () => {
    try {
      await postSessionUpdate({}, { keepView: true });
    } catch (error) {
      if (error instanceof APIError && error.status === 401) {
        pinVault.clear();
        quest.set({
          pin: null,
        });
      }
    }
  };

  const initialize = async () => {
    render();
    quest.set({ busy: true, error: null });

    try {
      const status = await apiRequest("/geocache/status");
      quest.set({
        status,
      });

      if (!status.enabled) {
        quest.set({
          view: "offline",
          busy: false,
        });
        return;
      }

      const story = await apiRequest("/geocache/story");

      quest.set({
        story: story && Object.keys(story).length ? story : quest.state.story,
        busy: false,
        view: quest.state.profile ? "resume" : "landing",
        signinForm: {
          trainer_name:
            quest.state.profile?.trainer_name ||
            quest.state.signinForm?.trainer_name ||
            "",
          pin: "",
        },
        error: null,
      });
    } catch (error) {
      quest.set({
        busy: false,
        view: "error",
        error: messageFromError(error),
      });
    }
  };

  // Prime initial render with whatever the server sent down.
  quest.set({
    view: "loading",
    error: initialPayload.error || null,
  });

initialize();
