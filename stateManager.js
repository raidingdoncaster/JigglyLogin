export const gameState = {
  act: 0,
  scene: 0,
  line: 0,
  totalActs: 0,
  scenes: [],
  codes: {},
  locations: {},
  mode: 'dialogue'
};

export function initializeState(rawScenes = {}) {
  const actKeys = Object.keys(rawScenes).sort((a, b) => Number(a) - Number(b));
  gameState.scenes = actKeys.map((key) => rawScenes[key] || []);
  gameState.totalActs = gameState.scenes.length;
  gameState.act = 0;
  gameState.scene = 0;
  gameState.line = 0;
  gameState.mode = gameState.scenes[0]?.[0]?.type || 'dialogue';
  return gameState;
}

export function setCodes(data = {}) {
  gameState.codes = { ...data };
}

export function setLocations(data = {}) {
  gameState.locations = { ...data };
}

export function getGameState() {
  return { ...gameState };
}

export function getCurrentScene() {
  return gameState.scenes[gameState.act]?.[gameState.scene] || null;
}

export function getLineIndex() {
  return gameState.line;
}

export function resetLine() {
  gameState.line = 0;
}

export function advanceDialogue(scene) {
  const lines = scene?.dialogue || [];
  if (gameState.line < lines.length - 1) {
    gameState.line += 1;
    return true;
  }
  return false;
}

export function retreatDialogue(scene) {
  if (gameState.line > 0) {
    gameState.line -= 1;
    return true;
  }
  return false;
}

export function changeScene(step) {
  if (!Number.isInteger(step) || step === 0) {
    return false;
  }

  const targetScene = gameState.scene + step;
  const scenesInAct = gameState.scenes[gameState.act] || [];

  if (targetScene >= 0 && targetScene < scenesInAct.length) {
    gameState.scene = targetScene;
    gameState.line = 0;
    return true;
  }

  if (targetScene < 0 && gameState.act > 0) {
    gameState.act -= 1;
    const prevScenes = gameState.scenes[gameState.act] || [];
    gameState.scene = Math.max(prevScenes.length - 1, 0);
    gameState.line = 0;
    return true;
  }

  if (targetScene >= scenesInAct.length && gameState.act < gameState.totalActs - 1) {
    gameState.act += 1;
    gameState.scene = 0;
    gameState.line = 0;
    return true;
  }

  return false;
}

export function getProgress() {
  const scenesInAct = gameState.scenes[gameState.act] || [];
  if (!scenesInAct.length) {
    return 0;
  }
  return ((gameState.scene + 1) / scenesInAct.length) * 100;
}

export function getActLabel() {
  const scene = getCurrentScene();
  if (scene?.actName) {
    return scene.actName;
  }
  return `Act ${gameState.act + 1}`;
}

export function getSceneName() {
  const scene = getCurrentScene();
  return scene?.sceneName || `Scene ${gameState.scene + 1}`;
}

export function getAllScenes() {
  return gameState.scenes;
}

export function getCodeById(id) {
  return gameState.codes[id] || '';
}

export function getLocationById(id) {
  return gameState.locations[id] || null;
}

export function setActScene(actIndex, sceneIndex) {
  if (!Number.isInteger(actIndex) || actIndex < 0 || actIndex >= gameState.totalActs) {
    return false;
  }
  const scenesInAct = gameState.scenes[actIndex] || [];
  const clampedScene = Math.min(Math.max(sceneIndex, 0), Math.max(scenesInAct.length - 1, 0));
  gameState.act = actIndex;
  gameState.scene = clampedScene;
  gameState.line = 0;
  return true;
}

export function setMode(mode) {
  gameState.mode = mode || '';
}
