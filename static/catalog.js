// === Watchlist Modal ===
function toggleWatchlist() {
  const modal = document.getElementById("watchlist-modal");
  if (modal.classList.contains("hidden")) {
    modal.classList.remove("hidden");
    loadWatchlist();
  } else {
    modal.classList.add("hidden");
  }
}

function loadWatchlist() {
  const container = document.getElementById("watchlist-items");
  container.innerHTML = "Loading...";

  fetch("/watchlist")
    .then(res => res.json())
    .then(data => {
      if (!data.success) {
        container.innerHTML = "<p>⚠️ Could not load watchlist.</p>";
        return;
      }
      if (data.count === 0) {
        container.innerHTML = "<p>📭 Your watchlist is empty.</p>";
        return;
      }

      container.innerHTML = "";
      data.items.forEach(item => {
        const div = document.createElement("div");
        div.className = "watchlist-item";
        div.innerHTML = `
          <div class="thumb" style="background-image:url('${item.image_url}')"></div>
          <div class="meta">
            <strong>${item.name}</strong><br>
            🪙 ${item.cost_stamps} — 📦 ${item.stock}
          </div>
          <button class="remove-btn" onclick="toggleWatchFromModal('${item.id}')">❌</button>
        `;
        container.appendChild(div);
      });
    })
    .catch(err => {
      console.error("Watchlist fetch failed", err);
      container.innerHTML = "<p>⚠️ Error loading watchlist.</p>";
    });
}

function toggleWatchFromModal(itemId) {
  fetch(`/watchlist/toggle/${itemId}`, { method: "POST" })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        loadWatchlist();
        // also update buttons on main catalog page
        const btn = document.querySelector(`.watch-btn[data-id="${itemId}"]`);
        if (btn) btn.innerText = data.watched ? "👀" : "➕";
      }
    });
}

// === Toggle watch from item card ===
function toggleWatch(btn) {
  const itemId = btn.getAttribute("data-id");
  fetch(`/watchlist/toggle/${itemId}`, { method: "POST" })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        btn.innerText = data.watched ? "👀" : "➕";
      }
    })
    .catch(err => console.error("Toggle watch failed", err));
}

// === Carousel Auto-Rotate ===
const track = document.querySelector(".carousel-track");
const slides = document.querySelectorAll(".carousel-slide");
const dots = document.querySelectorAll(".carousel-dots .dot");
let currentIndex = 0;
let interval = setInterval(nextSlide, 6000);

function updateCarousel() {
  track.style.transform = `translateX(-${currentIndex * 100}%)`;
  dots.forEach((d, i) => d.classList.toggle("active", i === currentIndex));
}

function nextSlide() {
  currentIndex = (currentIndex + 1) % slides.length;
  updateCarousel();
}

function prevSlide() {
  currentIndex = (currentIndex - 1 + slides.length) % slides.length;
  updateCarousel();
}

document.querySelector(".carousel-next")?.addEventListener("click", () => {
  nextSlide();
  resetInterval();
});

document.querySelector(".carousel-prev")?.addEventListener("click", () => {
  prevSlide();
  resetInterval();
});

dots.forEach((dot, i) => {
  dot.addEventListener("click", () => {
    currentIndex = i;
    updateCarousel();
    resetInterval();
  });
});

function resetInterval() {
  clearInterval(interval);
  interval = setInterval(nextSlide, 6000);
}

updateCarousel();

const progressBar = document.querySelector(".carousel-progress .progress-bar");

function nextSlide() {
  currentIndex = (currentIndex + 1) % slides.length;
  updateCarousel();
  restartProgressBar();
}

function updateCarousel() {
  track.style.transform = `translateX(-${currentIndex * 100}%)`;
  dots.forEach((d, i) => d.classList.toggle("active", i === currentIndex));
}

function restartProgressBar() {
  progressBar.style.transition = "none";
  progressBar.style.width = "0%";
  setTimeout(() => {
    progressBar.style.transition = "width 6s linear";
    progressBar.style.width = "100%";
  }, 50);
}

function resetInterval() {
  clearInterval(interval);
  interval = setInterval(nextSlide, 6000);
  restartProgressBar();
}

restartProgressBar();