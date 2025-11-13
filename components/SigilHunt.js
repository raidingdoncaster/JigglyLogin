export function SigilHunt(scene) {
  const title = scene.title || 'Sigil Hunt';
  const prompt = scene.prompt || 'Enter the rune code you discovered.';
  const image = scene.image || 'assets/objects/sigil_placeholder.png';

  return `
    <div class="scene-placeholder sigil-hunt" data-mode="sigil-hunt" data-code-id="${scene.codeId || ''}">
      <h2>${title}</h2>
      <p>${prompt}</p>
      <div class="sigil-visual">
        <img src="${image}" alt="${title}">
      </div>
      <div class="sigil-input-group">
        <input class="sigil-code-input" type="text" maxlength="4" placeholder="####" inputmode="numeric" autocomplete="off" />
        <div class="sigil-actions">
          <button class="pill-btn sigil-clue" type="button">Need a clue?</button>
          <button class="pill-btn sigil-submit" type="button">Enter Code</button>
        </div>
      </div>
      <div class="sigil-feedback" role="status" aria-live="polite"></div>
    </div>
  `;
}
