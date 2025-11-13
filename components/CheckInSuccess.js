export function CheckInSuccess(scene) {
  const title = scene.title || 'Resonance Confirmed';
  const message = scene.message || 'Your location syncs with the Wild Court.';
  return `
    <div class="scene-placeholder checkin-success" data-mode="checkin-success">
      <h2>${title}</h2>
      <div class="compass-visual">
        <div class="compass-outer">
          <div class="compass-needle"></div>
        </div>
      </div>
      <p>${message}</p>
      <button class="pill-btn checkin-continue" type="button">Continue</button>
    </div>
  `;
}
