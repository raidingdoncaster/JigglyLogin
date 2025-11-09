const root = document.getElementById("scene-root");

if (!root) {
  console.error("Geocache quest root element missing.");
} else {
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
      story: null,
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

  const render = () => {
    const { view, busy, error } = quest.state;
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
        case "act":
          return renderAct();
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

  const renderAct = () => {
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
        story,
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
}
