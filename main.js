import { OverlayHUD } from './components/OverlayHUD.js';
import { DialogueMode } from './components/DialogueMode.js';
import { ActivityMode } from './components/ActivityMode.js';
import { LocationMode } from './components/LocationMode.js';
import { SigilHunt } from './components/SigilHunt.js';
import { SigilDiscovery } from './components/SigilDiscovery.js';
import { CheckInSuccess } from './components/CheckInSuccess.js';
import { initDebugOverlay } from './debugOverlay.js';
import {
  initializeState,
  setCodes,
  setLocations,
  getCurrentScene,
  getLineIndex,
  advanceDialogue,
  retreatDialogue,
  changeScene,
  resetLine,
  getProgress,
  getActLabel,
  getSceneName,
  getCodeById,
  getLocationById,
  getAllScenes,
  getGameState,
  setMode
} from './stateManager.js';

const sceneContainer = document.getElementById('scene-container');
const interactionZone = document.getElementById('interaction-zone');

let hud;
let nextButton;
let backButton;
let sceneReadyToAdvance = false;
let autoAdvanceEnabled = true;

export function setAutoAdvanceEnabled(value) {
  autoAdvanceEnabled = Boolean(value);
}

export function isAutoAdvanceEnabled() {
  return autoAdvanceEnabled;
}

async function fetchJSON(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Failed to load ${path}`);
  }
  return response.json();
}

export function loadScene() {
  const scene = getCurrentScene();
  if (!scene) {
    console.warn('No scene available at current index.');
    return;
  }

  setMode(scene.type);

  const template = renderSceneTemplate(scene);
  sceneContainer.classList.remove('fade-in');
  sceneContainer.innerHTML = template;
  void sceneContainer.offsetWidth;
  sceneContainer.classList.add('fade-in');

  sceneReadyToAdvance = !['quiz', 'riddle', 'location', 'sigilHunt'].includes(scene.type);

  renderNavigation(scene);
  setupSceneHandlers(scene);
  updateHud();
  logSceneChange(scene);
  document.dispatchEvent(new CustomEvent('debug:scene-updated'));
}

function renderSceneTemplate(scene) {
  const lineIndex = getLineIndex();
  switch (scene.type) {
    case 'dialogue':
      return DialogueMode(scene, lineIndex);
    case 'quiz':
    case 'riddle':
      return ActivityMode(scene);
    case 'location':
      return LocationMode(scene);
    case 'sigilHunt':
      return SigilHunt(scene);
    case 'sigilDiscovery':
      return SigilDiscovery(scene);
    case 'checkInSuccess':
      return CheckInSuccess(scene);
    default:
      return `
        <div class="scene-placeholder">
          <h2>Scene Coming Soon</h2>
          <p>Scene type <strong>${scene.type}</strong> is not implemented yet.</p>
        </div>
      `;
  }
}

function renderNavigation(scene) {
  interactionZone.innerHTML = '';
  backButton = createNavButton('Back');
  nextButton = createNavButton('Next');

  const state = getGameState();
  const canGoBack =
    (scene.type === 'dialogue' && getLineIndex() > 0) ||
    state.scene > 0 ||
    state.act > 0;

  backButton.disabled = !canGoBack;
  nextButton.disabled = !sceneReadyToAdvance;

  backButton.addEventListener('click', handleBack);
  nextButton.addEventListener('click', handleNext);

  if (scene.type === 'sigilDiscovery' || scene.type === 'checkInSuccess') {
    nextButton.classList.add('hidden');
  } else {
    nextButton.classList.remove('hidden');
  }

  interactionZone.appendChild(backButton);
  interactionZone.appendChild(nextButton);
}

function setupSceneHandlers(scene) {
  switch (scene.type) {
    case 'dialogue':
      // Dialogue controls handled through Next/Back logic.
      break;
    case 'quiz':
    case 'riddle':
      setupActivityHandlers(scene);
      break;
    case 'location':
      setupLocationHandler(scene);
      break;
    case 'sigilHunt':
      setupSigilHandler(scene);
      break;
    case 'sigilDiscovery':
      setupSigilDiscoveryHandler();
      break;
    case 'checkInSuccess':
      setupCheckInHandler();
      break;
    default:
      break;
  }
}

function setupActivityHandlers(scene) {
  const options = Array.from(sceneContainer.querySelectorAll('.activity-option'));
  const feedbackNode = sceneContainer.querySelector('.activity-feedback');
  options.forEach((button) => {
    button.addEventListener('click', () => {
      const selected = Number(button.dataset.index);
      const correctIndex = Number(scene.correct);
      options.forEach((opt) => opt.setAttribute('disabled', 'disabled'));
      if (selected === correctIndex) {
        options.forEach((opt) => opt.classList.remove('incorrect', 'correct'));
        button.classList.add('correct');
        feedbackNode.textContent = scene.successText || 'Correct! The path clears.';
        markSceneResolved(true, 700);
      } else {
        button.classList.add('incorrect');
        feedbackNode.textContent = scene.failureText || 'Not quite. Try again.';
        sceneReadyToAdvance = false;
        nextButton.disabled = true;
        options.forEach((opt) => {
          opt.removeAttribute('disabled');
          opt.classList.remove('incorrect', 'correct');
        });
      }
    });
  });
}

function setupLocationHandler(scene) {
  const confirmButton = sceneContainer.querySelector('.location-confirm');
  const feedbackNode = sceneContainer.querySelector('.location-feedback');
  const target = getLocationById(scene.locationId);
  const dummyCoords = { lat: 53.522, lng: -1.131 };

  confirmButton.addEventListener('click', () => {
    if (!target) {
      feedbackNode.textContent = 'No target location configured.';
      return;
    }
    const inRange = isWithinRange(dummyCoords, target, 0.003);
    if (inRange) {
      feedbackNode.textContent = scene.successText || 'Resonance confirmed. Advancing...';
      confirmButton.disabled = true;
      markSceneResolved(true, 800);
    } else {
      feedbackNode.textContent = 'Alignment off — adjust your position.';
    }
  });
}

function setupSigilHandler(scene) {
  const clueButton = sceneContainer.querySelector('.sigil-clue');
  const submitButton = sceneContainer.querySelector('.sigil-submit');
  const input = sceneContainer.querySelector('.sigil-code-input');
  const feedbackNode = sceneContainer.querySelector('.sigil-feedback');
  const requiredCode = getCodeById(scene.codeId);

  clueButton?.addEventListener('click', () => {
    feedbackNode.textContent = scene.clue || 'Look for the glowing runes etched nearby.';
  });

  submitButton?.addEventListener('click', () => {
    const entered = (input.value || '').trim();
    if (!requiredCode) {
      feedbackNode.textContent = 'No code configured yet.';
      return;
    }
    if (entered === requiredCode) {
      feedbackNode.textContent = scene.successText || 'The sigil blazes to life!';
      input.setAttribute('disabled', 'disabled');
      submitButton.disabled = true;
      if (clueButton) {
        clueButton.disabled = true;
      }
      markSceneResolved(true, 700);
    } else {
      feedbackNode.textContent = 'The sigil dims. Check your clues and try again.';
    }
  });
}

function setupSigilDiscoveryHandler() {
  const continueButton = sceneContainer.querySelector('.sigil-continue');
  if (continueButton) {
    continueButton.addEventListener('click', () => {
      goToNextScene();
    });
  }
}

function setupCheckInHandler() {
  const continueButton = sceneContainer.querySelector('.checkin-continue');
  if (continueButton) {
    continueButton.addEventListener('click', () => {
      goToNextScene();
    });
  }
}

function markSceneResolved(shouldAdvance, delay = 0) {
  sceneReadyToAdvance = true;
  if (nextButton) {
    nextButton.disabled = false;
  }
  if (shouldAdvance && autoAdvanceEnabled) {
    setTimeout(() => {
      goToNextScene();
    }, delay);
  }
}

function handleNext() {
  const scene = getCurrentScene();
  if (!scene) return;

  if (scene.type === 'dialogue') {
    const advanced = advanceDialogue(scene);
    if (advanced) {
      loadScene();
      return;
    }
  }

  goToNextScene();
}

function handleBack() {
  const scene = getCurrentScene();
  if (!scene) return;

  if (scene.type === 'dialogue') {
    const retreated = retreatDialogue(scene);
    if (retreated) {
      loadScene();
      return;
    }
  }

  if (changeScene(-1)) {
    loadScene();
  }
}

function goToNextScene() {
  resetLine();
  const moved = changeScene(1);
  if (moved) {
    loadScene();
  } else {
    console.info('Reached the current end of the story data.');
  }
}

function createNavButton(label) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'hex-btn';
  button.textContent = label;
  return button;
}

function isWithinRange(current, target, threshold) {
  const latDiff = Math.abs(current.lat - target.lat);
  const lngDiff = Math.abs(current.lng - target.lng);
  return latDiff <= threshold && lngDiff <= threshold;
}

function updateHud() {
  const actTitle = getActLabel();
  const progress = getProgress();
  hud.setActInfo({ title: actTitle, progress });
  hud.updateActList(buildProgressData());
}

function buildProgressData() {
  const scenes = getAllScenes();
  const state = getGameState();
  return scenes.map((actScenes, actIndex) => {
    const actTitle =
      actScenes[0]?.actName || `Act ${actIndex + 1}`;
    const sceneEntries = actScenes.map((scene, sceneIndex) => {
      let status = 'locked';
      if (actIndex < state.act || (actIndex === state.act && sceneIndex < state.scene)) {
        status = 'complete';
      } else if (actIndex === state.act && sceneIndex === state.scene) {
        status = 'current';
      }
      const statusLabel =
        status === 'complete'
          ? 'Completed'
          : status === 'current'
          ? 'In progress'
          : 'Locked';
      return {
        name: scene.sceneName || `Scene ${sceneIndex + 1}`,
        status,
        statusLabel
      };
    });
    return { title: actTitle, scenes: sceneEntries };
  });
}

function logSceneChange(scene) {
  console.log(
    `[Whispers] ${getActLabel()} — ${getSceneName()} | Mode: ${scene.type}`
  );
}

async function bootstrap() {
  hud = new OverlayHUD({
    actButton: document.getElementById('act-info'),
    settingsButton: document.getElementById('settings-btn')
  });

  try {
    const [scenes, codes, locations] = await Promise.all([
      fetchJSON('./data/scenes.json'),
      fetchJSON('./data/codes.json'),
      fetchJSON('./data/locations.json')
    ]);

    initializeState(scenes);
    setCodes(codes);
    setLocations(locations);
    loadScene();
    initDebugOverlay({
      loadScene,
      setAutoAdvanceEnabled,
      isAutoAdvanceEnabled
    });
  } catch (error) {
    console.error('Failed to bootstrap Whispers of the Wild Court:', error);
    sceneContainer.innerHTML = `
      <div class="scene-placeholder">
        <h2>Unable to start</h2>
        <p>Check the console for error details when loading data files.</p>
      </div>
    `;
  }
}

document.addEventListener('DOMContentLoaded', bootstrap);
