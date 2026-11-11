// Matinee — Movie Night recommender modal.
import { api, esc, isAdmin } from "./core.js";
import { icon } from "./icons.js";
import { closeModal } from "./forms.js";

  // ================================================================= Movie Night Modal
  async function openMovieNightModal() {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

    let currentMode = "forgotten_gems";
    let currentPick = null;
    const CAP_KEY = "matinee.movieNight.maxCert";
    let currentCap = "";
    try {
      const saved = localStorage.getItem(CAP_KEY);
      if (["G", "PG", "PG-13"].includes(saved)) currentCap = saved;
    } catch (e) { /* storage unavailable: default to Any */ }

    function renderModalSkeleton() {
      modalRoot.innerHTML = `
        <div class="modal-overlay" id="modal-overlay">
          <div class="modal-dialog movie-night-dialog">
            <div class="modal-header">
              <h2>${icon("dice")} Movie Night Picker</h2>
              <button class="btn-close" id="btn-movie-night-close" aria-label="Close">&times;</button>
            </div>
            <div class="movie-night-tabs" role="tablist" aria-label="Pick mode">
              <button type="button" role="tab" aria-selected="${currentMode === "forgotten_gems"}" class="movie-night-tab ${currentMode === "forgotten_gems" ? "active" : ""}" data-mode="forgotten_gems">Forgotten Gems</button>
              <button type="button" role="tab" aria-selected="${currentMode === "quick_picks"}" class="movie-night-tab ${currentMode === "quick_picks" ? "active" : ""}" data-mode="quick_picks">Quick Picks</button>
              <button type="button" role="tab" aria-selected="${currentMode === "never_watched"}" class="movie-night-tab ${currentMode === "never_watched" ? "active" : ""}" data-mode="never_watched">Never Watched</button>
              <button type="button" role="tab" aria-selected="${currentMode === "next_in_franchise"}" class="movie-night-tab ${currentMode === "next_in_franchise" ? "active" : ""}" data-mode="next_in_franchise">Next in Series</button>
              <button type="button" role="tab" aria-selected="${currentMode === "start_franchise"}" class="movie-night-tab ${currentMode === "start_franchise" ? "active" : ""}" data-mode="start_franchise">Start a Series</button>
              <button type="button" role="tab" aria-selected="${currentMode === "genre_mood"}" class="movie-night-tab ${currentMode === "genre_mood" ? "active" : ""}" data-mode="genre_mood">Surprise Me</button>
            </div>
            <div class="movie-night-cap-label" id="movie-night-cap-label">Rating cap</div>
            <div class="movie-night-cap" role="radiogroup" aria-label="Rating cap">
              <button type="button" role="radio" aria-checked="${currentCap === ""}" class="movie-night-cap-option" data-cert="">Any</button>
              <button type="button" role="radio" aria-checked="${currentCap === "G"}" class="movie-night-cap-option" data-cert="G">G</button>
              <button type="button" role="radio" aria-checked="${currentCap === "PG"}" class="movie-night-cap-option" data-cert="PG">PG</button>
              <button type="button" role="radio" aria-checked="${currentCap === "PG-13"}" class="movie-night-cap-option" data-cert="PG-13">PG-13</button>
            </div>
            <div class="movie-night-content" id="movie-night-content">
              <div class="movie-night-loading">Picking a movie…</div>
            </div>
            <div class="modal-actions movie-night-actions">
              <button type="button" class="btn btn-secondary" id="btn-movie-night-roll">${icon("dice")} Roll Again</button>
              <button type="button" class="btn btn-primary" id="btn-movie-night-view" disabled>Open Movie</button>
              <button type="button" class="btn btn-ghost" id="btn-movie-night-cancel">Close</button>
            </div>
          </div>
        </div>
      `;

      modalRoot.querySelectorAll(".movie-night-tab").forEach(tab => {
        tab.addEventListener("click", () => {
          modalRoot.querySelectorAll(".movie-night-tab").forEach(t => {
            t.classList.remove("active");
            t.setAttribute("aria-selected", "false");
          });
          tab.classList.add("active");
          tab.setAttribute("aria-selected", "true");
          currentMode = tab.dataset.mode;
          fetchPick();
        });
      });

      modalRoot.querySelectorAll(".movie-night-cap-option").forEach(opt => {
        opt.addEventListener("click", () => {
          currentCap = opt.dataset.cert;
          modalRoot.querySelectorAll(".movie-night-cap-option").forEach(o => {
            o.setAttribute("aria-checked", String(o === opt));
          });
          try {
            localStorage.setItem(CAP_KEY, currentCap);
          } catch (e) { /* remembered per device when storage allows */ }
          fetchPick();
        });
      });

      document.getElementById("btn-movie-night-close")?.addEventListener("click", closeModal);
      document.getElementById("btn-movie-night-cancel")?.addEventListener("click", closeModal);
      document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
        if (e.target.id === "modal-overlay") closeModal();
      });
      document.getElementById("btn-movie-night-roll")?.addEventListener("click", () => fetchPick());
      document.getElementById("btn-movie-night-view")?.addEventListener("click", () => {
        if (currentPick) {
          closeModal();
          location.hash = `#/t/${currentPick.id}`;
        }
      });
    }

    let pickToken = 0;
    async function fetchPick() {
      const content = document.getElementById("movie-night-content");
      const viewBtn = document.getElementById("btn-movie-night-view");
      if (!content) return;
      const myToken = ++pickToken;
      content.innerHTML = `<div class="movie-night-loading">Picking a movie…</div>`;
      if (viewBtn) viewBtn.disabled = true;

      const apiPrefix = isAdmin ? "/admin/api" : "/api";
      try {
        const res = await api(`${apiPrefix}/recommendations?mode=${encodeURIComponent(currentMode)}&limit=1&random=1${currentCap ? `&max_cert=${encodeURIComponent(currentCap)}` : ""}`);
        if (myToken !== pickToken) return; // a newer tab/cap tap superseded this response
        if (res && res.results && res.results.length > 0) {
          currentPick = res.results[0];
          const runtimeStr = currentPick.runtime ? `${currentPick.runtime}m` : "";
          const metaParts = [currentPick.year, runtimeStr, (currentPick.formats || []).join(", ")].filter(Boolean).join(" · ");
          const ratingVal = currentPick.user_rating != null ? currentPick.user_rating.toFixed(1) : (currentPick.tmdb_vote ? currentPick.tmdb_vote.toFixed(1) : null);
          const ratingBadge = ratingVal ? `<span class="movie-night-score">★ ${ratingVal}</span>` : "";

          content.innerHTML = `
            <div class="movie-night-card movie-night-animated">
              ${currentPick.poster_url ? `<img src="${currentPick.poster_url}" class="movie-night-poster" alt="${esc(currentPick.title)}">` : '<div class="movie-night-poster placeholder">No Poster</div>'}
              <div class="movie-night-info">
                <div class="movie-night-title-row">
                  <h3 class="movie-night-title">${esc(currentPick.title)}</h3>
                  ${ratingBadge}
                </div>
                <div class="movie-night-meta">${esc(metaParts)}</div>
                ${currentPick.certification ? `<div><span class="cert-badge">${esc(currentPick.certification)}</span></div>` : ""}
                ${currentPick.genres && currentPick.genres.length > 0 ? `<div class="movie-night-genres">${currentPick.genres.map(g => `<span class="genre-tag">${esc(g)}</span>`).join(" ")}</div>` : ""}
                ${currentPick.collection_name ? `<div class="movie-night-collection">${esc(currentPick.collection_name)}</div>` : ""}
                <div class="movie-night-reason">${esc(currentPick.reason || currentPick.recommendation_reason || "Curated pick")}</div>
              </div>
            </div>
          `;
          if (viewBtn) viewBtn.disabled = false;
        } else {
          currentPick = null;
          content.innerHTML = `
            <div class="movie-night-empty movie-night-animated">
              <div style="font-size:2rem;margin-bottom:8px;color:var(--amber)">${icon("popcorn")}</div>
              <p style="font-weight:600;color:var(--ink);margin-bottom:4px">No matching movies found</p>
              <p class="movie-night-hint">${esc((res && res.hint) || 'Try rolling in another category like "Quick Picks" or "Surprise Me"!')}</p>
              ${currentCap && !(res && res.hint && /unrated/i.test(res.hint)) ? `<p class="movie-night-hint">Unrated films are hidden while a rating cap is on.</p>` : ""}
            </div>
          `;
          if (viewBtn) viewBtn.disabled = true;
        }
      } catch (err) {
        if (myToken !== pickToken) return;
        content.innerHTML = `<div class="movie-night-empty movie-night-animated"><p>Failed to load recommendation: ${esc(err.message || String(err))}</p></div>`;
        if (viewBtn) viewBtn.disabled = true;
      }
    }

    renderModalSkeleton();
    await fetchPick();
  }

export { openMovieNightModal };
