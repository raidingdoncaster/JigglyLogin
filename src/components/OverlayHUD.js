export class OverlayHUD {
  constructor({ actButton, settingsButton }) {
    this.actButton = actButton;
    this.settingsButton = settingsButton;
    this.listeners = {};

    this.actLabel = this.actButton?.querySelector('.act-name') || null;
    this.progressFill = this.actButton?.querySelector('.progress-fill') || null;

    this._createSettingsModal();
    this._createProgressModal();
    this._bindEvents();
  }

  _bindEvents() {
    if (this.actButton) {
      this.actButton.addEventListener('click', () => this.showProgress());
      this.actButton.addEventListener('keyup', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          this.showProgress();
        }
      });
    }
    if (this.settingsButton) {
      this.settingsButton.addEventListener('click', () => this.showSettings());
      this.settingsButton.addEventListener('keyup', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          this.showSettings();
        }
      });
    }
  }

  _createSettingsModal() {
    this.settingsOverlay = document.createElement('div');
    this.settingsOverlay.className = 'modal-overlay';

    const panel = document.createElement('div');
    panel.className = 'modal-panel';
    panel.innerHTML = `
      <h3>Settings</h3>
      <button type="button" data-action="reload">Reload Save</button>
      <button type="button" data-action="credits">Credits</button>
      <button type="button" data-action="logout">Log Out</button>
    `;

    panel.querySelector('[data-action="reload"]').addEventListener('click', () => {
      this.emit('settings:reload');
      this.hideSettings();
    });

    panel.querySelector('[data-action="credits"]').addEventListener('click', () => {
      this.emit('settings:credits');
      this.hideSettings();
    });

    panel.querySelector('[data-action="logout"]').addEventListener('click', () => {
      this.emit('settings:logout');
      this.hideSettings();
    });

    this.settingsOverlay.appendChild(panel);
    this.settingsOverlay.addEventListener('click', (event) => {
      if (event.target === this.settingsOverlay) {
        this.hideSettings();
      }
    });

    document.body.appendChild(this.settingsOverlay);
  }

  _createProgressModal() {
    this.progressOverlay = document.createElement('div');
    this.progressOverlay.className = 'modal-overlay';

    const panel = document.createElement('div');
    panel.className = 'modal-panel';
    const header = document.createElement('h3');
    header.textContent = 'Acts & Scenes';
    this.progressListEl = document.createElement('div');
    this.progressListEl.className = 'progress-acts';
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.dataset.action = 'close-progress';
    closeBtn.textContent = 'Close';
    closeBtn.addEventListener('click', () => this.hideProgress());

    panel.appendChild(header);
    panel.appendChild(this.progressListEl);
    panel.appendChild(closeBtn);

    this.progressOverlay.appendChild(panel);
    this.progressOverlay.addEventListener('click', (event) => {
      if (event.target === this.progressOverlay) {
        this.hideProgress();
      }
    });

    document.body.appendChild(this.progressOverlay);
  }

  showSettings() {
    this.settingsOverlay.classList.add('active');
  }

  hideSettings() {
    this.settingsOverlay.classList.remove('active');
  }

  showProgress() {
    this.progressOverlay.classList.add('active');
  }

  hideProgress() {
    this.progressOverlay.classList.remove('active');
  }

  on(eventName, handler) {
    if (!this.listeners[eventName]) {
      this.listeners[eventName] = [];
    }
    this.listeners[eventName].push(handler);
  }

  emit(eventName, payload) {
    (this.listeners[eventName] || []).forEach((handler) => handler(payload));
  }

  setActInfo({ title, progress }) {
    if (this.actLabel && title) {
      this.actLabel.textContent = title;
    }
    if (this.progressFill && typeof progress === 'number') {
      this.progressFill.style.width = `${Math.max(0, Math.min(100, progress))}%`;
    }
  }

  updateActList(acts = []) {
    if (!this.progressListEl) {
      return;
    }
    this.progressListEl.innerHTML = acts
      .map((act) => {
        const scenesMarkup = (act.scenes || [])
          .map((scene) => {
            const statusClass = scene.status ? `status-${scene.status}` : '';
            return `<p class="${statusClass}"><strong>${scene.name}</strong> — <em>${scene.statusLabel}</em></p>`;
          })
          .join('');
        return `
          <div class="progress-act-block">
            <h4>${act.title}</h4>
            ${scenesMarkup || '<p><em>No scenes loaded</em></p>'}
          </div>
        `;
      })
      .join('');
  }
}
