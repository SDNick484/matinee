// Matinee — poster grid, search and filters (7.2).
import { api, closeFilterPopovers, esc, exportHref, getChipClass, isAdmin, state, toast } from "./core.js";
import { activePeople } from "./people.js";
import { getChipIcon, icon } from "./icons.js";
import { playButtonHtml, wirePlay } from "./play.js";

  // The editor-only filters: the people filters (spec 2026-11-11-per-person §6.4) and `code` (unredeemed digital codes,
  // spec 2026-11-11-copy-details §4). /api answers 400 for any of them.
  const EDITOR_FILTERS = ["unseen_by", "attendance", "code"];

  function hasEditorFilter(query) {
    return EDITOR_FILTERS.some((k) => query.has(k));
  }

  // A location hash ("#/?unseen_by=kids&code=unredeemed&sort=year") without the editor filters ("#/?sort=year").
  function hashWithoutEditorFilters(hash) {
    const raw = String(hash || "").replace(/^#/, "");
    const [path, query] = raw.split("?");
    const q = new URLSearchParams(query || "");
    EDITOR_FILTERS.forEach((k) => q.delete(k));
    const qStr = q.toString();
    return `#${path || "/"}${qStr ? `?${qStr}` : ""}`;
  }

  // Drop the editor filters from the current route and the address bar, without a new history entry or a
  // hashchange (the caller renders straight away).
  function dropEditorFilters() {
    const hash = hashWithoutEditorFilters(location.hash || "#/");
    history.replaceState(history.state, "", hash);
    EDITOR_FILTERS.forEach((k) => state.route.query.delete(k));
    state.grid.lastHash = hash;
  }

  // ================================================================= 7.2 Poster Grid
  // The toolbar (search box, filter bar, sort menu) is built once per visit to the grid. A filter, sort or search
  // change then re-renders only the filter bar's labels and the results, so the search box keeps its focus and a
  // phone's keyboard stays open. The facets do not depend on the filters and are fetched once per visit.
  let gridReq = 0;      // the newest results request: an older, slower answer never renders over it
  let searchTimer = 0;  // the search debounce
  let drawnBarKey = null;   // the filters the filter bar was last drawn for

  // The query in the address bar, parsed into state.grid.
  function syncGridState() {
    const get = (k) => state.route.query.get(k) || "";
    const mediumStr = get("medium");
    const qualityStr = get("quality");
    const genreStr = get("genre");
    const both = get("both");
    const kind = get("kind");
    const format = get("format");
    const locationId = (get("location_id") || get("location")).trim();

    const medium = mediumStr ? mediumStr.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) : [];
    if (!medium.length) {
      if (both === "1" || kind === "both") medium.push("both");
      else if (kind === "physical") medium.push("disc");
      else if (kind === "digital") medium.push("streaming");
    }

    const quality = qualityStr ? qualityStr.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) : [];
    if (!quality.length && format) {
      if (format === "4K UHD" || format === "Digital 4K") quality.push("4k");
      else if (format === "Blu-ray" || format === "3D Blu-ray" || format === "Digital HD") quality.push("hd");
      else if (format === "DVD" || format === "VHS" || format === "Digital SD") quality.push("sd");
    }

    const genre = genreStr ? genreStr.split(",").map((s) => s.trim()).filter(Boolean) : [];

    state.grid.q = get("q");
    state.grid.medium = medium;
    state.grid.quality = quality;
    state.grid.genre = genre;
    state.grid.rating = get("rating");
    state.grid.score = get("score");
    state.grid.watched = get("watched");
    state.grid.unseen_by = get("unseen_by");
    state.grid.attendance = get("attendance");
    state.grid.code = get("code");
    state.grid.location_id = locationId;
    state.grid.kind = kind;
    state.grid.both = both;
    state.grid.format = format;
    state.grid.service = get("service");
    state.grid.sort = get("sort") || "title";
  }

  // The toolbar and the empty results slot, drawn once per visit.
  function buildToolbar(container) {
    clearTimeout(searchTimer);   // a pause from an earlier visit must not search this one
    drawnBarKey = null;
    container.innerHTML = `
      <div class="toolbar">
        <div class="search-wrap">
          <span class="search-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
          </span>
          <input type="text" id="grid-search" class="search-input" placeholder="Search collection…" value="" autocomplete="off" autocorrect="off">
          <button type="button" id="grid-clear-search" class="clear-btn" aria-label="Clear search" style="display:none">✕</button>
        </div>

        <div class="filter-row">
          <div class="filter-bar" id="filter-bar">
            <!-- Populated from facets -->
          </div>
          <div>
            <select id="grid-sort" class="sort-select" aria-label="Sort by">
              <option value="title">Title (A–Z)</option>
              <option value="added">Recently Added</option>
              <option value="year">Release Year</option>
              <option value="rating">Highest Rated</option>
              <option value="unrated">Unrated</option>
              <option value="last_watched">Last Watched</option>
              <option value="least_watched">Least Recently / Never Watched</option>
            </select>
          </div>
        </div>
      </div>

      ${isAdmin ? `<div id="grid-export-slot" class="export-bar"></div>` : ""}

      <div id="grid-container">
        <div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading movies…</div>
      </div>
    `;

    const searchInput = document.getElementById("grid-search");
    searchInput.addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => searchGrid(e.target.value), 350);
      document.getElementById("grid-clear-search").style.display = e.target.value ? "" : "none";
    });
    searchInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      clearTimeout(searchTimer);   // Enter searches now, without waiting out the pause
      searchGrid(e.target.value);
    });
    document.getElementById("grid-clear-search").addEventListener("click", () => {
      clearTimeout(searchTimer);
      searchInput.value = "";
      searchGrid("");
    });
    document.getElementById("grid-sort").addEventListener("change", (e) => {
      applyGridParam("sort", e.target.value);
    });
  }

  // Bring the toolbar in line with the address bar (a filter, a sort, or a hash typed or pasted by hand). The search
  // box is left alone while it already says the same thing, so a word being typed is never overwritten.
  function syncToolbar() {
    const input = document.getElementById("grid-search");
    const q = state.grid.q;
    if (input && input.value.trim() !== q) input.value = q;
    const clearBtn = document.getElementById("grid-clear-search");
    if (clearBtn) clearBtn.style.display = q || (input && input.value) ? "" : "none";
    const sortSelect = document.getElementById("grid-sort");
    if (sortSelect) sortSelect.value = state.grid.sort;
  }

  // A search typed in the box: put it in the address bar with replaceState (no hashchange and no history entry per
  // pause), then refresh just the results.
  function searchGrid(value) {
    if (state.route.path !== "/" || !document.getElementById("grid-search")) return;   // the user has left the grid
    const q = new URLSearchParams(state.route.query);
    const text = String(value || "").trim();
    if (text) q.set("q", text);
    else q.delete("q");
    if (q.toString() === state.route.query.toString()) return;
    const qStr = q.toString();
    const hash = qStr ? `#/?${qStr}` : "#/";
    history.replaceState(history.state, "", hash);
    state.route.query = q;
    state.grid.lastHash = hash;
    window.dispatchEvent(new Event("matinee:hashreplaced"));   // the router refreshes the header's sign-in link
    syncGridState();
    syncToolbar();
    const container = document.getElementById("view");
    if (container) loadGrid(container, { seq: state.routeSeq, fresh: false });
  }

  async function renderGrid(container, { retried = false } = {}) {
    const seq = state.routeSeq;   // see core.js
    // A visitor cannot use the editor filters (an editor's shared link, or Public View from a filtered grid):
    // drop them rather than let /api answer 400 with no way back.
    if (!isAdmin && hasEditorFilter(state.route.query)) dropEditorFilters();
    syncGridState();

    // The toolbar survives a filter, sort or search change; any other page replaced it, so a visit starts afresh.
    const fresh = !container.querySelector("#grid-search");
    if (fresh) buildToolbar(container);
    syncToolbar();
    await loadGrid(container, { seq, fresh, retried });
  }

  // Fetch and draw the results (and, on a fresh visit, the facets).
  async function loadGrid(container, { seq, fresh, retried = false }) {
    const mine = ++gridReq;
    const needFacets = fresh || !state.grid.facets;
    const apiPrefix = isAdmin ? "/admin/api" : "/api";
    try {
      const [facetsData, titlesData] = await Promise.all([
        needFacets ? api(`${apiPrefix}/facets`) : state.grid.facets,
        api(`${apiPrefix}/titles?${state.route.query.toString()}`),
      ]);

      if (isAdmin && state.peopleReady) await state.peopleReady;   // the Watched popover names people
      if (seq !== state.routeSeq || mine !== gridReq) return;   // the user left the grid, or a newer search is on its way
      state.grid.facets = facetsData;
      state.grid.titles = titlesData.titles;

      // The filter bar only depends on the filters, never on the search text.
      const barKey = [...state.route.query].filter(([k]) => k !== "q" && k !== "sort").map((kv) => kv.join("=")).join("&");
      if (barKey !== drawnBarKey) {
        drawnBarKey = barKey;
        renderFilterBar();
      }
      renderExportBar();
      renderGridContent();
      // The ▶ on a playable poster (editors only): one listener on the grid, which is drawn afresh on every visit.
      if (isAdmin && fresh) wirePlay(document.getElementById("grid-container"), (id) => (state.grid.titles || []).find((t) => t.id === id));

      if (fresh && state.grid.scrollY > 0) {
        // Back from a title (or another page): restore the scroll position, once.
        window.scrollTo({ top: state.grid.scrollY, behavior: "instant" });
      } else if (!fresh && window.scrollY > 0) {
        window.scrollTo({ top: 0, behavior: "instant" });   // a new filter, sort or search starts at the top
      }
      if (fresh) state.grid.scrollY = 0;
    } catch (err) {
      if (seq !== state.routeSeq || mine !== gridReq) return;
      // An editor's filter the server rejects (a deleted person, nobody set up, or a code value it doesn't know):
      // retry once without it.
      if (isAdmin && !retried && err.status === 400 && hasEditorFilter(state.route.query)) {
        dropEditorFilters();
        toast(err.message || "That filter no longer applies.");
        await renderGrid(container, { retried: true });
        return;
      }
      const gridContainer = document.getElementById("grid-container");
      if (gridContainer) {
        gridContainer.innerHTML = `
          <div class="empty-state">
            <h2>Unable to load collection</h2>
            <p>${esc(err.message || "Failed to connect to Matinee server")}</p>
            <button class="btn" data-reload>Retry</button>
          </div>
        `;
      }
    }
  }

  // ================================================================= Export these (spec 2026-11-18-grid-export)
  // The copy filters the CSV export understands, read from the route query (state.grid.medium and .quality are
  // derived from format and kind, so they cannot tell a Quality menu choice from a format), and the other active
  // filters it ignores. `filters` is empty when no copy filter is active.
  function exportParams(query) {
    const get = (k) => (query.get(k) || "").trim();
    const mediums = get("medium").split(",").map((m) => m.trim().toLowerCase()).filter(Boolean);
    let kind = get("kind");
    if (kind !== "physical" && kind !== "digital") kind = "";
    if (!kind && mediums.length === 1) kind = mediums[0] === "disc" ? "physical" : mediums[0] === "streaming" ? "digital" : "";
    const filters = { kind, format: get("format"), service: get("service"), location: get("location_id") || get("location") };
    const active = Object.keys(filters).some((k) => filters[k]);
    const bothMedia = mediums.includes("both") || get("both") === "1" || get("kind") === "both";
    const ignored = [];
    if (get("quality")) ignored.push("quality");
    if (bothMedia) ignored.push("medium");
    if (get("code")) ignored.push("code");
    const other = ["q", "genre", "watched", "unseen_by", "attendance", "rating", "score"].some((k) => get(k));
    return { filters: active ? filters : null, ignored, noteNeeded: other || ignored.length > 0 };
  }

  // "Export these": two download links to the CSV for the active copy filters. Editors only, and only while a
  // filter the export honors is active.
  function renderExportBar() {
    const slot = document.getElementById("grid-export-slot");
    if (!slot) return;
    const { filters, ignored, noteNeeded } = exportParams(state.route.query);
    if (!isAdmin || !filters) {
      slot.innerHTML = "";
      return;
    }
    const names = ["search", "genre", ...ignored];
    slot.innerHTML = `
      <div class="filter-popover" id="popover-export">
        <button type="button" class="filter-btn export-btn" id="btn-grid-export" aria-expanded="false" aria-haspopup="true">
          ${icon("download")}<span>Export these</span><span class="caret">▾</span>
        </button>
        <div class="popover-menu" id="grid-export" role="menu">
          <a class="export-choice" id="export-copy" role="menuitem" href="${esc(exportHref(filters, "copy"))}" download>One row per copy</a>
          <a class="export-choice" id="export-title" role="menuitem" href="${esc(exportHref(filters, "title"))}" download>One row per title</a>
        </div>
      </div>
      ${noteNeeded ? `<p class="export-note" id="grid-export-note">Export ignores ${names.join(", ")} and watch filters.</p>` : ""}
    `;
    const popover = document.getElementById("popover-export");
    const btn = document.getElementById("btn-grid-export");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const wasOpen = popover.classList.contains("open");
      closeFilterPopovers();
      if (!wasOpen) {
        popover.classList.add("open");
        btn.setAttribute("aria-expanded", "true");
      }
    });
    popover.querySelectorAll(".export-choice").forEach((a) => {
      a.addEventListener("click", () => {   // the browser downloads the file; close the menu behind it
        popover.classList.remove("open");
        btn.setAttribute("aria-expanded", "false");
      });
    });
  }

  function applyGridParam(key, value) {
    const q = new URLSearchParams(state.route.query);
    if (!value) {
      q.delete(key);
    } else {
      q.set(key, value);
    }
    const qStr = q.toString();
    location.hash = qStr ? `#/?${qStr}` : "#/";
  }

  function getMediumLabel(medium) {
    if (!medium || medium.length === 0) return "Medium: All";
    if (medium.includes("both")) return "Medium: Disc + Stream";
    const parts = [];
    if (medium.includes("disc")) parts.push("Disc");
    if (medium.includes("streaming")) parts.push("Streaming");
    return parts.length ? `Medium: ${parts.join(", ")}` : "Medium: All";
  }

  function getQualityLabel(quality) {
    if (!quality || quality.length === 0) return "Quality: All";
    const map = { "4k": "4K", hd: "HD", sd: "SD" };
    const parts = quality.map((q) => map[q] || q.toUpperCase());
    return `Quality: ${parts.join(", ")}`;
  }

  function getGenreLabel(genre) {
    if (!genre || genre.length === 0) return "Genre: All";
    if (genre.length === 1) return `Genre: ${genre[0]}`;
    const joined = genre.join(", ");
    if (joined.length <= 16) return `Genre: ${joined}`;
    return `Genre (${genre.length})`;
  }

  function unseenByLabel(val) {
    if (val === "everyone" || val === "adults" || val === "kids") return val.charAt(0).toUpperCase() + val.slice(1);
    const names = String(val).split(",").map((t) => {
      const p = (state.people || []).find((x) => String(x.id) === t.trim());
      return p ? p.name : t.trim();
    });
    return names.join(", ");
  }

  function getWatchedLabel(watched, unseenBy = "", attendance = "") {
    const parts = [];
    if (watched === "watched") parts.push("Seen");
    else if (watched === "unwatched") parts.push("Unseen");
    else if (watched) parts.push(watched);
    if (unseenBy) parts.push(`Unseen by ${unseenByLabel(unseenBy)}`);
    if (attendance === "unknown") parts.push("Who watched: unknown");
    return parts.length ? `Watched: ${parts.join(" · ")}`.replace(/^Watched: (Unseen by|Who watched)/, "$1") : "Watched: All";
  }

  function getRatingLabel(rating, score) {
    if (score) return `Rating: ★ ${Number(score).toFixed(1)}`;
    if (!rating) return "Rating: All";
    if (rating === "8+" || rating === "8") return "Rating: 8.0+ ★";
    if (rating === "unrated") return "Rating: Unrated";
    return `Rating: ${rating}`;
  }

  function renderFilterBar() {
    const bar = document.getElementById("filter-bar");
    if (!bar) return;

    const f = state.grid.facets || {};
    const medium = state.grid.medium || [];
    const quality = state.grid.quality || [];
    const genre = state.grid.genre || [];
    const rating = state.grid.rating || "";
    const score = state.grid.score || "";   // one exact score, from the Stats breakdown
    const watched = state.grid.watched || "";
    const unseenBy = state.grid.unseen_by || "";
    const attendance = state.grid.attendance || "";
    const showWho = isAdmin && activePeople().length > 0;   // editor-only: both filters need people
    const whoActive = Boolean(unseenBy) || Boolean(attendance);
    const codeActive = isAdmin && state.grid.code === "unredeemed";   // editor-only, like the people filters
    const locationId = state.grid.location_id || "";

    const hasFilters =
      whoActive ||
      codeActive ||
      medium.length > 0 ||
      quality.length > 0 ||
      genre.length > 0 ||
      Boolean(rating) ||
      Boolean(score) ||
      Boolean(watched) ||
      Boolean(locationId) ||
      state.grid.kind ||
      state.grid.both ||
      state.grid.format ||
      state.grid.service;

    const mediumLabel = getMediumLabel(medium);
    const qualityLabel = getQualityLabel(quality);
    const genreLabel = getGenreLabel(genre);
    const watchedLabel = getWatchedLabel(watched, unseenBy, attendance);
    const ratingLabel = getRatingLabel(rating, score);

    let locationLabel = "All Locations";
    if (locationId && f.locations) {
      const matchLoc = f.locations.find((l) => String(l.id) === String(locationId));
      if (matchLoc) locationLabel = matchLoc.name;
    }

    const genresList = f.genres || [];

    bar.innerHTML = `
      <!-- Medium Dropdown -->
      <div class="filter-popover" id="popover-medium">
        <button type="button" class="filter-btn ${medium.length ? "active" : ""}" data-toggle="popover" aria-expanded="false">
          <span>${esc(mediumLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="group" aria-label="Medium filter">
          <label class="popover-item is-all">
            <input type="checkbox" data-facet="medium" data-val="" ${medium.length === 0 ? "checked" : ""}>
            <span>All Mediums</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="medium" data-val="disc" ${medium.includes("disc") ? "checked" : ""}>
            <span>Disc (Physical)</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="medium" data-val="streaming" ${medium.includes("streaming") ? "checked" : ""}>
            <span>Streaming (Digital)</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="medium" data-val="both" ${medium.includes("both") ? "checked" : ""}>
            <span>Both Disc + Streaming</span><span class="ico-pair">${icon("disc")}${icon("cloud")}</span>
          </label>
        </div>
      </div>

      <!-- Quality Dropdown -->
      <div class="filter-popover" id="popover-quality">
        <button type="button" class="filter-btn ${quality.length ? "active" : ""}" data-toggle="popover" aria-expanded="false">
          <span>${esc(qualityLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="group" aria-label="Quality filter">
          <label class="popover-item is-all">
            <input type="checkbox" data-facet="quality" data-val="" ${quality.length === 0 ? "checked" : ""}>
            <span>All Qualities</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="quality" data-val="4k" ${quality.includes("4k") ? "checked" : ""}>
            <span>4K (4K UHD &amp; Digital 4K)</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="quality" data-val="hd" ${quality.includes("hd") ? "checked" : ""}>
            <span>HD (Blu-ray &amp; Digital HD)</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="quality" data-val="sd" ${quality.includes("sd") ? "checked" : ""}>
            <span>SD (DVD, VHS &amp; Digital SD)</span>
          </label>
        </div>
      </div>

      <!-- Genre Dropdown -->
      <div class="filter-popover" id="popover-genre">
        <button type="button" class="filter-btn ${genre.length ? "active" : ""}" data-toggle="popover" aria-expanded="false">
          <span>${esc(genreLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="group" aria-label="Genre filter">
          <label class="popover-item is-all">
            <input type="checkbox" data-facet="genre" data-val="" ${genre.length === 0 ? "checked" : ""}>
            <span>All Genres</span>
          </label>
          ${genresList
            .map(
              (g) => `
            <label class="popover-item">
              <input type="checkbox" data-facet="genre" data-val="${esc(g)}" ${genre.includes(g) ? "checked" : ""}>
              <span>${esc(g)}</span>
            </label>
          `
            )
            .join("")}
        </div>
      </div>

      <!-- Watched Dropdown -->
      <div class="filter-popover" id="popover-watched">
        <button type="button" class="filter-btn ${watched || whoActive ? "active" : ""}" data-toggle="popover" aria-expanded="false">
          <span>${esc(watchedLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="group" aria-label="Watched filter">
          <label class="popover-item is-all">
            <input type="checkbox" data-facet="watched" data-val="" ${!watched ? "checked" : ""}>
            <span>All Movies</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="watched" data-val="watched" ${watched === "watched" ? "checked" : ""}>
            <span>Watched (Seen)</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="watched" data-val="unwatched" ${watched === "unwatched" ? "checked" : ""}>
            <span>Unwatched (Never seen)</span>
          </label>
          ${showWho ? `
          <div class="popover-heading">Unseen by</div>
          ${[["everyone", "Everyone"], ["adults", "Adults"], ["kids", "Kids"], ...activePeople().map((p) => [String(p.id), p.name])].map(([v, label]) => `
          <label class="popover-item popover-item-who">
            <input type="checkbox" data-facet="unseen_by" data-val="${esc(v)}" ${unseenBy === v ? "checked" : ""}>
            <span>${esc(label)}</span>
          </label>`).join("")}
          <div class="popover-heading">Old watches</div>
          <label class="popover-item popover-item-who">
            <input type="checkbox" data-facet="attendance" data-val="unknown" ${attendance === "unknown" ? "checked" : ""}>
            <span>Who watched: unknown</span>
          </label>` : ""}
        </div>
      </div>

      <!-- Rating Dropdown -->
      <div class="filter-popover" id="popover-rating">
        <button type="button" class="filter-btn ${rating || score ? "active" : ""}" data-toggle="popover" aria-expanded="false">
          <span>${esc(ratingLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="group" aria-label="Rating filter">
          <label class="popover-item is-all">
            <input type="checkbox" data-facet="rating" data-val="" ${!rating && !score ? "checked" : ""}>
            <span>All Ratings</span>
          </label>
          ${
            score
              ? `
          <label class="popover-item">
            <input type="checkbox" data-facet="score" data-val="${esc(score)}" checked>
            <span>Exactly ★ ${esc(Number(score).toFixed(1))}</span>
          </label>`
              : ""
          }
          <label class="popover-item">
            <input type="checkbox" data-facet="rating" data-val="8+" ${rating === "8+" || rating === "8" ? "checked" : ""}>
            <span>Rated 8.0+ ★</span>
          </label>
          <label class="popover-item">
            <input type="checkbox" data-facet="rating" data-val="unrated" ${rating === "unrated" ? "checked" : ""}>
            <span>Unrated</span>
          </label>
        </div>
      </div>

      <!-- Location Dropdown -->
      ${
        f.locations && f.locations.length
          ? `
      <div class="filter-popover" id="popover-location">
        <button type="button" class="filter-btn ${locationId ? "active" : ""}" data-toggle="popover" aria-expanded="false">
          ${icon("pin")}<span>${esc(locationLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="group" aria-label="Location filter">
          <label class="popover-item is-all">
            <input type="checkbox" data-facet="location" data-val="" ${!locationId ? "checked" : ""}>
            <span>All Locations</span>
          </label>
          ${f.locations
            .map(
              (loc) => `
            <label class="popover-item" style="padding-left:calc(var(--s3) + ${loc.depth * 12}px)">
              <input type="checkbox" data-facet="location" data-val="${loc.id}" ${String(locationId) === String(loc.id) ? "checked" : ""}>
              <span>${esc(loc.name)} <span style="color:var(--ink-3);font-size:var(--fs-xs)">(${loc.copy_count})</span></span>
            </label>
          `
            )
            .join("")}
        </div>
      </div>
      `
          : ""
      }

      ${codeActive ? `<button type="button" class="filter-btn active" id="filter-code-btn">Unredeemed codes ✕</button>` : ""}
      ${hasFilters ? `<button type="button" class="filter-reset-btn" id="filter-reset-btn">✕ Reset</button>` : ""}
    `;

    // Restore open popover if active
    if (state.openPopoverId) {
      const p = bar.querySelector(`#${state.openPopoverId}`);
      if (p) {
        p.classList.add("open");
        p.querySelector(".filter-btn")?.setAttribute("aria-expanded", "true");
      }
    }

    // Dropdown toggle buttons
    bar.querySelectorAll('[data-toggle="popover"]').forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const popover = btn.closest(".filter-popover");
        const isOpen = popover.classList.contains("open");
        closeFilterPopovers();
        if (!isOpen) {
          popover.classList.add("open");
          btn.setAttribute("aria-expanded", "true");
          state.openPopoverId = popover.id;
        }
      });
    });

    // Checkbox changes
    bar.querySelectorAll('.popover-item input[type="checkbox"]').forEach((input) => {
      input.addEventListener("change", (e) => {
        e.stopPropagation();
        const popover = input.closest(".filter-popover");
        state.openPopoverId = popover?.id || null;
        const facet = input.dataset.facet;
        const val = input.dataset.val;
        const checked = input.checked;
        const q = new URLSearchParams(state.route.query);

        if (facet === "medium") {
          let curr = [...(state.grid.medium || [])];
          if (!val) {
            curr = [];
          } else if (val === "both") {
            curr = checked ? ["both"] : [];
          } else {
            curr = curr.filter((x) => x !== "both");
            if (checked) {
              if (!curr.includes(val)) curr.push(val);
            } else {
              curr = curr.filter((x) => x !== val);
            }
          }
          q.delete("kind");
          q.delete("both");
          if (curr.length) {
            q.set("medium", curr.join(","));
          } else {
            q.delete("medium");
          }
        } else if (facet === "quality") {
          let curr = [...(state.grid.quality || [])];
          if (!val) {
            curr = [];
          } else {
            if (checked) {
              if (!curr.includes(val)) curr.push(val);
            } else {
              curr = curr.filter((x) => x !== val);
            }
          }
          q.delete("format");
          if (curr.length) {
            q.set("quality", curr.join(","));
          } else {
            q.delete("quality");
          }
        } else if (facet === "genre") {
          let curr = [...(state.grid.genre || [])];
          if (!val) {
            curr = [];
          } else {
            if (checked) {
              if (!curr.includes(val)) curr.push(val);
            } else {
              curr = curr.filter((x) => x !== val);
            }
          }
          if (curr.length) {
            q.set("genre", curr.join(","));
          } else {
            q.delete("genre");
          }
        } else if (facet === "watched") {
          if (!val || (val === state.grid.watched && !checked)) {
            q.delete("watched");
          } else {
            q.set("watched", val);
          }
        } else if (facet === "unseen_by" || facet === "attendance") {
          const key = facet;
          if (!val || (val === state.grid[key] && !checked)) q.delete(key);
          else q.set(key, val);
        } else if (facet === "score") {
          q.delete("score");   // only ever shown checked, so a tap clears it
        } else if (facet === "rating") {
          q.delete("score");   // the 8.0+ / Unrated choices replace an exact score
          if (!val || (val === state.grid.rating && !checked)) {
            q.delete("rating");
          } else {
            q.set("rating", val);
          }
        } else if (facet === "location") {
          if (!val || (String(val) === String(state.grid.location_id) && !checked)) {
            q.delete("location_id");
            q.delete("location");
          } else {
            q.set("location_id", val);
            q.delete("location");
          }
        }

        const qStr = q.toString();
        location.hash = qStr ? `#/?${qStr}` : "#/";
      });
    });

    // The active "Unredeemed codes" chip clears just that filter
    document.getElementById("filter-code-btn")?.addEventListener("click", () => {
      const q = new URLSearchParams(state.route.query);
      q.delete("code");
      const qStr = q.toString();
      location.hash = qStr ? `#/?${qStr}` : "#/";
    });

    // Reset button
    const resetBtn = document.getElementById("filter-reset-btn");
    resetBtn?.addEventListener("click", () => {
      state.openPopoverId = null;
      const q = new URLSearchParams(state.route.query);
      q.delete("medium");
      q.delete("quality");
      q.delete("genre");
      q.delete("rating");
      q.delete("score");
      q.delete("watched");
      q.delete("unseen_by");
      q.delete("attendance");
      q.delete("code");
      q.delete("kind");
      q.delete("both");
      q.delete("format");
      q.delete("service");
      q.delete("location_id");
      q.delete("location");
      const qStr = q.toString();
      location.hash = qStr ? `#/?${qStr}` : "#/";
    });
  }

  function renderGridContent() {
    const el = document.getElementById("grid-container");
    if (!el) return;

    const titles = state.grid.titles || [];

    if (titles.length === 0) {
      const q = state.grid.q;
      const hasFilters =
        (state.grid.medium && state.grid.medium.length > 0) ||
        (state.grid.quality && state.grid.quality.length > 0) ||
        (state.grid.genre && state.grid.genre.length > 0) ||
        Boolean(state.grid.rating) ||
        Boolean(state.grid.score) ||
        Boolean(state.grid.watched) ||
        Boolean(state.grid.unseen_by) ||
        Boolean(state.grid.attendance) ||
        Boolean(state.grid.code) ||
        state.grid.kind ||
        state.grid.both ||
        state.grid.format ||
        state.grid.service;

      if (q) {
        el.innerHTML = `
          <div class="empty-state">
            <h2>No matches</h2>
            <p>Nothing in the collection matches &#8216;${esc(q)}&#8217;.</p>
            ${
              isAdmin
                ? `<a href="#/add?q=${encodeURIComponent(q)}" class="btn">Search TMDB to add it</a>`
                : ""
            }
            ${
              isAdmin && state.me && state.me.ask
                ? `<a href="#/ask" class="btn btn-ghost" id="btn-ask-instead">Ask instead →</a>`
                : ""
            }
          </div>
        `;
        document.getElementById("btn-ask-instead")?.addEventListener("click", () => {
          state.ask.draft = q; // prefilled, not sent
        });
      } else if (hasFilters) {
        el.innerHTML = `
          <div class="empty-state">
            <h2>No movies match</h2>
            <p>No owned movies match the active filters.</p>
            <a class="btn btn-ghost" href="#/">Clear all filters</a>
          </div>
        `;
      } else {
        el.innerHTML = `
          <div class="empty-state">
            <h2>Collection is empty</h2>
            <p>${
              isAdmin
                ? "You haven't added any movies to your collection yet."
                : "Nothing has been added to the collection yet."
            }</p>
            ${isAdmin ? `<a href="#/add" class="btn">Add your first movie</a>` : ""}
          </div>
        `;
      }
      return;
    }

    el.innerHTML = `
      <div class="grid">
        ${titles
          .map((t) => {
            const posterSrc = t.poster_url || (t.poster_file ? `/images/${esc(t.poster_file)}` : null);
            const posterHtml = posterSrc
              ? `<img class="poster-img" src="${esc(posterSrc)}" loading="lazy" alt="${esc(t.title)}" data-poster-fallback="${esc(t.title)}" data-poster-year="${esc(t.year || "")}">`
              : `<div class="poster-ph"><span>${esc(t.title)}<br><small style="font-weight:400;color:var(--ink-3)">${t.year || ""}</small></span></div>`;

            const mediumLabel = t.has_disc && t.has_digital ? "Disc + Digital" : t.has_disc ? "On disc" : t.has_digital ? "Digital" : "";
            const mediumBadge = mediumLabel
              ? `<span class="badge-medium" title="${mediumLabel}" aria-label="${mediumLabel}">${t.has_disc ? icon("disc") : ""}${t.has_digital ? icon("cloud") : ""}</span>`
              : "";
            const ratingBadge = (t.user_rating !== null && t.user_rating !== undefined)
              ? `<span class="badge-rating" title="Personal Rating: ${Number(t.user_rating).toFixed(1)}">★ ${Number(t.user_rating).toFixed(1)}</span>`
              : "";
            const chipsHtml = (t.formats || [])
              .map((fmt) => `<span class="chip ${getChipClass(fmt)}">${getChipIcon(fmt)}${esc(fmt)}</span>`)
              .join("");

            return `
              <a href="#/t/${t.id}" class="movie-card" data-id="${t.id}">
                <div class="poster-wrap">
                  ${mediumBadge}
                  ${ratingBadge}
                  ${posterHtml}
                  ${isAdmin ? playButtonHtml(t) : ""}
                </div>
                <div class="card-title">${esc(t.title)}</div>
                <div class="card-year">${t.year || ""}</div>
                <div class="chips">${chipsHtml}</div>
              </a>
            `;
          })
          .join("")}
      </div>
    `;
  }

export { hashWithoutEditorFilters, renderGrid };
