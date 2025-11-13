import {
  gameState,
  changeScene,
  resetLine,
  getAllScenes,
  getGameState,
  setActScene,
  getCurrentScene
} from './stateManager.js';

let panel;
let actSelect;
let sceneSelect;
let modeLabel;
let autoAdvanceCheckbox;
let initialized = false;
let dependencies = {};
let lastActIndex = -1;

export function initDebugOverlay(deps) {
  if (initialized) {
    return;
  }
  dependencies = deps || {};
  createPanel();
  bindEvents();
  populateActs();
  updatePanel();
  initialized = true;
}

function createPanel() {
  panel = document.createElement('div');
  panel.id = 'debug-overlay';
  panel.innerHTML = `
    <div id="debug-header">🧩 Debug Panel</div>
    <div class="debug-row">
      <label for="debug-act">Act</label>
      <select id="debug-act"></select>
    </div>
    <div class="debug-row">
      <label for="debug-scene">Scene</label>
      <select id="debug-scene"></select>
    </div>
    <div class="debug-row">
      <label>Mode</label>
      <span id="debug-mode">–</span>
    </div>
    <div class="debug-row buttons">
      <button id="debug-prev" type="button">Prev Scene</button>
      <button id="debug-next" type="button">Next Scene</button>
      <button id="debug-reload" type="button">Reload Scene</button>
    </div>
    <label class="debug-row checkbox">
      <input type="checkbox" id="debug-auto-advance" checked /> Auto Advance on Correct Answer
    </label>
  `;

  document.body.appendChild(panel);
  panel.style.display = 'none';

  actSelect = panel.querySelector('#debug-act');
  sceneSelect = panel.querySelector('#debug-scene');
  modeLabel = panel.querySelector('#debug-mode');
  autoAdvanceCheckbox = panel.querySelector('#debug-auto-advance');
}

function bindEvents() {
  document.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() === 'd' && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const activeTag = document.activeElement?.tagName;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(activeTag)) {
        return;
      }
      togglePanel();
    }
  });

  panel.querySelector('#debug-prev').addEventListener('click', () => {
    if (changeScene(-1)) {
      dependencies.loadScene?.();
    }
  });

  panel.querySelector('#debug-next').addEventListener('click', () => {
    if (changeScene(1)) {
      dependencies.loadScene?.();
    }
  });

  panel.querySelector('#debug-reload').addEventListener('click', () => {
    resetLine();
    dependencies.loadScene?.();
  });

  actSelect.addEventListener('change', () => {
    const actIndex = clampIndex(parseInt(actSelect.value, 10) || 0, getAllScenes().length);
    setActScene(actIndex, 0);
    populateScenes(actIndex);
    resetLine();
    dependencies.loadScene?.();
  });

  sceneSelect.addEventListener('change', () => {
    const actIndex = clampIndex(parseInt(actSelect.value, 10) || 0, getAllScenes().length);
    const sceneIndex = clampIndex(
      parseInt(sceneSelect.value, 10) || 0,
      getAllScenes()[actIndex]?.length || 0
    );
    setActScene(actIndex, sceneIndex);
    resetLine();
    dependencies.loadScene?.();
  });

  autoAdvanceCheckbox.addEventListener('change', () => {
    dependencies.setAutoAdvanceEnabled?.(autoAdvanceCheckbox.checked);
  });

  document.addEventListener('debug:scene-updated', updatePanel);
}

function togglePanel() {
  const isHidden = panel.style.display === 'none';
  panel.style.display = isHidden ? 'block' : 'none';
  if (!isHidden) {
    return;
  }
  updatePanel();
}

function populateActs() {
  const acts = getAllScenes();
  actSelect.innerHTML = acts
    .map((actScenes, index) => {
      const label = actScenes[0]?.actName || `Act ${index + 1}`;
      return `<option value="${index}">${label}</option>`;
    })
    .join('');
  populateScenes(gameState.act);
}

function populateScenes(actIndex) {
  const scenes = getAllScenes()[actIndex] || [];
  sceneSelect.innerHTML = scenes
    .map((scene, index) => {
      const label = scene.sceneName || `Scene ${index + 1}`;
      return `<option value="${index}">${label}</option>`;
    })
    .join('');
}

function updatePanel() {
  const state = getGameState();
  if (lastActIndex !== state.act) {
    populateActs();
    lastActIndex = state.act;
  } else {
    populateScenes(state.act);
  }
  actSelect.value = state.act;
  sceneSelect.value = state.scene;
  const currentScene = getCurrentScene();
  modeLabel.textContent = gameState.mode || currentScene?.type || 'unknown';
  if (dependencies.isAutoAdvanceEnabled) {
    autoAdvanceCheckbox.checked = Boolean(dependencies.isAutoAdvanceEnabled());
  }
}

function clampIndex(value, length) {
  if (length <= 0) {
    return 0;
  }
  return Math.min(Math.max(value, 0), length - 1);
}
