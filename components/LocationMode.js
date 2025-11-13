export function LocationMode(scene) {
  const title = scene.title || 'Location Check';
  const prompt = scene.prompt || 'Confirm you have reached the correct location.';
  return `
    <div class="scene-placeholder location-mode" data-mode="location" data-location="${scene.locationId || ''}">
      <h2>${title}</h2>
      <p>${prompt}</p>
      <div class="map-placeholder">
        <span>Simulated Map</span>
      </div>
      <button class="pill-btn location-confirm" type="button">I’m Here!</button>
      <div class="location-feedback" role="status" aria-live="polite"></div>
    </div>
  `;
}
