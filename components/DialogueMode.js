export function DialogueMode(scene, lineIndex = 0) {
  const currentLine = scene.dialogue?.[lineIndex] || {};
  const background = scene.background || 'assets/backgrounds/sample.jpg';
  const character = scene.character || 'assets/characters/sample.png';
  const speakerName = currentLine.name || 'Narrator';
  const text = currentLine.text || '...';

  return `
    <div class="scene-placeholder dialogue-mode" data-mode="dialogue" data-lines="${scene.dialogue?.length || 0}" data-line-index="${lineIndex}">
      <div class="scene-bg" style="background-image: url('${background}');"></div>
      <div class="character">
        <img src="${character}" alt="${speakerName} portrait">
      </div>
      <div class="dialogue-box">
        <div class="char-name">${speakerName}</div>
        <p>${text}</p>
      </div>
    </div>
  `;
}
