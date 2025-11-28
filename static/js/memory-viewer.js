(function () {
  var overlay = document.querySelector("[data-memory-overlay]");
  if (!overlay) return;

  var payload = overlay.getAttribute("data-memory-albums") || "[]";
  var albums = [];
  try {
    albums = JSON.parse(payload);
  } catch (err) {
    console.warn("Memory albums payload invalid", err);
    albums = [];
  }
  if (!Array.isArray(albums) || !albums.length) {
    return;
  }

  var albumMap = {};
  albums.forEach(function (album) {
    if (album && album.id) {
      albumMap[album.id] = album;
      if (!album.slides) {
        album.slides = [];
      }
      album.slide_count = album.slide_count || album.slides.length;
    }
  });
  if (!Object.keys(albumMap).length) {
    return;
  }

  var bodyLock = window.__overlayLock || {
    lock: function () {},
    unlock: function () {},
  };

  var state = {
    albumId: null,
    index: 0,
    zoom: 1,
  };

  var frame = overlay.querySelector("[data-memory-frame]");
  var mediaWrap = overlay.querySelector("[data-memory-media]");
  var imageEl = overlay.querySelector("[data-memory-image]");
  var captionEl = overlay.querySelector("[data-memory-caption]");
  var titleEl = overlay.querySelector("[data-memory-title]");
  var metaEl = overlay.querySelector("[data-memory-meta]");
  var progressEl = overlay.querySelector("[data-memory-progress]");
  var zoomLabel = overlay.querySelector("[data-memory-zoom-level]");
  var prevBtn = overlay.querySelector("[data-memory-prev]");
  var nextBtn = overlay.querySelector("[data-memory-next]");
  var zoomInBtn = overlay.querySelector("[data-memory-zoom-in]");
  var zoomOutBtn = overlay.querySelector("[data-memory-zoom-out]");
  var closeEls = overlay.querySelectorAll("[data-memory-close]");

  function clampIndex(album, idx) {
    var total = (album.slides || []).length;
    if (!total) return 0;
    if (idx < 0) return total - 1;
    if (idx >= total) return 0;
    return idx;
  }

  function applyZoom() {
    if (imageEl) {
      imageEl.style.transform = "scale(" + state.zoom + ")";
    }
    if (zoomLabel) {
      zoomLabel.textContent = Math.round(state.zoom * 100) + "%";
    }
  }

  function setZoom(level) {
    state.zoom = Math.min(Math.max(level, 1), 3);
    applyZoom();
  }

  function formatDate(value) {
    if (!value) return "Recently";
    try {
      var date = new Date(value);
      if (!isNaN(date.getTime())) {
        return date.toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
          year: "numeric",
        });
      }
    } catch (err) {
      /* noop */
    }
    return "Recently";
  }

  function render() {
    var album = albumMap[state.albumId];
    if (!album) return;
    var slides = album.slides || [];
    var slide = slides[state.index];
    if (!slide) return;
    if (titleEl) {
      titleEl.textContent = album.title || "Memory album";
    }
    if (metaEl) {
      var published = formatDate(album.published_at);
      metaEl.textContent = slides.length + " photos • " + published;
    }
    if (imageEl) {
      imageEl.src = slide.image || "";
      imageEl.alt = slide.caption || album.title || "Memory photo";
      imageEl.style.transform = "scale(1)";
    }
    if (captionEl) {
      captionEl.textContent = slide.caption || "";
    }
    if (progressEl) {
      progressEl.textContent = (state.index + 1) + "/" + slides.length;
    }
    applyZoom();
  }

  function openOverlay(albumId, startIndex) {
    var album = albumMap[albumId];
    if (!album || !(album.slides || []).length) {
      return;
    }
    state.albumId = albumId;
    state.index = clampIndex(album, typeof startIndex === "number" ? startIndex : 0);
    state.zoom = 1;
    overlay.hidden = false;
    overlay.classList.add("is-open");
    bodyLock.lock();
    render();
    setTimeout(function () {
      frame && frame.focus();
    }, 30);
  }

  function closeOverlay() {
    if (!overlay.classList.contains("is-open")) return;
    overlay.classList.remove("is-open");
    overlay.hidden = true;
    state.albumId = null;
    state.index = 0;
    state.zoom = 1;
    bodyLock.unlock();
  }

  function advance(step) {
    var album = albumMap[state.albumId];
    if (!album) return;
    state.index = clampIndex(album, state.index + step);
    state.zoom = 1;
    render();
  }

  prevBtn && prevBtn.addEventListener("click", function () {
    advance(-1);
  });
  nextBtn && nextBtn.addEventListener("click", function () {
    advance(1);
  });
  zoomInBtn && zoomInBtn.addEventListener("click", function () {
    setZoom(state.zoom + 0.25);
  });
  zoomOutBtn && zoomOutBtn.addEventListener("click", function () {
    setZoom(state.zoom - 0.25);
  });
  closeEls.forEach(function (btn) {
    btn.addEventListener("click", closeOverlay);
  });

  overlay.addEventListener("click", function (evt) {
    if (evt.target === overlay || evt.target.hasAttribute("data-memory-close")) {
      closeOverlay();
    }
  });

  document.addEventListener("keydown", function (evt) {
    if (!overlay.classList.contains("is-open")) return;
    if (evt.key === "Escape") {
      closeOverlay();
    } else if (evt.key === "ArrowRight") {
      evt.preventDefault();
      advance(1);
    } else if (evt.key === "ArrowLeft") {
      evt.preventDefault();
      advance(-1);
    } else if (evt.key === "+" || evt.key === "=") {
      evt.preventDefault();
      setZoom(state.zoom + 0.25);
    } else if (evt.key === "-" || evt.key === "_") {
      evt.preventDefault();
      setZoom(state.zoom - 0.25);
    }
  });

  var swipeStartX = null;
  var pointerActive = false;

  function handleSwipeEnd(clientX) {
    if (swipeStartX === null) return;
    var diff = clientX - swipeStartX;
    swipeStartX = null;
    pointerActive = false;
    if (Math.abs(diff) > 40) {
      advance(diff > 0 ? -1 : 1);
    }
  }

  if (mediaWrap) {
    mediaWrap.addEventListener("pointerdown", function (evt) {
      if (evt.pointerType === "mouse" && evt.button !== 0) return;
      pointerActive = true;
      swipeStartX = evt.clientX;
    });
    mediaWrap.addEventListener("pointerup", function (evt) {
      if (!pointerActive) return;
      handleSwipeEnd(evt.clientX);
    });
    mediaWrap.addEventListener("touchstart", function (evt) {
      swipeStartX = (evt.touches[0] || {}).clientX || 0;
    }, { passive: true });
    mediaWrap.addEventListener("touchend", function (evt) {
      handleSwipeEnd((evt.changedTouches[0] || {}).clientX || 0);
    });
  }

  function parseIndex(value) {
    var parsed = parseInt(value, 10);
    return isNaN(parsed) ? 0 : parsed;
  }

  document.querySelectorAll("[data-memory-launch]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var albumId = btn.getAttribute("data-memory-launch");
      if (!albumId) return;
      openOverlay(albumId, parseIndex(btn.getAttribute("data-memory-start")));
    });
  });

  document.querySelectorAll("[data-photo-trigger]").forEach(function (el) {
    function openFromTrigger(evt) {
      evt.preventDefault();
      var albumId = el.getAttribute("data-memory-album");
      if (!albumId) return;
      openOverlay(albumId, parseIndex(el.getAttribute("data-photo-index")));
    }
    el.addEventListener("click", openFromTrigger);
    el.addEventListener("keydown", function (evt) {
      if (evt.key === "Enter" || evt.key === " ") {
        openFromTrigger(evt);
      }
    });
  });
})();
