const root = document.getElementById("scene-root");

if (!root) {
  throw new Error("Geocache quest root element missing.");
}

const initialPayload = (() => {
  try {
    return JSON.parse(root.dataset.initialState || "{}");
  } catch (error) {
    console.error("Failed to parse geocache initial state:", error);
    return {};
  }
})();

const STORAGE_KEY = "wotwQuest.localState.v1";

function loadSavedState() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (err) {
    console.warn("Unable to load saved quest state:", err);
    return {};
  }
}

function saveState(payload) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload || {}));
  } catch (err) {
    console.warn("Unable to persist quest state:", err);
  }
}

function clearState() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch (err) {
    console.warn("Unable to clear quest state:", err);
  }
}

function defaultSession() {
  return {
    current_act: 1,
    last_scene: null,
    branch: null,
    choices: {},
    inventory: {},
    progress_flags: {},
    ending_choice: null,
    ended_at: null,
  };
}

function mergeObjects(base, updates) {
  const result = Object.assign({}, base || {});
  Object.keys(updates || {}).forEach((key) => {
    const current = result[key];
    const update = updates[key];
    if (isPlainObject(current) && isPlainObject(update)) {
      result[key] = mergeObjects(current, update);
    } else {
      result[key] = update;
    }
  });
  return result;
}

function isPlainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function mergeSession(current, updates) {
  const base = Object.assign({}, current || defaultSession());
  const next = Object.assign({}, base);

  if (updates.current_act !== undefined && updates.current_act !== null) {
    next.current_act = parseInt(updates.current_act, 10) || next.current_act || 1;
  }
  if (updates.last_scene !== undefined) {
    next.last_scene = updates.last_scene;
  }
  if (updates.branch !== undefined) {
    next.branch = updates.branch;
  }
  if (updates.ending_choice !== undefined) {
    next.ending_choice = updates.ending_choice;
  }
  if (updates.ended_at !== undefined) {
    next.ended_at = updates.ended_at;
  }
  if (updates.progress_flags) {
    next.progress_flags = mergeObjects(next.progress_flags, updates.progress_flags);
  }
  if (updates.choices) {
    next.choices = mergeObjects(next.choices, updates.choices);
  }
  if (updates.inventory) {
    next.inventory = mergeObjects(next.inventory, updates.inventory);
  }

  return next;
}

const savedState = loadSavedState();

const quest = {
  state: {
    view: "landing",
    busy: false,
    error: null,
    story: initialPayload.story || null,
    profile: savedState.profile || null,
    session: savedState.session || null,
    sceneId: null,
  },
  set(patch) {
    this.state = Object.assign({}, this.state, patch || {});
    render();
  },
};

function createDefaultProfile(name) {
  const trimmed = (name || "").trim();
  return {
    trainer_name: trimmed || "Adventurer",
  };
}

let storyData = buildStoryIndex(quest.state.story || {});

function setStory(story) {
  storyData = buildStoryIndex(story || {});
  quest.set({ story: storyData.story });
}

function buildStoryIndex(story) {
  const acts = Array.isArray(story.acts) ? story.acts.slice() : [];
  const scenes = story.scenes || {};
  const actMap = {};
  const sceneToAct = {};
  acts.forEach((act) => {
    if (!act || !act.id) {
      return;
    }
    actMap[act.id] = act;
    const actScenes = Array.isArray(act.scenes) ? act.scenes : [];
    actScenes.forEach((sceneId) => {
      sceneToAct[sceneId] = act.id;
    });
  });
  return {
    story: story,
    acts,
    actMap,
    scenes,
    sceneToAct,
  };
}

function getActById(actId) {
  return storyData.actMap[actId] || null;
}

function getSceneById(sceneId) {
  return storyData.scenes[sceneId] || null;
}

function getActIdForScene(sceneId) {
  return storyData.sceneToAct[sceneId] || null;
}

function parseActNumber(actId) {
  if (!actId) {
    return null;
  }
  const match = String(actId).match(/act\s*(\d+)/i);
  if (!match) {
    return null;
  }
  const value = parseInt(match[1], 10);
  return Number.isNaN(value) ? null : value;
}

function determineStartScene(session) {
  const story = storyData.story;
  if (!story || !story.acts || !story.acts.length) {
    return null;
  }
  const lastScene = session && session.last_scene;
  if (lastScene && getSceneById(lastScene)) {
    return lastScene;
  }
  const actNumber = session && session.current_act;
  let targetActId = null;
  if (typeof actNumber === "number" && !Number.isNaN(actNumber)) {
    targetActId = "act" + actNumber;
  }
  if (!targetActId || !getActById(targetActId)) {
    targetActId = story.acts[0].id;
  }
  const act = getActById(targetActId);
  const actScenes = act && Array.isArray(act.scenes) ? act.scenes : [];
  return actScenes.length ? actScenes[0] : null;
}

function updateSession(sessionUpdates) {
  const merged = mergeSession(quest.state.session || defaultSession(), sessionUpdates || {});
  quest.set({ session: merged });
  saveState({ profile: quest.state.profile, session: merged });
}

function enterScene(sceneId) {
  if (!sceneId) {
    quest.set({
      view: "error",
      error: "Scene not found. Please refresh the page.",
    });
    return;
  }
  quest.set({
    view: "story",
    sceneId,
  });
}

function resolveSceneNext(scene) {
  if (!scene) {
    return null;
  }
  let ctas = [];
  if (scene.cta) {
    ctas = Array.isArray(scene.cta) ? scene.cta : [scene.cta];
  }
  for (let i = 0; i < ctas.length; i += 1) {
    const cta = ctas[i];
    if (cta && (cta.next || cta.href)) {
      return cta;
    }
  }
  const fallback = determineDefaultNext(scene.id);
  if (fallback) {
    return { label: "Continue", next: fallback };
  }
  return null;
}

function determineDefaultNext(sceneId) {
  const actId = getActIdForScene(sceneId);
  const act = getActById(actId);
  if (!act) {
    return null;
  }
  const actScenes = Array.isArray(act.scenes) ? act.scenes : [];
  const index = actScenes.indexOf(sceneId);
  if (index >= 0 && index < actScenes.length - 1) {
    return actScenes[index + 1];
  }
  if (act.next_act) {
    const nextAct = getActById(act.next_act);
    const nextScenes = nextAct && Array.isArray(nextAct.scenes) ? nextAct.scenes : [];
    return nextScenes.length ? nextScenes[0] : null;
  }
  return null;
}

function completeScene(scene, options) {
  const resolvedNext = resolveSceneNext(scene);
  options = options || {};
  const nextSceneId =
    options.nextSceneId !== undefined
      ? options.nextSceneId
      : options.next
      ? options.next
      : resolvedNext && resolvedNext.next;
  const href = options.href !== undefined ? options.href : resolvedNext && resolvedNext.href;
  const extraState = mergeObjects(
    {},
    options.state || {
      progress_flags: {},
    }
  );

  if (nextSceneId) {
    extraState.last_scene = nextSceneId;
    const nextActId = getActIdForScene(nextSceneId) || getActIdForScene(scene.id);
    const actNumber = parseActNumber(nextActId);
    if (actNumber) {
      extraState.current_act = actNumber;
    }
  } else {
    extraState.last_scene = scene.id;
  }

  updateSession(extraState);

  if (href) {
    window.location.href = href;
    return;
  }
  if (nextSceneId) {
    enterScene(nextSceneId);
  } else {
    quest.set({
      view: "story",
      sceneId: scene.id,
    });
  }
}

function startQuest(profile, fresh) {
  const newProfile = profile || createDefaultProfile("");
  let session = quest.state.session || defaultSession();
  if (fresh || !session) {
    session = defaultSession();
  }
  quest.set({
    profile: newProfile,
    session,
  });
  saveState({ profile: newProfile, session });
  return startQuestFromSession(session, { sync: false });
}

function startQuestFromSession(sessionData, options) {
  const ensureStory =
    storyData.acts && storyData.acts.length
      ? Promise.resolve()
      : ensureStoryLoaded().catch((error) => {
          quest.set({ error: error ? String(error) : "Unable to load quest story." });
          throw error;
        });
  return ensureStory.then(() => {
    let startScene = determineStartScene(sessionData || defaultSession());
    if (!startScene) {
      const acts = storyData.acts || [];
      for (let i = 0; i < acts.length; i += 1) {
        const actScenes = Array.isArray(acts[i].scenes) ? acts[i].scenes : [];
        if (actScenes.length) {
          startScene = actScenes[0];
          break;
        }
      }
    }
    if (!startScene) {
      quest.set({
        view: "story",
        sceneId: null,
        error: "Unable to locate the next scene. Please speak to an event host.",
      });
      return;
    }
    const syncFlag =
      options && Object.prototype.hasOwnProperty.call(options, "sync") ? options.sync : !sessionData.last_scene;
    if (syncFlag) {
      const actId = getActIdForScene(startScene);
      const actNumber = parseActNumber(actId) || 1;
      updateSession({
        current_act: actNumber,
        last_scene: startScene,
      });
    }
    enterScene(startScene);
  });
}

function ensureStoryLoaded() {
  if (storyData.acts && storyData.acts.length) {
    return Promise.resolve();
  }
  if (initialPayload.story) {
    setStory(initialPayload.story);
    return Promise.resolve();
  }
  return fetch("/geocache/manifest")
    .then((resp) => resp.json())
    .then((payload) => {
      if (payload && payload.story) {
        setStory(payload.story);
      }
    });
}

function renderLanding() {
  const container = createElement("section", { className: "screen screen--landing" });
  container.appendChild(
    createElement("h1", { className: "screen__title", text: "Whispers of the Wild Court" })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__subtitle",
      text: "A live geocache adventure. Start a fresh run or resume your previous progress.",
    })
  );
  const actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Begin quest", "button button--primary", () => {
      quest.set({ view: "setup", error: null });
    })
  );
  if (quest.state.session && quest.state.profile) {
    actions.appendChild(
      createButton("Resume quest", "button button--secondary", () => {
        startQuestFromSession(quest.state.session, { sync: false });
      })
    );
  }
  actions.appendChild(
    createButton("What is this?", "button button--ghost", () => {
      quest.set({ view: "about", error: null });
    })
  );
  container.appendChild(actions);
  return container;
}

function renderAbout() {
  const container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", { className: "screen__title", text: "What is Whispers of the Wild Court?" })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__message",
      text:
        "A story-led geocache written for GO Wild Doncaster. Find hidden Sigils, solve riddles, and decide the fate of the Wild Court.",
    })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__meta",
      text: "You can play entirely offline in this browser tab. Progress is saved on this device.",
    })
  );
  const actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Back", "button button--primary", () => {
      quest.set({ view: "landing", error: null });
    })
  );
  container.appendChild(actions);
  return container;
}

function renderSetup() {
  const container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", { className: "screen__title", text: "Create your quest pass" })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__subtitle",
      text: "Enter a trainer name (optional) so your save slot is easy to spot later.",
    })
  );

  const form = createElement("form", { className: "form" });
  const field = createElement("div", { className: "field" });
  const label = createElement("label", { text: "Trainer name" });
  label.setAttribute("for", "setup_trainer");
  const input = createElement("input", {
    className: "input",
    attrs: {
      id: "setup_trainer",
      type: "text",
      maxlength: "32",
      placeholder: "e.g. Tylaethetrainer",
    },
  });
  field.appendChild(label);
  field.appendChild(input);
  form.appendChild(field);

  const actions = createElement("div", { className: "screen__actions" });
  const startButton = createButton("Start quest", "button button--primary");
  startButton.setAttribute("type", "submit");
  actions.appendChild(startButton);
  actions.appendChild(
    createButton("Cancel", "button button--ghost", () => {
      quest.set({ view: "landing", error: null });
    })
  );
  form.appendChild(actions);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const profile = createDefaultProfile(input.value);
    startQuest(profile, true);
  });

  container.appendChild(form);
  return container;
}

function renderStory() {
  const sceneId = quest.state.sceneId;
  if (!sceneId) {
    return renderStoryPlaceholder("No quest scene selected yet. Press the continue button to begin.");
  }
  const scene = getSceneById(sceneId);
  if (!scene) {
    return renderStoryPlaceholder("Scene data missing. Please speak to an event host.");
  }
  const actId = getActIdForScene(sceneId);
  const act = getActById(actId);

  const container = createElement("section", { className: "story" });
  const header = createElement("header", { className: "story__header" });
  header.appendChild(
    createElement("p", {
      className: "story__act",
      text: act ? act.title : "Quest",
    })
  );
  container.appendChild(header);

  const content = createElement("div", { className: "story__content" });

  if (scene.art) {
    const artWrapper = createElement("div", { className: "story__art" });
    const artImg = createElement("img", {
      attrs: {
        src: resolveAssetUrl(scene.art),
        alt: scene.title || scene.id,
      },
    });
    artWrapper.appendChild(artImg);
    content.appendChild(artWrapper);
  }

  if (scene.speaker) {
    content.appendChild(
      createElement("h2", { className: "story__speaker", text: scene.speaker })
    );
  }

  const textBlocks = Array.isArray(scene.text) ? scene.text : [];
  for (let i = 0; i < textBlocks.length; i += 1) {
    content.appendChild(
      createElement("p", { className: "story__line", text: textBlocks[i] })
    );
  }

  container.appendChild(content);
  if (scene.type === "minigame") {
    container.appendChild(buildMinigameView(scene));
  } else {
    const actions = createElement("div", { className: "story__actions" });
    let ctas = [];
    if (scene.cta) {
      ctas = Array.isArray(scene.cta) ? scene.cta : [scene.cta];
    }
    if (!ctas.length) {
      ctas.push({
        label: "Continue",
        next: determineDefaultNext(sceneId),
      });
    }
    ctas.forEach((cta) => {
      if (!cta || !cta.label) {
        return;
      }
      actions.appendChild(
        createButton(cta.label, "button button--primary", () => {
          if (cta.href) {
            window.location.href = cta.href;
            return;
          }
          completeScene(scene, { nextSceneId: cta.next });
        })
      );
    });
    container.appendChild(actions);
  }

  return container;
}

function renderStoryPlaceholder(message) {
  const container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", { className: "screen__title", text: "Quest ready" })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__message",
      text: message,
    })
  );
  const actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Back to start", "button button--primary", () => {
      quest.set({ view: "landing", error: null });
    })
  );
  container.appendChild(actions);
  return container;
}

function renderErrorScreen() {
  const container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", {
      className: "screen__title",
      text: "Something went wrong",
    })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__message",
      text: quest.state.error || "We couldn’t load the quest. Please refresh the page.",
    })
  );
  const actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Retry", "button button--primary", () => {
      quest.set({ view: "landing", error: null });
    })
  );
  container.appendChild(actions);
  return container;
}

function render() {
  const view = quest.state.view;
  const busy = quest.state.busy;
  const error = quest.state.error;

  let screen;
  if (view === "landing") {
    screen = renderLanding();
  } else if (view === "about") {
    screen = renderAbout();
  } else if (view === "setup") {
    screen = renderSetup();
  } else if (view === "resume") {
    startQuestFromSession(quest.state.session || defaultSession(), { sync: false });
    return;
  } else if (view === "story") {
    screen = renderStory();
  } else {
    screen = renderErrorScreen();
  }

  if (error && screen && !screen.querySelector(".screen__error")) {
    const banner = createElement("div", { className: "screen__error", text: error });
    screen.insertBefore(banner, screen.firstChild || null);
  }
  if (busy) {
    screen.classList.add("screen--busy");
  }

  root.innerHTML = "";
  root.appendChild(screen);
}

function buildMinigameView(scene) {
  const minigame = scene.minigame || {};
  const kind = (minigame.kind || "").toLowerCase();
  const container = createElement("div", { className: "minigame minigame--" + kind });
  const banner = createElement("div", { className: "minigame__status" });
  container.appendChild(banner);

  if (kind === "artifact_scan") {
    renderArtifactMinigame(scene, minigame, container, banner);
  } else if (kind === "location") {
    renderLocationMinigame(scene, minigame, container, banner);
  } else if (kind === "mosaic") {
    renderMosaicMinigame(scene, minigame, container, banner);
  } else if (kind === "quiz") {
    renderQuizMinigame(scene, minigame, container, banner);
  } else if (kind === "reflex") {
    renderReflexMinigame(scene, minigame, container, banner);
  } else if (kind === "pattern") {
    renderPatternMinigame(scene, minigame, container, banner);
  } else if (kind === "ending_choice") {
    renderEndingChoiceMinigame(scene, minigame, container, banner);
  } else {
    banner.textContent = "Interactive challenge coming soon. Ask an event host for instructions.";
    const fallback = createElement("div", { className: "story__actions story__actions--minigame" });
    fallback.appendChild(
      createButton("Continue", "button button--primary", () => {
        completeScene(scene, {});
      })
    );
    container.appendChild(fallback);
  }
  return container;
}

function renderArtifactMinigame(scene, minigame, container, banner) {
  const flagKey = minigame.success_flag || minigame.artifact_slug || scene.id;
  const progress = getProgressEntry(flagKey);

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text:
        minigame.success_text ||
        minigame.code_hint ||
        "Enter the artifact code or tap the NFC tag attached to the item.",
    })
  );

  const form = createElement("form", { className: "minigame__form" });
  const input = createElement("input", {
    className: "input",
    attrs: {
      type: "text",
      maxlength: "12",
      placeholder: "Enter artifact code",
    },
  });
  const button = createButton("Validate code", "button button--primary");
  button.setAttribute("type", "submit");
  form.appendChild(input);
  form.appendChild(button);

  const manual = createElement("div", { className: "minigame__actions" });
  const manualButton = createButton("Mark as complete (event assist)", "button button--secondary");
  manual.appendChild(manualButton);

  const actions = createElement("div", { className: "story__actions story__actions--minigame" });

  function markComplete(method, codeValue) {
    if (progress) {
      return;
    }
    banner.textContent = "Artifact logged!";
    const entry = {
      status: "artifact_scanned",
      artifact_slug: minigame.artifact_slug || scene.id,
      method: method,
      code: codeValue || null,
      validated_at: new Date().toISOString(),
    };
    completeScene(scene, {
      state: {
        progress_flags: {
          [flagKey]: entry,
        },
      },
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (progress) {
      return;
    }
    const value = (input.value || "").trim();
    if (!value) {
      banner.textContent = "Enter the artifact code to continue.";
      return;
    }
    const expected = minigame.code ? String(minigame.code).trim() : "";
    if (expected && expected.length && value !== expected) {
      banner.textContent = "That code doesn’t match. Double-check the artifact.";
      return;
    }
    markComplete("code", value);
  });

  manualButton.addEventListener("click", () => {
    if (progress) {
      return;
    }
    if (!window.confirm("Confirm with an event host before marking this artifact as complete.")) {
      return;
    }
    markComplete("manual", null);
  });

  if (progress) {
    banner.textContent = "Artifact already scanned. Great work!";
    const continueButton = createButton("Continue", "button button--primary", () => {
      completeScene(scene, {});
    });
    actions.appendChild(continueButton);
  } else {
    container.appendChild(form);
    container.appendChild(manual);
  }
  container.appendChild(actions);
}

function renderLocationMinigame(scene, minigame, container, banner) {
  const flagKey = minigame.success_flag || minigame.location_id || scene.id;
  const progress = getProgressEntry(flagKey);

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text:
        minigame.prompt ||
        "Travel to the highlighted location. Use GPS check-in when you’re within range, or ask an event host.",
    })
  );

  const statusText = createElement("p", { className: "minigame__status-text" });
  container.appendChild(statusText);

  function record(method, coords) {
    if (progress) {
      return;
    }
    banner.textContent = "Check-in recorded!";
    const entry = Object.assign(
      {
        status: "location_check_in",
        location_id: minigame.location_id || scene.id,
        method: method,
        validated_at: new Date().toISOString(),
      },
      coords || {}
    );
    completeScene(scene, {
      state: {
        progress_flags: {
          [flagKey]: entry,
        },
      },
    });
  }

  function handleGPS() {
    if (!navigator.geolocation) {
      statusText.textContent = "GPS unavailable on this device.";
      return;
    }
    statusText.textContent = "Checking your position…";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const lat = position.coords.latitude;
        const lng = position.coords.longitude;
        const accuracy = position.coords.accuracy || null;
        const targetLat = minigame.latitude;
        const targetLng = minigame.longitude;
        const radius = minigame.radius_m || 75;
        const distance = calculateDistanceMeters(lat, lng, targetLat, targetLng);
        if (distance <= radius + 10) {
          statusText.textContent = "Within range! (" + formatDistance(distance) + ")";
          record("gps", {
            latitude: lat,
            longitude: lng,
            accuracy_m: accuracy,
          });
        } else {
          statusText.textContent =
            "You’re " + formatDistance(distance) + " away. Move closer to the quest marker.";
        }
      },
      () => {
        statusText.textContent = "Unable to read your location. Try again or ask an event host.";
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
    );
  }

  function handleManual() {
    if (!window.confirm("Confirm with an event host before marking this location as complete.")) {
      return;
    }
    record("manual", {});
  }

  const actions = createElement("div", { className: "story__actions story__actions--minigame" });
  if (progress) {
    banner.textContent = "Location already checked in.";
    actions.appendChild(
      createButton("Continue", "button button--primary", () => {
        completeScene(scene, {});
      })
    );
  } else {
    actions.appendChild(createButton("Check in with GPS", "button button--primary", handleGPS));
    actions.appendChild(createButton("I’m on-site – mark complete", "button button--secondary", handleManual));
  }
  container.appendChild(actions);
}

function renderMosaicMinigame(scene, minigame, container, banner) {
  const flagKey = minigame.success_flag || scene.id;
  const progress = getProgressEntry(flagKey);
  const totalPieces = parseInt(minigame.pieces, 10) || 6;

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Tap each shard to place it back into the compass frame.",
    })
  );

  const board = createElement("div", { className: "mosaic" });
  const pool = createElement("div", { className: "mosaic__pool" });
  const frame = createElement("div", { className: "mosaic__frame" });
  board.appendChild(pool);
  board.appendChild(frame);
  container.appendChild(board);

  function finish() {
    banner.textContent = "Compass restored!";
    completeScene(scene, {
      state: {
        progress_flags: {
          [flagKey]: {
            status: "puzzle_complete",
            puzzle_id: minigame.puzzle_id || scene.id,
            pieces: totalPieces,
            validated_at: new Date().toISOString(),
          },
        },
      },
    });
  }

  if (progress) {
    banner.textContent = "Puzzle already solved.";
    const done = createElement("div", { className: "story__actions story__actions--minigame" });
    done.appendChild(
      createButton("Continue", "button button--primary", () => {
        completeScene(scene, {});
      })
    );
    container.appendChild(done);
    return;
  }

  const ids = [];
  for (let i = 1; i <= totalPieces; i += 1) {
    ids.push(i);
  }
  ids.sort(() => Math.random() - 0.5);

  let placed = 0;
  ids.forEach((id) => {
    const piece = createElement("button", {
      className: "mosaic__piece",
      text: String(id),
    });
    piece.addEventListener("click", () => {
      if (piece.dataset.placed === "true") {
        return;
      }
      piece.dataset.placed = "true";
      piece.classList.add("mosaic__piece--placed");
      frame.appendChild(piece);
      placed += 1;
      if (placed >= totalPieces) {
        finish();
      }
    });
    pool.appendChild(piece);
  });
}

function renderQuizMinigame(scene, minigame, container, banner) {
  const flagKey = minigame.success_flag || scene.id;
  const progress = getProgressEntry(flagKey);

  if (progress) {
    banner.textContent = "Quiz already completed.";
    const done = createElement("div", { className: "story__actions story__actions--minigame" });
    done.appendChild(
      createButton("Continue", "button button--primary", () => {
        completeScene(scene, {});
      })
    );
    container.appendChild(done);
    return;
  }

  if (Array.isArray(minigame.questions) && minigame.questions.length) {
    renderMultiQuestionQuiz(scene, minigame, container, banner);
    return;
  }

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Choose the correct answer to continue.",
    })
  );

  const form = createElement("form", { className: "minigame__form" });
  const choices = Array.isArray(minigame.choices) ? minigame.choices : [];
  choices.forEach((choice, index) => {
    const label = createElement("label", { className: "choice" });
    const radio = createElement("input", {
      attrs: {
        type: "radio",
        name: "quiz-choice",
        value: choice.id || String(index),
        required: "required",
      },
    });
    label.appendChild(radio);
    label.appendChild(createElement("span", { text: choice.label || choice.id || String(index) }));
    form.appendChild(label);
  });
  const submitButton = createButton("Submit answer", "button button--primary");
  submitButton.setAttribute("type", "submit");
  form.appendChild(submitButton);
  container.appendChild(form);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const selected = form.querySelector("input[name='quiz-choice']:checked");
    if (!selected) {
      banner.textContent = "Select an answer first.";
      return;
    }
    const selectedId = selected.value;
    const selectedChoice = choices.find((c, idx) => (c.id || String(idx)) === selectedId);
    if (!selectedChoice) {
      banner.textContent = "Select an answer first.";
      return;
    }
    if (selectedChoice.correct) {
      banner.textContent = "Correct! Updating quest progress…";
      completeScene(scene, {
        state: {
          progress_flags: {
            [flagKey]: {
              status: "quiz_correct",
              choice_id: selectedChoice.id || selectedId,
              validated_at: new Date().toISOString(),
            },
          },
        },
      });
    } else {
      banner.textContent = "That isn’t right. Try again after another look around.";
    }
  });
}

function renderMultiQuestionQuiz(scene, minigame, container, banner) {
  const questions = minigame.questions || [];
  const flagKey = minigame.success_flag || scene.id;

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Answer each prompt. The Oracles will interpret your vibe.",
    })
  );
  const form = createElement("form", { className: "minigame__form minigame__form--quiz" });
  questions.forEach((question, questionIndex) => {
    const fieldset = createElement("fieldset", { className: "quiz__question" });
    const legend = createElement("legend", { text: question.prompt || "Question" });
    fieldset.appendChild(legend);
    const choices = Array.isArray(question.choices) ? question.choices : [];
    choices.forEach((choice, choiceIndex) => {
      const label = createElement("label", { className: "choice" });
      const radio = createElement("input", {
        attrs: {
          type: "radio",
          name: "quiz_" + (question.id || String(questionIndex)),
          value: choice.id || String(choiceIndex),
          required: "required",
        },
      });
      label.appendChild(radio);
      label.appendChild(createElement("span", { text: choice.label || choice.id || String(choiceIndex) }));
      fieldset.appendChild(label);
    });
    form.appendChild(fieldset);
  });
  const submitButton = createButton("Submit responses", "button button--primary");
  submitButton.setAttribute("type", "submit");
  form.appendChild(submitButton);
  container.appendChild(form);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const tally = {};
    const answers = [];
    for (let i = 0; i < questions.length; i += 1) {
      const question = questions[i];
      const fieldName = "quiz_" + (question.id || String(i));
      const selected = form.querySelector("input[name='" + fieldName + "']:checked");
      if (!selected) {
        banner.textContent = "Answer every question before submitting.";
        return;
      }
      const choiceId = selected.value;
      tally[choiceId] = (tally[choiceId] || 0) + 1;
      answers.push({ question_id: question.id || String(i), choice_id: choiceId });
    }
    let dominantChoice = null;
    let dominantScore = -1;
    Object.keys(tally).forEach((key) => {
      if (tally[key] > dominantScore) {
        dominantScore = tally[key];
        dominantChoice = key;
      }
    });
    const results = minigame.results || {};
    const interpretation = results[dominantChoice] || "The Oracles are still deciding…";
    banner.textContent = interpretation;
    completeScene(scene, {
      state: {
        progress_flags: {
          [flagKey]: {
            status: "quiz_completed",
            dominant_choice: dominantChoice,
            answers: answers,
            interpretation: interpretation,
            validated_at: new Date().toISOString(),
          },
        },
      },
    });
  });
}

function renderReflexMinigame(scene, minigame, container, banner) {
  const flagKey = minigame.success_flag || scene.id;
  const progress = getProgressEntry(flagKey);
  const rounds = parseInt(minigame.rounds, 10) || 5;

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Tap the glowing orb the moment it appears.",
    })
  );

  const arena = createElement("div", { className: "reflex" });
  container.appendChild(arena);
  const startButton = createButton(progress ? "Focus test complete" : "Start focus test", "button button--primary");
  arena.appendChild(startButton);
  const counter = createElement("p", { className: "minigame__status-text" });
  container.appendChild(counter);

  if (progress) {
    banner.textContent = "Focus test already completed.";
    const done = createElement("div", { className: "story__actions story__actions--minigame" });
    done.appendChild(
      createButton("Continue", "button button--primary", () => {
        completeScene(scene, {});
      })
    );
    container.appendChild(done);
    return;
  }

  startButton.addEventListener("click", () => {
    startButton.disabled = true;
    const orb = createElement("div", { className: "reflex__orb" });
    arena.appendChild(orb);
    let round = 0;

    function nextRound() {
      round += 1;
      if (round > rounds) {
        banner.textContent = "You passed the focus test!";
        completeScene(scene, {
          state: {
            progress_flags: {
              [flagKey]: {
                status: "reflex_complete",
                rounds: rounds,
                validated_at: new Date().toISOString(),
              },
            },
          },
        });
        return;
      }
      counter.textContent = "Round " + round + " of " + rounds;
      const x = Math.random() * 80 + 10;
      const y = Math.random() * 60 + 20;
      orb.style.left = x + "%";
      orb.style.top = y + "%";
      orb.classList.add("is-visible");
    }

    orb.addEventListener("click", () => {
      if (!orb.classList.contains("is-visible")) {
        return;
      }
      orb.classList.remove("is-visible");
      setTimeout(nextRound, 400);
    });

    nextRound();
  });
}

function renderPatternMinigame(scene, minigame, container, banner) {
  const flagKey = minigame.success_flag || scene.id;
  const progress = getProgressEntry(flagKey);
  const rounds = parseInt(minigame.rounds, 10) || 5;
  const symbols = ["⚡️", "🌿", "🔥"];

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Tap the matching sigil to disrupt the illusion.",
    })
  );

  const arena = createElement("div", { className: "pattern" });
  const prompt = createElement("div", { className: "pattern__symbol", text: "?" });
  arena.appendChild(prompt);
  const controls = createElement("div", { className: "pattern__choices" });
  symbols.forEach((symbol) => {
    controls.appendChild(
      createButton(symbol, "button button--secondary", () => {
        handleChoice(symbol);
      })
    );
  });
  arena.appendChild(controls);
  container.appendChild(arena);
  const statusLine = createElement("p", { className: "minigame__status-text" });
  container.appendChild(statusLine);

  if (progress) {
    banner.textContent = "Illusion already broken.";
    const done = createElement("div", { className: "story__actions story__actions--minigame" });
    done.appendChild(
      createButton("Continue", "button button--primary", () => {
        completeScene(scene, {});
      })
    );
    container.appendChild(done);
    return;
  }

  let round = 0;
  let currentSymbol = null;

  function nextRound() {
    round += 1;
    if (round > rounds) {
      banner.textContent = "Illusion circuit shattered!";
      completeScene(scene, {
        state: {
          progress_flags: {
            [flagKey]: {
              status: "pattern_complete",
              rounds: rounds,
              validated_at: new Date().toISOString(),
            },
          },
        },
      });
      return;
    }
    currentSymbol = symbols[Math.floor(Math.random() * symbols.length)];
    prompt.textContent = currentSymbol;
    statusLine.textContent = "Round " + round + " of " + rounds;
  }

  function handleChoice(choice) {
    if (!currentSymbol) {
      nextRound();
      return;
    }
    if (choice === currentSymbol) {
      banner.textContent = "Direct hit!";
      nextRound();
    } else {
      banner.textContent = "Missed. Resetting current round.";
      round -= 1;
      nextRound();
    }
  }

  nextRound();
}

function renderEndingChoiceMinigame(scene, minigame, container, banner) {
  const choices = Array.isArray(minigame.choices) ? minigame.choices : [];
  const flagKey = minigame.success_flag || scene.id;
  banner.textContent = "Choose where the Sigils’ power flows.";
  const actions = createElement("div", { className: "story__actions story__actions--minigame" });
  choices.forEach((option) => {
    if (!option) {
      return;
    }
    actions.appendChild(
      createButton(option.label || option.id, "button button--primary", () => {
        completeScene(scene, {
          nextSceneId: option.next_scene || option.next,
          state: {
            progress_flags: {
              [flagKey]: {
                status: "ending_selected",
                choice_id: option.id || option.label || "unknown",
                validated_at: new Date().toISOString(),
              },
            },
          },
        });
      })
    );
  });
  container.appendChild(actions);
}

function calculateDistanceMeters(latA, lngA, latB, lngB) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const earthRadius = 6371000;
  const dLat = toRad(latB - latA);
  const dLng = toRad(lngB - lngA);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(latA)) * Math.cos(toRad(latB)) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return earthRadius * c;
}

function formatDistance(distance) {
  if (distance >= 1000) {
    return (distance / 1000).toFixed(2) + " km";
  }
  return Math.round(distance) + " m";
}

function getProgressEntry(flagKey) {
  const session = quest.state.session || {};
  const flags = session.progress_flags || {};
  return flags[flagKey] || null;
}

function createElement(tagName, options) {
  const element = document.createElement(tagName);
  if (!options) {
    return element;
  }
  if (options.className) {
    element.className = options.className;
  }
  if (typeof options.text === "string") {
    element.textContent = options.text;
  }
  if (typeof options.html === "string") {
    element.innerHTML = options.html;
  }
  if (options.attrs) {
    Object.keys(options.attrs).forEach((key) => {
      const value = options.attrs[key];
      if (value !== undefined && value !== null) {
        element.setAttribute(key, value);
      }
    });
  }
  if (typeof options.onClick === "function") {
    element.addEventListener("click", options.onClick);
  }
  return element;
}

function createButton(label, className, handler) {
  return createElement("button", {
    className: className || "button",
    text: label,
    onClick: handler,
  });
}

function resolveAssetUrl(path) {
  if (!path || typeof path !== "string") {
    return null;
  }
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const normalized = path.replace(/^\/+/, "");
  return "/static/" + normalized;
}

ensureStoryLoaded().then(() => {
  if (!quest.state.story && initialPayload.story) {
    setStory(initialPayload.story);
  }
  if (!quest.state.session) {
    quest.set({ session: defaultSession() });
  }
  if (!quest.state.profile && savedState.profile) {
    quest.set({ profile: savedState.profile });
  }
  render();
});
