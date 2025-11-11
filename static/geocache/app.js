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

function getValue(source) {
  var result = source;
  for (var i = 1; i < arguments.length; i += 1) {
    if (result === undefined || result === null) {
      return undefined;
    }
    var key = arguments[i];
    result = result[key];
  }
  return result;
}

function callIfFunction(fn, context) {
  if (typeof fn !== "function") {
    return undefined;
  }
  var args = Array.prototype.slice.call(arguments, 2);
  return fn.apply(context || null, args);
}

function createElement(tagName, options) {
  var element = document.createElement(tagName);
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
    for (var key in options.attrs) {
      if (Object.prototype.hasOwnProperty.call(options.attrs, key)) {
        var value = options.attrs[key];
        if (value !== undefined && value !== null) {
          element.setAttribute(key, value);
        }
      }
    }
  }
  if (typeof options.onClick === "function") {
    element.addEventListener("click", options.onClick);
  }
  return element;
}

function createButton(label, className, onClick) {
  return createElement("button", {
    className: className || "button",
    text: label,
    onClick: onClick,
  });
}

var supportsLocalStorage = (function () {
  try {
    var key = "__wotw_storage_test__";
    window.localStorage.setItem(key, "1");
    window.localStorage.removeItem(key);
    return true;
  } catch (error) {
    console.warn("Local storage unavailable:", error);
    return false;
  }
})();

var supportsSessionStorage = (function () {
  try {
    var key = "__wotw_session_test__";
    window.sessionStorage.setItem(key, "1");
    window.sessionStorage.removeItem(key);
    return true;
  } catch (error) {
    console.warn("Session storage unavailable:", error);
    return false;
  }
})();

var STORAGE_KEY = "wotwQuestState.v1";
var PIN_KEY = "wotwQuestPin";

var storage = {
  load: function () {
    if (!supportsLocalStorage) {
      return {};
    }
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return {};
      }
      var parsed = JSON.parse(raw);
      return {
        profile: parsed.profile || null,
        session: parsed.session || null,
      };
    } catch (error) {
      console.warn("Failed to load quest storage:", error);
      return {};
    }
  },
  save: function (data) {
    if (!supportsLocalStorage) {
      return;
    }
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          profile: data.profile || null,
          session: data.session || null,
        })
      );
    } catch (error) {
      console.warn("Failed to persist quest storage:", error);
    }
  },
  clear: function () {
    if (!supportsLocalStorage) {
      return;
    }
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      console.warn("Failed to clear quest storage:", error);
    }
  },
};

var pinVault = {
  remember: function (pin) {
    if (!supportsSessionStorage) {
      return;
    }
    try {
      window.sessionStorage.setItem(PIN_KEY, pin);
    } catch (error) {
      console.warn("Failed to store quest PIN:", error);
    }
  },
  get: function () {
    if (!supportsSessionStorage) {
      return null;
    }
    try {
      return window.sessionStorage.getItem(PIN_KEY);
    } catch (error) {
      console.warn("Failed to read quest PIN:", error);
      return null;
    }
  },
  clear: function () {
    if (!supportsSessionStorage) {
      return;
    }
    try {
      window.sessionStorage.removeItem(PIN_KEY);
    } catch (error) {
      console.warn("Failed to clear quest PIN:", error);
    }
  },
};

var savedState = storage.load();
var savedPin = pinVault.get();

const quest = {
  state: {
    view: initialPayload.view || (savedState.profile ? "resume" : "landing"),
    busy: false,
    error: null,
    story: initialPayload.story || null,
    profile: initialPayload.profile || savedState.profile || null,
    session: initialPayload.session || savedState.session || null,
    pin: savedPin || null,
    sceneId: null,
    sessionTrainer: (initialPayload.session_trainer || "").trim() || null,
    sessionAuthAttempted: false,
    useSessionAuth: false,
  },
  set(patch) {
    this.state = Object.assign({}, this.state, patch || {});
    render();
  },
};

var storyData = buildStoryIndex(quest.state.story || {});

function setStory(story) {
  storyData = buildStoryIndex(story || {});
  quest.set({ story: storyData.story });
}

function buildStoryIndex(story) {
  var acts = Array.isArray(story.acts) ? story.acts.slice() : [];
  var scenes = story.scenes || {};
  var actMap = {};
  var sceneToAct = {};
  for (var i = 0; i < acts.length; i += 1) {
    var act = acts[i];
    if (!act || !act.id) {
      continue;
    }
    actMap[act.id] = act;
    var actScenes = Array.isArray(act.scenes) ? act.scenes : [];
    for (var j = 0; j < actScenes.length; j += 1) {
      sceneToAct[actScenes[j]] = act.id;
    }
  }
  return {
    story: story,
    acts: acts,
    actMap: actMap,
    scenes: scenes,
    sceneToAct: sceneToAct,
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
  var match = String(actId).match(/act\s*(\d+)/i);
  if (!match) {
    return null;
  }
  var value = parseInt(match[1], 10);
  return Number.isNaN(value) ? null : value;
}

function messageFromError(error) {
  if (!error) {
    return "Unexpected error.";
  }
  if (error instanceof APIError) {
    return (
      getValue(error.payload, "detail") ||
      getValue(error.payload, "error") ||
      error.message ||
      "Request failed."
    );
  }
  if (error instanceof Error) {
    return error.message || "Unexpected error.";
  }
  return String(error);
}

class APIError extends Error {
  constructor(message, payload, status) {
    super(message);
    this.payload = payload || {};
    this.status = status || 500;
  }
}

function apiRequest(url, options) {
  var opts = Object.assign({ method: "GET" }, options || {});
  var isFormData = opts.body instanceof FormData;
  var headers = opts.headers || {};
  if (!isFormData) {
    headers["Content-Type"] = "application/json";
  }
  opts.headers = headers;
  if (opts.body && !isFormData && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }
  return fetch(url, opts)
    .then(function (response) {
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (payload) {
          if (!response.ok) {
            var message =
              (payload && (payload.detail || payload.error)) ||
              "Request failed (" + response.status + ")";
            throw new APIError(message, payload, response.status);
          }
          return payload || {};
        });
    });
}

function createProfileRequest(payload) {
  return apiRequest("/geocache/profile", {
    method: "POST",
    body: payload,
  });
}

function updateSessionRequest(body) {
  return apiRequest("/geocache/session", {
    method: "POST",
    body: body,
  });
}

function fetchManifest() {
  return apiRequest("/geocache/manifest", { method: "GET" }).then(function (payload) {
    if (payload && payload.story) {
      setStory(payload.story);
    }
    return payload;
  });
}

function determineStartScene(session) {
  var story = storyData.story;
  if (!story || !story.acts || !story.acts.length) {
    return null;
  }
  var lastScene = getValue(session, "last_scene");
  if (lastScene && getSceneById(lastScene)) {
    return lastScene;
  }
  var actNumber = getValue(session, "current_act");
  var targetActId = null;
  if (typeof actNumber === "number" && !Number.isNaN(actNumber)) {
    targetActId = "act" + actNumber;
  }
  if (!targetActId || !getActById(targetActId)) {
    targetActId = story.acts[0].id;
  }
  var act = getActById(targetActId);
  var actScenes = act && Array.isArray(act.scenes) ? act.scenes : [];
  return actScenes.length ? actScenes[0] : null;
}

function syncScene(sceneId, eventKey, extraState) {
  if (!quest.state.profile) {
    return Promise.resolve();
  }
  var activePin = quest.state.pin || "";
  var useSessionAuth = !!quest.state.useSessionAuth || (!activePin && !!quest.state.sessionTrainer);
  if (!activePin && !useSessionAuth) {
    quest.set({
      error: "Enter your quest PIN to continue syncing progress.",
    });
    return Promise.resolve();
  }
  var actId = getActIdForScene(sceneId);
  var actNumber = parseActNumber(actId);
  var statePayload = {
    current_act: actNumber || getValue(quest.state.session, "current_act") || 1,
    last_scene: sceneId,
  };
  if (extraState) {
    for (var key in extraState) {
      if (Object.prototype.hasOwnProperty.call(extraState, key)) {
        statePayload[key] = extraState[key];
      }
    }
  }
  var requestBody = {
    profile_id: quest.state.profile.id,
    pin: activePin,
    state: statePayload,
  };
  if (useSessionAuth && !activePin) {
    requestBody.use_session_auth = true;
  }
  if (eventKey) {
    requestBody.event = { event_type: eventKey };
  }
  quest.set({ busy: true, error: null });
  return updateSessionRequest(requestBody)
    .then(function (response) {
      var updatedProfile = getValue(response, "profile") || quest.state.profile;
      var updatedSession = getValue(response, "session") || quest.state.session;
      storage.save({ profile: updatedProfile, session: updatedSession });
      quest.set({
        busy: false,
        profile: updatedProfile,
        session: updatedSession,
      });
    })
    .catch(function (error) {
      quest.set({ busy: false, error: messageFromError(error) });
    });
}

function enterScene(sceneId, options) {
  if (!sceneId) {
    quest.set({
      view: "error",
      error: "Scene not found. Please try refreshing.",
    });
    return;
  }
  quest.set({
    view: "story",
    sceneId: sceneId,
  });
  var shouldSync = options && options.sync;
  if (shouldSync) {
    syncScene(sceneId, options.event, options.state || null);
  }
}

function resumeQuest() {
  if (!quest.state.profile) {
    quest.set({ view: "signin", error: "Sign in first to continue your quest." });
    return;
  }
  var activePin = quest.state.pin || "";
  var useSessionAuth = !!quest.state.useSessionAuth || (!activePin && !!quest.state.sessionTrainer);
  if (!activePin && !useSessionAuth) {
    quest.set({
      view: "signin",
      error: "Enter your quest PIN to continue.",
    });
    return;
  }
  var requestBody = {
    profile_id: quest.state.profile.id,
    pin: activePin,
  };
  if (useSessionAuth && !activePin) {
    requestBody.use_session_auth = true;
  }
  quest.set({ busy: true, error: null });
  updateSessionRequest(requestBody)
    .then(function (response) {
      var updatedProfile = getValue(response, "profile") || quest.state.profile;
      var updatedSession = getValue(response, "session") || quest.state.session || {};
      storage.save({ profile: updatedProfile, session: updatedSession });
      quest.set({
        busy: false,
        profile: updatedProfile,
        session: updatedSession,
      });
      var startScene = determineStartScene(updatedSession);
      if (!startScene) {
        quest.set({
          view: "story",
          sceneId: null,
          error: "Unable to locate the next scene. Please speak to an event host.",
        });
        return;
      }
      enterScene(startScene, { sync: false });
    })
    .catch(function (error) {
      quest.set({ busy: false, error: messageFromError(error) });
      pinVault.clear();
    });
}

function handleSigninSubmit(event) {
  event.preventDefault();
  var form = event.target;
  var trainerInput = form.querySelector("#quest_trainer");
  var campfireInput = form.querySelector("#quest_campfire");
  var campfireOptOut = form.querySelector("#quest_campfire_opt_out");
  var pinInput = form.querySelector("#quest_pin");

  var trainerName = trainerInput ? trainerInput.value.trim() : "";
  var pinValue = pinInput ? pinInput.value.trim() : "";
  var campfireName = campfireInput ? campfireInput.value.trim() : "";
  var optOut = campfireOptOut ? campfireOptOut.checked : false;

  if (!trainerName || !pinValue) {
    quest.set({ error: "Trainer name and PIN are required." });
    return;
  }
  if (!/^\d{4}$/.test(pinValue)) {
    quest.set({ error: "PIN must be exactly 4 digits." });
    return;
  }

  var body = {
    trainer_name: trainerName,
    pin: pinValue,
    campfire_name: optOut ? null : campfireName || null,
    campfire_opt_out: optOut,
    metadata: {
      auth_mode: "signin",
      source: "geocache",
    },
    create_if_missing: true,
  };

  quest.set({ busy: true, error: null });
  createProfileRequest(body)
    .then(function (response) {
      var profile = getValue(response, "profile") || null;
      var session = getValue(response, "session") || {};
      if (!profile) {
        throw new APIError("Quest profile missing from response.", response, 500);
      }
      pinVault.remember(pinValue);
      storage.save({ profile: profile, session: session });
      quest.set({
        busy: false,
        profile: profile,
        session: session,
        pin: pinValue,
        useSessionAuth: false,
        sessionTrainer: trainerName,
        sessionAuthAttempted: true,
      });
      var startScene = determineStartScene(session) || determineStartScene({});
      if (!startScene) {
        quest.set({
          view: "error",
          error: "Unable to load Act I. Please talk to an event organiser.",
        });
        return;
      }
      enterScene(startScene, { sync: !session.last_scene });
    })
    .catch(function (error) {
      quest.set({
        busy: false,
        error: messageFromError(error),
      });
      pinVault.clear();
    });
}

function autoSignInFromSession(force) {
  var trainer = quest.state.sessionTrainer;
  if (!trainer) {
    return;
  }
  if (!force && quest.state.sessionAuthAttempted) {
    return;
  }
  if (quest.state.busy && !force) {
    return;
  }
  quest.set({
    sessionAuthAttempted: true,
    busy: true,
    error: null,
    pin: "",
  });
  pinVault.clear();
  createProfileRequest({
    trainer_name: trainer,
    pin: "",
    metadata: { auth_mode: "session" },
    create_if_missing: true,
    use_session_auth: true,
  })
    .then(function (response) {
      var profile = getValue(response, "profile") || null;
      var sessionData = getValue(response, "session") || {};
      if (!profile) {
        throw new APIError("Quest profile missing from response.", response, 500);
      }
      storage.save({ profile: profile, session: sessionData });
      quest.set({
        busy: false,
        profile: profile,
        session: sessionData,
        pin: "",
        useSessionAuth: true,
        sessionTrainer: trainer,
      });
      var startScene = determineStartScene(sessionData) || determineStartScene({});
      if (!startScene) {
        quest.set({
          view: "story",
          sceneId: null,
          error: "Unable to load quest progress. Please talk to an event organiser.",
        });
        return;
      }
      enterScene(startScene, { sync: !sessionData.last_scene });
    })
    .catch(function (error) {
      if (error instanceof APIError && error.payload && error.payload.error === "session_not_authorised") {
        quest.set({
          busy: false,
          error: null,
          useSessionAuth: false,
          sessionTrainer: null,
        });
        return;
      }
      if (error instanceof APIError && error.payload && error.payload.error === "trainer_not_found") {
        quest.set({
          busy: false,
          error: null,
          useSessionAuth: false,
        });
        return;
      }
      quest.set({
        busy: false,
        error: messageFromError(error),
        useSessionAuth: false,
      });
    });
}

function handleCtaAction(scene, cta) {
  if (!cta) {
    return;
  }
  if (cta.href) {
    window.location.href = cta.href;
    return;
  }
  var eventKey = cta.event || null;
  var nextSceneId = cta.next || null;
  if (nextSceneId) {
    enterScene(nextSceneId, { sync: true, event: eventKey });
    return;
  }
  var actId = getActIdForScene(scene.id);
  var act = getActById(actId);
  var actScenes = act && Array.isArray(act.scenes) ? act.scenes : [];
  var idx = actScenes.indexOf(scene.id);
  var fallbackNext = idx >= 0 && idx < actScenes.length - 1 ? actScenes[idx + 1] : null;
  if (fallbackNext) {
    enterScene(fallbackNext, { sync: true, event: eventKey });
  } else {
    quest.set({
      error: "No further scenes available. Check with an event host.",
    });
  }
}

function renderLanding() {
  var container = createElement("section", { className: "screen screen--landing" });
  container.appendChild(
    createElement("h1", { className: "screen__title", text: "Whispers of the Wild Court" })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__subtitle",
      text: "A live geocache adventure for GO Wild Doncaster. Sign in and begin Act I instantly.",
    })
  );
  if (quest.state.sessionTrainer) {
    container.appendChild(
      createElement("p", {
        className: "screen__meta",
        text: "Detected RDAB session for trainer \"" + quest.state.sessionTrainer + "\".",
      })
    );
  }
  var actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Sign in with RDAB app", "button button--primary", function () {
      quest.set({ view: "signin", error: null });
    })
  );
  if (quest.state.sessionTrainer && !quest.state.profile) {
    actions.appendChild(
      createButton(
        "Continue as " + quest.state.sessionTrainer,
        "button button--secondary",
        function () {
          autoSignInFromSession(true);
        }
      )
    );
  }
  actions.appendChild(
    createButton("What is this?", "button button--secondary", function () {
      quest.set({ view: "about", error: null });
    })
  );
  if (quest.state.profile) {
    actions.appendChild(
      createButton("Reload save", "button button--ghost", function () {
        quest.set({ view: "resume", error: null });
      })
    );
  }
  container.appendChild(actions);
  return container;
}

function renderAbout() {
  var container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", {
      className: "screen__title",
      text: "What is Whispers of the Wild Court?",
    })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__message",
      text:
        "A story-led geocache across Doncaster. Explore landmarks, scan hidden Sigils, and decide the fate of the Wild Court before Team NO Wild claims the city.",
    })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__meta",
      text: "You’ll need your Pokémon GO trainer name, an optional Campfire username, and a 4-digit PIN.",
    })
  );
  var actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Back", "button button--primary", function () {
      quest.set({ view: "landing", error: null });
    })
  );
  container.appendChild(actions);
  return container;
}

function renderResume() {
  var hasProfile = !!quest.state.profile;
  var container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", {
      className: "screen__title",
      text: hasProfile
        ? "Resume your quest"
        : "No save detected",
    })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__message",
      text: hasProfile
        ? quest.state.useSessionAuth || (!quest.state.pin && quest.state.sessionTrainer)
          ? "Press continue to reload your quest progress."
          : "Press continue to fetch your progress. We’ll need your quest PIN."
        : "Sign in first to create a quest profile.",
    })
  );
  var actions = createElement("div", { className: "screen__actions" });
  if (hasProfile) {
    actions.appendChild(
      createButton("Continue", "button button--primary", function () {
        resumeQuest();
      })
    );
  }
  actions.appendChild(
    createButton("Back", "button button--ghost", function () {
      quest.set({ view: "landing", error: null });
    })
  );
  container.appendChild(actions);
  return container;
}

function renderSignin() {
  var container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", {
      className: "screen__title",
      text: "Sign in / Create quest pass",
    })
  );
  container.appendChild(
    createElement("p", {
      className: "screen__subtitle",
      text: "Enter your trainer name, Campfire handle (optional), and 4-digit PIN to begin.",
    })
  );
  var form = createElement("form", { className: "form" });

  var trainerField = createElement("div", { className: "field" });
  var trainerLabel = createElement("label", { text: "Trainer name" });
  trainerLabel.setAttribute("for", "quest_trainer");
  var trainerInput = createElement("input", {
    className: "input",
    attrs: {
      id: "quest_trainer",
      type: "text",
      maxlength: "32",
      autocomplete: "username",
      required: "required",
    },
  });
  if (quest.state.profile && quest.state.profile.trainer_name) {
    trainerInput.value = quest.state.profile.trainer_name;
  } else if (quest.state.sessionTrainer) {
    trainerInput.value = quest.state.sessionTrainer;
  }
  trainerField.appendChild(trainerLabel);
  trainerField.appendChild(trainerInput);

  var campfireField = createElement("div", { className: "field" });
  var campfireLabel = createElement("label", { text: "Campfire username (optional)" });
  campfireLabel.setAttribute("for", "quest_campfire");
  var campfireInput = createElement("input", {
    className: "input",
    attrs: {
      id: "quest_campfire",
      type: "text",
      maxlength: "32",
    },
  });
  if (quest.state.profile && quest.state.profile.campfire_name) {
    var campfireValue = quest.state.profile.campfire_name;
    if (campfireValue !== "Not on Campfire") {
      campfireInput.value = campfireValue;
    }
  }
  var campfireHint = createElement("p", {
    className: "form__hint",
    text: "Tick below if you’re not on Campfire.",
  });
  var campfireOptLabel = createElement("label", { className: "checkbox" });
  var campfireOptCheck = createElement("input", {
    attrs: {
      type: "checkbox",
      id: "quest_campfire_opt_out",
    },
  });
  if (
    quest.state.profile &&
    quest.state.profile.campfire_name === "Not on Campfire"
  ) {
    campfireOptCheck.checked = true;
  }
  var campfireOptText = createElement("span", { text: "Not on Campfire" });
  campfireOptLabel.appendChild(campfireOptCheck);
  campfireOptLabel.appendChild(campfireOptText);
  campfireField.appendChild(campfireLabel);
  campfireField.appendChild(campfireInput);
  campfireField.appendChild(campfireHint);
  campfireField.appendChild(campfireOptLabel);

  var pinField = createElement("div", { className: "field" });
  var pinLabel = createElement("label", { text: "4-digit quest PIN" });
  pinLabel.setAttribute("for", "quest_pin");
  var pinInput = createElement("input", {
    className: "input",
    attrs: {
      id: "quest_pin",
      type: "password",
      inputmode: "numeric",
      pattern: "\\d{4}",
      maxlength: "4",
      required: "required",
      autocomplete: "current-password",
    },
  });
  pinField.appendChild(pinLabel);
  pinField.appendChild(pinInput);

  form.appendChild(trainerField);
  form.appendChild(campfireField);
  form.appendChild(pinField);

  var actions = createElement("div", { className: "screen__actions" });
  var submitButton = createButton("Continue", "button button--primary");
  submitButton.setAttribute("type", "submit");
  actions.appendChild(submitButton);
  actions.appendChild(
    createButton("Back", "button button--ghost", function () {
      quest.set({ view: "landing", error: null });
    })
  );
  form.appendChild(actions);

  form.addEventListener("submit", handleSigninSubmit);

  container.appendChild(form);
  return container;
}

function renderStory() {
  var sceneId = quest.state.sceneId;
  if (!sceneId) {
    return renderStoryPlaceholder("No quest scene selected yet. Use the controls to continue.");
  }
  var scene = getSceneById(sceneId);
  if (!scene) {
    return renderStoryPlaceholder("Scene data missing. Please speak to an event organiser.");
  }
  var actId = getActIdForScene(sceneId);
  var act = getActById(actId);

  var container = createElement("section", { className: "story" });

  var header = createElement("header", { className: "story__header" });
  header.appendChild(
    createElement("p", {
      className: "story__act",
      text: act ? act.title : "Quest",
    })
  );
  container.appendChild(header);

  var content = createElement("div", { className: "story__content" });

  if (scene.art) {
    var artWrapper = createElement("div", { className: "story__art" });
    var artImg = createElement("img", {
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

  var textBlocks = Array.isArray(scene.text) ? scene.text : [];
  for (var i = 0; i < textBlocks.length; i += 1) {
    content.appendChild(
      createElement("p", { className: "story__line", text: textBlocks[i] })
    );
  }

  container.appendChild(content);
  if (scene.type === "minigame") {
    container.appendChild(buildMinigameView(scene));
  } else {
    var actions = createElement("div", { className: "story__actions" });
    var ctaList = [];
    if (scene.cta) {
      ctaList = Array.isArray(scene.cta) ? scene.cta : [scene.cta];
    }
    if (!ctaList.length) {
      ctaList.push({
        label: "Continue",
        next: determineDefaultNext(sceneId),
      });
    }
    for (var j = 0; j < ctaList.length; j += 1) {
      (function (cta) {
        if (!cta || !cta.label) {
          return;
        }
        actions.appendChild(
          createButton(cta.label, "button button--primary", function () {
            handleCtaAction(scene, cta);
          })
        );
      })(ctaList[j]);
    }
    container.appendChild(actions);
  }

  return container;
}

function determineDefaultNext(sceneId) {
  var actId = getActIdForScene(sceneId);
  var act = getActById(actId);
  if (!act) {
    return null;
  }
  var actScenes = Array.isArray(act.scenes) ? act.scenes : [];
  var index = actScenes.indexOf(sceneId);
  if (index >= 0 && index < actScenes.length - 1) {
    return actScenes[index + 1];
  }
  if (act.next_act) {
    var nextAct = getActById(act.next_act);
    var nextScenes = nextAct && Array.isArray(nextAct.scenes) ? nextAct.scenes : [];
    return nextScenes.length ? nextScenes[0] : null;
  }
  return null;
}

function getProgressFlags() {
  var session = quest.state.session || {};
  return session.progress_flags || {};
}

function getProgressEntry(flagKey) {
  if (!flagKey) {
    return null;
  }
  var flags = getProgressFlags();
  return flags[flagKey] || null;
}

function buildProgressUpdate(flagKey, entry) {
  var update = {};
  update[flagKey] = entry;
  return update;
}

function resolveSceneNext(scene) {
  if (!scene) {
    return null;
  }
  var ctas = scene.cta ? (Array.isArray(scene.cta) ? scene.cta : [scene.cta]) : [];
  for (var i = 0; i < ctas.length; i += 1) {
    var cta = ctas[i];
    if (!cta) {
      continue;
    }
    if (cta.next || cta.href) {
      return cta;
    }
  }
  var fallbackNext = determineDefaultNext(scene.id);
  if (fallbackNext) {
    return { next: fallbackNext, label: "Continue" };
  }
  return null;
}

function completeScene(scene, options) {
  options = options || {};
  var resolvedNext = options.next || resolveSceneNext(scene);
  var nextSceneId =
    options.nextSceneId !== undefined ? options.nextSceneId : resolvedNext && resolvedNext.next;
  var href = options.href !== undefined ? options.href : resolvedNext && resolvedNext.href;
  var eventKey = options.event !== undefined ? options.event : resolvedNext && resolvedNext.event;
  var extraState = options.state ? Object.assign({}, options.state) : {};

  if (nextSceneId) {
    extraState.last_scene = nextSceneId;
    var targetActId = getActIdForScene(nextSceneId) || getActIdForScene(scene.id);
    var nextActNumber = parseActNumber(targetActId);
    if (nextActNumber) {
      extraState.current_act = nextActNumber;
    }
  }

  return syncScene(scene.id, eventKey || null, extraState).then(function () {
    if (href) {
      window.location.href = href;
      return;
    }
    if (nextSceneId) {
      enterScene(nextSceneId, { sync: false });
    } else {
      quest.set({ view: "story", sceneId: scene.id });
    }
  });
}

function buildMinigameView(scene) {
  var minigame = scene.minigame || {};
  var kind = (minigame.kind || "").toLowerCase();
  var container = createElement("div", { className: "minigame minigame--" + kind });
  var statusBanner = createElement("div", { className: "minigame__status" });
  container.appendChild(statusBanner);

  if (kind === "artifact_scan") {
    renderArtifactMinigame(scene, minigame, container, statusBanner);
  } else if (kind === "location") {
    renderLocationMinigame(scene, minigame, container, statusBanner);
  } else if (kind === "mosaic") {
    renderMosaicMinigame(scene, minigame, container, statusBanner);
  } else if (kind === "quiz") {
    renderQuizMinigame(scene, minigame, container, statusBanner);
  } else if (kind === "reflex") {
    renderReflexMinigame(scene, minigame, container, statusBanner);
  } else if (kind === "pattern") {
    renderPatternMinigame(scene, minigame, container, statusBanner);
  } else if (kind === "ending_choice") {
    renderEndingChoiceMinigame(scene, minigame, container, statusBanner);
  } else {
    statusBanner.textContent =
      "Interactive challenge coming soon. Speak with an event host for instructions.";
    var fallbackActions = createElement("div", { className: "story__actions story__actions--minigame" });
    fallbackActions.appendChild(
      createButton("Continue", "button button--primary", function () {
        completeScene(scene, {});
      })
    );
    container.appendChild(fallbackActions);
  }

  return container;
}

function renderArtifactMinigame(scene, minigame, container, statusBanner) {
  var flagKey = minigame.success_flag || minigame.artifact_slug || scene.id;
  var progress = getProgressEntry(flagKey);
  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text:
        minigame.code_hint ||
        "Enter the artifact code or tap the NFC tag attached to the item.",
    })
  );

  var form = createElement("form", { className: "minigame__form" });
  var input = createElement("input", {
    className: "input",
    attrs: { type: "text", maxlength: "12", placeholder: "Enter artifact code" },
  });
  var submitButton = createButton("Validate code", "button button--primary");
  submitButton.setAttribute("type", "submit");
  form.appendChild(input);
  form.appendChild(submitButton);
  container.appendChild(form);

  var manualSection = createElement("div", { className: "minigame__actions" });
  var manualButton = createButton("Mark as scanned (event assist)", "button button--secondary");
  manualSection.appendChild(manualButton);
  container.appendChild(manualSection);

  function finalize(method, codeValue) {
    if (progress) {
      return;
    }
    var nextDetails = resolveSceneNext(scene) || {};
    var entry = {
      status: "artifact_scanned",
      artifact_slug: minigame.artifact_slug || scene.id,
      method: method,
      code: codeValue || null,
      validated_at: new Date().toISOString(),
    };
    statusBanner.textContent = "Artifact logged! Syncing with HQ…";
    submitButton.disabled = true;
    input.disabled = true;
    manualButton.disabled = true;
    completeScene(scene, {
      state: {
        progress_flags: buildProgressUpdate(flagKey, entry),
      },
      nextSceneId: nextDetails.next,
      event: "artifact_scan",
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (progress) {
      return;
    }
    var codeValue = (input.value || "").trim();
    if (!codeValue) {
      statusBanner.textContent = "Enter the artifact code to continue.";
      return;
    }
    var expected = minigame.code ? String(minigame.code).trim() : "";
    if (expected && expected.length && expected !== codeValue) {
      statusBanner.textContent = "That code doesn’t match. Check the artifact again.";
      return;
    }
    finalize("code", codeValue);
  });

  manualButton.addEventListener("click", function () {
    if (progress) {
      return;
    }
    if (!window.confirm("Have you confirmed the scan with an event host?")) {
      return;
    }
    finalize("manual", null);
  });

  if (progress) {
    statusBanner.textContent = "Artifact already scanned. Great work!";
    input.value = progress.code || "";
    input.disabled = true;
    submitButton.disabled = true;
    manualButton.disabled = true;
    var actions = createElement("div", { className: "story__actions story__actions--minigame" });
    actions.appendChild(
      createButton("Continue", "button button--primary", function () {
        completeScene(scene, {});
      })
    );
    container.appendChild(actions);
  }
}

function renderLocationMinigame(scene, minigame, container, statusBanner) {
  var flagKey = minigame.success_flag || minigame.location_id || scene.id;
  var progress = getProgressEntry(flagKey);
  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text:
        minigame.prompt ||
        "Travel to the highlighted spot. Use GPS check-in when you’re within radius.",
    })
  );

  var resultText = createElement("p", { className: "minigame__status-text" });
  container.appendChild(resultText);

  function complete(method, coords) {
    if (progress) {
      return;
    }
    var entry = {
      status: "location_check_in",
      location_id: minigame.location_id || scene.id,
      method: method,
      latitude: coords && coords.latitude !== undefined ? coords.latitude : undefined,
      longitude: coords && coords.longitude !== undefined ? coords.longitude : undefined,
      accuracy_m: coords && coords.accuracy_m !== undefined ? coords.accuracy_m : undefined,
      validated_at: new Date().toISOString(),
    };
    statusBanner.textContent = "Check-in recorded!";
    completeScene(scene, {
      event: "location_check",
      state: {
        progress_flags: buildProgressUpdate(flagKey, entry),
      },
    });
  }

  function useGPS() {
    if (progress) {
      return;
    }
    if (!navigator.geolocation) {
      resultText.textContent = "GPS unavailable on this device.";
      return;
    }
    resultText.textContent = "Checking your position…";
    navigator.geolocation.getCurrentPosition(
      function (position) {
        var lat = position.coords.latitude;
        var lng = position.coords.longitude;
        var accuracy = position.coords.accuracy || null;
        var targetLat = minigame.latitude;
        var targetLng = minigame.longitude;
        var radius = minigame.radius_m || 75;
        var distance = calculateDistanceMeters(lat, lng, targetLat, targetLng);
        if (distance <= radius + 10) {
          resultText.textContent = "Within range! (" + formatDistance(distance) + ")";
          complete("gps", {
            latitude: lat,
            longitude: lng,
            accuracy_m: accuracy,
          });
        } else {
          resultText.textContent =
            "You’re " + formatDistance(distance) + " away. Move closer to the marker.";
        }
      },
      function () {
        resultText.textContent = "We couldn’t read your location. Try again or ask an event host.";
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
    );
  }

  function manualOverride() {
    if (progress) {
      return;
    }
    if (!window.confirm("Confirm with an event host before manually completing this check-in.")) {
      return;
    }
    complete("manual", {});
  }

  var actions = createElement("div", { className: "story__actions story__actions--minigame" });
  var gpsButton = createButton("Check in with GPS", "button button--primary", useGPS);
  var manualButton = createButton("I’m on-site – mark complete", "button button--secondary", manualOverride);
  actions.appendChild(gpsButton);
  actions.appendChild(manualButton);

  if (progress) {
    statusBanner.textContent = "Location already checked in.";
    actions.innerHTML = "";
    actions.appendChild(
      createButton("Continue", "button button--primary", function () {
        completeScene(scene, {});
      })
    );
  }

  container.appendChild(actions);
}

function renderMosaicMinigame(scene, minigame, container, statusBanner) {
  var flagKey = minigame.success_flag || scene.id;
  var progress = getProgressEntry(flagKey);
  var totalPieces = parseInt(minigame.pieces, 10) || 6;

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Tap each shard to set it inside the compass frame.",
    })
  );

  var board = createElement("div", { className: "mosaic" });
  var pool = createElement("div", { className: "mosaic__pool" });
  var frame = createElement("div", { className: "mosaic__frame" });
  board.appendChild(pool);
  board.appendChild(frame);
  container.appendChild(board);

  function finishPuzzle() {
    if (progress) {
      return;
    }
    statusBanner.textContent = "Compass restored!";
    completeScene(scene, {
      state: {
        progress_flags: buildProgressUpdate(flagKey, {
          status: "puzzle_complete",
          puzzle_id: minigame.puzzle_id || scene.id,
          pieces: totalPieces,
          validated_at: new Date().toISOString(),
        }),
      },
    });
  }

  if (progress) {
    statusBanner.textContent = "Puzzle already solved. Nicely done.";
    var doneActions = createElement("div", { className: "story__actions story__actions--minigame" });
    doneActions.appendChild(
      createButton("Continue", "button button--primary", function () {
        completeScene(scene, {});
      })
    );
    container.appendChild(doneActions);
    return;
  }

  var pieceIds = [];
  for (var i = 1; i <= totalPieces; i += 1) {
    pieceIds.push(i);
  }
  pieceIds.sort(function () {
    return Math.random() - 0.5;
  });

  var placed = 0;
  function placePiece(el) {
    if (el.dataset.placed === "true") {
      return;
    }
    el.dataset.placed = "true";
    el.classList.add("mosaic__piece--placed");
    frame.appendChild(el);
    placed += 1;
    if (placed >= totalPieces) {
      finishPuzzle();
    }
  }

  for (var j = 0; j < pieceIds.length; j += 1) {
    (function (id) {
      var piece = createElement("button", {
        className: "mosaic__piece",
        text: String(id),
      });
      piece.addEventListener("click", function () {
        placePiece(piece);
      });
      pool.appendChild(piece);
    })(pieceIds[j]);
  }
}

function renderQuizMinigame(scene, minigame, container, statusBanner) {
  var flagKey = minigame.success_flag || scene.id;
  var progress = getProgressEntry(flagKey);
  if (progress) {
    statusBanner.textContent = "Quiz already recorded.";
    var doneActions = createElement("div", { className: "story__actions story__actions--minigame" });
    doneActions.appendChild(
      createButton("Continue", "button button--primary", function () {
        completeScene(scene, {});
      })
    );
    container.appendChild(doneActions);
    return;
  }

  if (minigame.questions && minigame.questions.length) {
    renderMultiQuestionQuiz(scene, minigame, container, statusBanner);
    return;
  }

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Choose the correct answer to continue.",
    })
  );

  var form = createElement("form", { className: "minigame__form" });
  var choices = Array.isArray(minigame.choices) ? minigame.choices : [];
  for (var i = 0; i < choices.length; i += 1) {
    (function (choice, index) {
      var label = createElement("label", { className: "choice" });
      var input = createElement("input", {
        attrs: {
          type: "radio",
          name: "quiz-choice",
          value: choice.id || String(index),
          required: "required",
        },
      });
      label.appendChild(input);
      label.appendChild(createElement("span", { text: choice.label || choice.id || String(index) }));
      form.appendChild(label);
    })(choices[i], i);
  }

  var submitButton = createButton("Submit answer", "button button--primary");
  submitButton.setAttribute("type", "submit");
  form.appendChild(submitButton);
  container.appendChild(form);

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var selected = form.querySelector("input[name='quiz-choice']:checked");
    if (!selected) {
      statusBanner.textContent = "Select an answer first.";
      return;
    }
    var selectedId = selected.value;
    var selectedChoice = null;
    for (var i = 0; i < choices.length; i += 1) {
      var candidateId = choices[i].id || String(i);
      if (candidateId === selectedId) {
        selectedChoice = choices[i];
        break;
      }
    }
    if (!selectedChoice) {
      statusBanner.textContent = "Select an answer first.";
      return;
    }
    if (selectedChoice.correct) {
      statusBanner.textContent = "Correct! Updating your quest…";
      completeScene(scene, {
        state: {
          progress_flags: buildProgressUpdate(flagKey, {
            status: "quiz_correct",
            choice_id: selectedChoice.id || selectedId,
            validated_at: new Date().toISOString(),
          }),
        },
      });
    } else {
      statusBanner.textContent = "That isn't right. Try again after another look around.";
    }
  });
}

function renderMultiQuestionQuiz(scene, minigame, container, statusBanner) {
  var questions = minigame.questions || [];
  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Answer each prompt. The Oracles will interpret the result.",
    })
  );
  var form = createElement("form", { className: "minigame__form minigame__form--quiz" });
  for (var i = 0; i < questions.length; i += 1) {
    var question = questions[i];
    var fieldset = createElement("fieldset", { className: "quiz__question" });
    var legend = createElement("legend", { text: question.prompt || "Question" });
    fieldset.appendChild(legend);
    var choices = Array.isArray(question.choices) ? question.choices : [];
    for (var j = 0; j < choices.length; j += 1) {
      var choice = choices[j];
      var label = createElement("label", { className: "choice" });
      var radio = createElement("input", {
        attrs: {
          type: "radio",
          name: "quiz_" + (question.id || String(i)),
          value: choice.id || String(j),
          required: "required",
        },
      });
      label.appendChild(radio);
      label.appendChild(createElement("span", { text: choice.label || choice.id || String(j) }));
      fieldset.appendChild(label);
    }
    form.appendChild(fieldset);
  }
  var submitButton = createButton("Submit responses", "button button--primary");
  submitButton.setAttribute("type", "submit");
  form.appendChild(submitButton);
  container.appendChild(form);

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var tally = {};
    var answers = [];
    for (var i = 0; i < questions.length; i += 1) {
      var question = questions[i];
      var fieldName = "quiz_" + (question.id || String(i));
      var selected = form.querySelector("input[name='" + fieldName + "']:checked");
      if (!selected) {
        statusBanner.textContent = "Answer every question.";
        return;
      }
      var choiceId = selected.value;
      tally[choiceId] = (tally[choiceId] || 0) + 1;
      answers.push({ question_id: question.id || String(i), choice_id: choiceId });
    }
    var dominantChoice = null;
    var dominantScore = -1;
    for (var key in tally) {
      if (Object.prototype.hasOwnProperty.call(tally, key)) {
        if (tally[key] > dominantScore) {
          dominantScore = tally[key];
          dominantChoice = key;
        }
      }
    }
    var results = minigame.results || {};
    var interpretation = results[dominantChoice] || "The Oracles are still deciding…";
    statusBanner.textContent = interpretation;
    completeScene(scene, {
      state: {
        progress_flags: buildProgressUpdate(minigame.success_flag || scene.id, {
          status: "quiz_completed",
          dominant_choice: dominantChoice,
          answers: answers,
          interpretation: interpretation,
          validated_at: new Date().toISOString(),
        }),
      },
    });
  });
}

function renderReflexMinigame(scene, minigame, container, statusBanner) {
  var flagKey = minigame.success_flag || scene.id;
  var progress = getProgressEntry(flagKey);
  var rounds = parseInt(minigame.rounds, 10) || 5;

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Tap the glowing orb each time it appears.",
    })
  );

  var arena = createElement("div", { className: "reflex" });
  container.appendChild(arena);
  var startButton = createButton(progress ? "Focus test complete" : "Start focus test", "button button--primary");
  arena.appendChild(startButton);
  var counter = createElement("p", { className: "minigame__status-text" });
  container.appendChild(counter);

  if (progress) {
    statusBanner.textContent = "Focus test already completed.";
    startButton.disabled = true;
    var continueActions = createElement("div", { className: "story__actions story__actions--minigame" });
    continueActions.appendChild(
      createButton("Continue", "button button--primary", function () {
        completeScene(scene, {});
      })
    );
    container.appendChild(continueActions);
    return;
  }

  startButton.addEventListener("click", function () {
    startButton.disabled = true;
    var orb = createElement("div", { className: "reflex__orb" });
    arena.appendChild(orb);
    var currentRound = 0;

    function nextRound() {
      currentRound += 1;
      if (currentRound > rounds) {
        statusBanner.textContent = "You passed the focus test!";
        completeScene(scene, {
          state: {
            progress_flags: buildProgressUpdate(flagKey, {
              status: "reflex_complete",
              rounds: rounds,
              validated_at: new Date().toISOString(),
            }),
          },
        });
        return;
      }
      counter.textContent = "Round " + currentRound + " of " + rounds;
      var x = Math.random() * 80 + 10;
      var y = Math.random() * 60 + 20;
      orb.style.left = x + "%";
      orb.style.top = y + "%";
      orb.classList.add("is-visible");
    }

    orb.addEventListener("click", function () {
      if (!orb.classList.contains("is-visible")) {
        return;
      }
      orb.classList.remove("is-visible");
      setTimeout(nextRound, 400);
    });

    nextRound();
  });
}

function renderPatternMinigame(scene, minigame, container, statusBanner) {
  var flagKey = minigame.success_flag || scene.id;
  var progress = getProgressEntry(flagKey);
  var rounds = parseInt(minigame.rounds, 10) || 5;
  var symbols = ["⚡️", "🌿", "🔥"];

  container.appendChild(
    createElement("p", {
      className: "minigame__hint",
      text: "Tap the matching sigil to disrupt Dr Nat L Order’s illusion.",
    })
  );

  var arena = createElement("div", { className: "pattern" });
  var prompt = createElement("div", { className: "pattern__symbol", text: "?" });
  arena.appendChild(prompt);
  var controls = createElement("div", { className: "pattern__choices" });
  for (var i = 0; i < symbols.length; i += 1) {
    (function (symbol) {
      controls.appendChild(
        createButton(symbol, "button button--secondary", function () {
          handleChoice(symbol);
        })
      );
    })(symbols[i]);
  }
  arena.appendChild(controls);
  container.appendChild(arena);
  var statusLine = createElement("p", { className: "minigame__status-text" });
  container.appendChild(statusLine);

  if (progress) {
    statusBanner.textContent = "Illusion already broken.";
    var continueActions = createElement("div", { className: "story__actions story__actions--minigame" });
    continueActions.appendChild(
      createButton("Continue", "button button--primary", function () {
        completeScene(scene, {});
      })
    );
    container.appendChild(continueActions);
    return;
  }

  var currentRound = 0;
  var currentSymbol = null;

  function nextRound() {
    currentRound += 1;
    if (currentRound > rounds) {
      statusBanner.textContent = "Illusion circuit shattered!";
      completeScene(scene, {
        state: {
          progress_flags: buildProgressUpdate(flagKey, {
            status: "pattern_complete",
            rounds: rounds,
            validated_at: new Date().toISOString(),
          }),
        },
      });
      return;
    }
    statusLine.textContent = "Round " + currentRound + " of " + rounds;
    currentSymbol = symbols[Math.floor(Math.random() * symbols.length)];
    prompt.textContent = currentSymbol;
  }

  function handleChoice(choice) {
    if (!currentSymbol) {
      nextRound();
      return;
    }
    if (choice === currentSymbol) {
      statusBanner.textContent = "Direct hit!";
      nextRound();
    } else {
      statusBanner.textContent = "Missed. Resetting the current round.";
      currentRound -= 1;
      nextRound();
    }
  }

  nextRound();
}

function renderEndingChoiceMinigame(scene, minigame, container, statusBanner) {
  statusBanner.textContent = "Choose the fate of the Sigils.";
  var choices = Array.isArray(minigame.choices) ? minigame.choices : [];
  var actions = createElement("div", { className: "story__actions story__actions--minigame" });
  for (var i = 0; i < choices.length; i += 1) {
    (function (option) {
      if (!option) {
        return;
      }
      actions.appendChild(
        createButton(option.label || option.id, "button button--primary", function () {
          completeScene(scene, {
            nextSceneId: option.next_scene || option.next,
            event: "ending_choice",
            state: {
              progress_flags: buildProgressUpdate(option.id || scene.id, {
                status: "ending_selected",
                choice_id: option.id || option.label || "unknown",
                validated_at: new Date().toISOString(),
              }),
            },
          });
        })
      );
    })(choices[i]);
  }
  container.appendChild(actions);
}

function calculateDistanceMeters(latA, lngA, latB, lngB) {
  function radians(deg) {
    return (deg * Math.PI) / 180;
  }
  var earthRadius = 6371000;
  var dLat = radians(latB - latA);
  var dLng = radians(lngB - lngA);
  var a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(radians(latA)) * Math.cos(radians(latB)) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
  var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return earthRadius * c;
}

function formatDistance(distance) {
  if (distance >= 1000) {
    return (distance / 1000).toFixed(2) + " km";
  }
  return Math.round(distance) + " m";
}

function renderStoryPlaceholder(message) {
  var container = createElement("section", { className: "screen" });
  container.appendChild(
    createElement("h1", { className: "screen__title", text: "Quest ready" })
  );
  container.appendChild(
    createElement("p", { className: "screen__message", text: message })
  );
  var actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Back to start", "button button--ghost", function () {
      quest.set({ view: "landing" });
    })
  );
  container.appendChild(actions);
  return container;
}

function renderErrorScreen() {
  var container = createElement("section", { className: "screen" });
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
  var actions = createElement("div", { className: "screen__actions" });
  actions.appendChild(
    createButton("Retry", "button button--primary", function () {
      quest.set({ view: "landing", error: null });
    })
  );
  container.appendChild(actions);
  return container;
}

function render() {
  var view = quest.state.view;
  var busy = quest.state.busy;
  var error = quest.state.error;
  var screen;

  if (view === "landing") {
    screen = renderLanding();
  } else if (view === "about") {
    screen = renderAbout();
  } else if (view === "resume") {
    screen = renderResume();
  } else if (view === "signin") {
    screen = renderSignin();
  } else if (view === "story") {
    screen = renderStory();
  } else {
    screen = renderErrorScreen();
  }

  if (error && screen && !screen.querySelector(".screen__error")) {
    var errorBanner = createElement("div", { className: "screen__error", text: error });
    screen.insertBefore(errorBanner, screen.firstChild || null);
  }

  if (busy) {
    screen.classList.add("screen--busy");
  }

  root.innerHTML = "";
  root.appendChild(screen);
}

function resolveAssetUrl(path) {
  if (!path || typeof path !== "string") {
    return null;
  }
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  var normalized = path.replace(/^\/+/, "");
  return "/static/" + normalized;
}

function ensureStoryLoaded() {
  if (storyData.acts && storyData.acts.length) {
    return Promise.resolve();
  }
  quest.set({ busy: true, error: null });
  return fetchManifest()
    .catch(function (error) {
      quest.set({
        busy: false,
        error: messageFromError(error),
      });
    })
    .then(function () {
      quest.set({ busy: false });
    });
}

ensureStoryLoaded().then(function () {
  render();
  if (
    quest.state.sessionTrainer &&
    !quest.state.sessionAuthAttempted &&
    (!quest.state.profile || !quest.state.pin)
  ) {
    autoSignInFromSession(false);
  }
  if (
    quest.state.view === "story" &&
    !quest.state.sceneId &&
    quest.state.session
  ) {
    var startScene = determineStartScene(quest.state.session);
    if (startScene) {
      enterScene(startScene, { sync: false });
    }
  }
});
