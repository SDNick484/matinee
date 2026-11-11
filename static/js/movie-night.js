// Matinee — Movie Night recommender modal.
import { api, esc, isAdmin, state } from "./core.js";
import { icon } from "./icons.js";
import { closeModal } from "./forms.js";
import { activePeople, personDotsHTML, personTogglesHTML, readToggles } from "./people.js";

  // ================================================================= Movie Night Modal
  async function openMovieNightModal() {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

    // The audience row is for editors who have people set up (spec 7.4); everyone else gets the plain picker.
    if (isAdmin && state.peopleReady) await state.peopleReady;
    const showAudience = isAdmin && activePeople().length > 0;

    let currentMode = "forgotten_gems";
    let currentPick = null;
    const CAP_KEY = "matinee.movieNight.maxCert";
    let currentCap = "";
    try {
      const saved = localStorage.getItem(CAP_KEY);
      if (["G", "PG", "PG-13"].includes(saved)) currentCap = saved;
    } catch (e) { /* storage unavailable: default to Any */ }

    // Audience: "everyone" | "adults" | "kids" | "custom" (customIds). Stored as a word or a comma-separated id list.
    const AUDIENCE_KEY = "matinee.movieNight.audience";
    let audience = "everyone";
    let customIds = [];
    if (showAudience) {
      let saved = "";
      try { saved = localStorage.getItem(AUDIENCE_KEY) || ""; } catch (e) { /* storage unavailable: Everyone */ }
      if (saved === "adults" || saved === "kids") {
        audience = saved;
      } else if (/^\d+(,\d+)*$/.test(saved)) {
        // Deactivated or deleted people must not come back through the toggles.
        const live = new Set(activePeople().map((p) => p.id));
        customIds = saved.split(",").map(Number).filter((n) => live.has(n));
        if (customIds.length) audience = "custom";
      }
    }
    let fellBack = false;   // the stale-audience fallback runs at most once per modal open

    function audienceValue() {
      return audience === "custom" ? customIds.join(",") : audience;
    }

    // "" means send no audience param: Everyone (today's request), or a Custom audience with nobody picked.
    function audienceParam() {
      if (audience === "everyone" || (audience === "custom" && !customIds.length)) return "";
      return audienceValue();
    }

    function saveAudience() {
      if (audience === "custom" && !customIds.length) return;   // never store a blank audience
      try { localStorage.setItem(AUDIENCE_KEY, audienceValue()); } catch (e) { /* remembered per device when storage allows */ }
    }

    function audienceRowHTML() {
      const opt = (key, label) => `<button type="button" role="radio" aria-checked="${audience === key}" class="movie-night-audience-option" data-audience="${key}">${label}</button>`;
      return `
        <div class="movie-night-cap-label">Who's watching</div>
        <div class="movie-night-audience" role="radiogroup" aria-label="Who's watching">
          ${opt("everyone", "Everyone")}${opt("adults", "Adults")}${opt("kids", "Kids")}${opt("custom", "Custom")}
        </div>
        <div class="movie-night-audience-custom" id="movie-night-audience-custom" ${audience === "custom" ? "" : "hidden"}>
          ${personTogglesHTML(customIds, "Custom audience")}
        </div>`;
    }

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
            ${showAudience ? audienceRowHTML() : ""}
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

      modalRoot.querySelectorAll(".movie-night-audience-option").forEach(opt => {
        opt.addEventListener("click", () => {
          audience = opt.dataset.audience;
          // First time on Custom: start with everyone on, so the pick button is usable straight away.
          if (audience === "custom" && !customIds.length) customIds = activePeople().map((p) => p.id);
          syncAudienceUI();
          saveAudience();
          fetchPick();
        });
      });
      const customBox = document.getElementById("movie-night-audience-custom");
      customBox?.addEventListener("click", (e) => {
        const btn = e.target.closest(".person-toggle");
        if (!btn) return;
        btn.setAttribute("aria-pressed", btn.getAttribute("aria-pressed") === "true" ? "false" : "true");
        customIds = readToggles(customBox);
        saveAudience();
        fetchPick();
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

    // Reflect `audience` / `customIds` in the radios and the Custom toggles (no re-render of the dialog).
    function syncAudienceUI() {
      modalRoot.querySelectorAll(".movie-night-audience-option").forEach((o) => {
        o.setAttribute("aria-checked", String(o.dataset.audience === audience));
      });
      const box = document.getElementById("movie-night-audience-custom");
      if (!box) return;
      box.hidden = audience !== "custom";
      box.innerHTML = personTogglesHTML(customIds, "Custom audience");
    }

    // Spec 7.4: display dots for the people OUTSIDE the audience who have seen this film; no line when nobody
    // outside has. seen_by holds names (everyone who has seen it); `aud` is the response's audience echo.
    function seenByHTML(pick, aud) {
      const names = showAudience && Array.isArray(pick.seen_by) ? pick.seen_by : [];
      const inside = Array.isArray(aud && aud.people) ? aud.people : [];
      const insideIds = new Set(inside.map((x) => x.id));
      const insideNames = new Set(inside.map((x) => x.name));
      const ids = (state.people || [])
        .filter((p) => names.includes(p.name) && !insideIds.has(p.id) && !insideNames.has(p.name))
        .map((p) => p.id);
      return ids.length ? `<div class="movie-night-seen-by"><span class="movie-night-seen-label">Seen by</span> ${personDotsHTML(ids)}</div>` : "";
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
      // Custom with nobody picked: nothing to ask for. Never send a blank audience.
      if (showAudience && audience === "custom" && !customIds.length) {
        currentPick = null;
        content.innerHTML = `<div class="movie-night-empty movie-night-animated"><p class="movie-night-hint">Pick at least one person to choose a movie for.</p></div>`;
        const rollBtn = document.getElementById("btn-movie-night-roll");
        if (rollBtn) rollBtn.disabled = true;
        return;
      }
      const rollBtn = document.getElementById("btn-movie-night-roll");
      if (rollBtn) rollBtn.disabled = false;
      const aud = showAudience ? audienceParam() : "";
      try {
        const res = await api(`${apiPrefix}/recommendations?mode=${encodeURIComponent(currentMode)}&limit=1&random=1${currentCap ? `&max_cert=${encodeURIComponent(currentCap)}` : ""}${aud ? `&audience=${encodeURIComponent(aud)}` : ""}`);
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
                ${seenByHTML(currentPick, res.audience)}
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
        // A saved audience the server no longer accepts: once per open, quietly go back to Everyone, which
        // sends no audience at all (today's request), and remember Everyone.
        if (aud && err.status === 400 && !fellBack) {
          fellBack = true;
          audience = "everyone";
          syncAudienceUI();
          saveAudience();
          fetchPick();
          return;
        }
        content.innerHTML = `<div class="movie-night-empty movie-night-animated"><p>Failed to load recommendation: ${esc(err.message || String(err))}</p></div>`;
        if (viewBtn) viewBtn.disabled = true;
      }
    }

    renderModalSkeleton();
    await fetchPick();
  }

export { openMovieNightModal };
