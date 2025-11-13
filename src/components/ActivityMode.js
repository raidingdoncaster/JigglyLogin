export function ActivityMode(scene) {
  const title = scene.title || 'Trial of Insight';
  const question = scene.question || 'Answer the challenge to continue.';
  const answers = Array.isArray(scene.answers) ? scene.answers : [];

  const answerButtons = answers
    .map(
      (answer, index) =>
        `<button class="activity-option" type="button" data-index="${index}">${answer}</button>`
    )
    .join('');

  return `
    <div class="scene-placeholder activity-mode" data-mode="activity">
      <h2>${title}</h2>
      <p class="activity-question">${question}</p>
      <div class="activity-options">
        ${answerButtons}
      </div>
      <div class="activity-feedback" role="status" aria-live="polite"></div>
    </div>
  `;
}
