export function SigilDiscovery(scene) {
  const title = scene.title || 'Sigil Discovery';
  const description = scene.description || 'The sigil pulses, ready to guide your journey.';
  const image = scene.image || 'assets/objects/sigil_placeholder.png';

  return `
    <div class="scene-placeholder sigil-discovery" data-mode="sigil-discovery">
      <h2>${title}</h2>
      <div class="sigil-glow">
        <img src="${image}" alt="${title}">
      </div>
      <p>${description}</p>
      <button class="pill-btn sigil-continue" type="button">Continue</button>
    </div>
  `;
}
