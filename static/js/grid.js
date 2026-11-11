// Matinee — poster grid, search and filters (7.2).
import { api, esc, getChipClass, isAdmin, state, toast } from "./core.js";
import { activePeople } from "./people.js";
import { getChipIcon, icon } from "./icons.js";

  // The editor-only people filters (spec 2026-11-11-per-person §6.4): /api answers 400 for either one.
  const PEOPLE_FILTERS = ["unseen_by", "attendance"];

  function hasPeopleFilter(query) {
    return PEOPLE_FILTERS.some((k) => query.has(k));
  }

  // A location hash ("#/?unseen_by=kids&sort=year") without the people filters ("#/?sort=year").
  function hashWithoutPeopleFilters(hash) {
    const raw = String(hash || "").replace(/^#/, "");
    const [path, query] = raw.split("?");
    const q = new URLSearchParams(query || "");
    PEOPLE_FILTERS.forEach((k) => q.delete(k));
    const qStr = q.toString();
    return `#${path || "/"}${qStr ? `?${qStr}` : ""}`;
  }

  // Drop the people filters from the current route and the address bar, without a new history entry or a
  // hashchange (the caller renders straight away).
  function dropPeopleFilters() {
    const hash = hashWithoutPeopleFilters(location.hash || "#/");
    history.replaceState(history.state, "", hash);
    PEOPLE_FILTERS.forEach((k) => state.route.query.delete(k));
    state.grid.lastHash = hash;
  }

  // ================================================================= 7.2 Poster Grid
  async function renderGrid(container, { retried = false } = {}) {
    // A visitor cannot use the people filters (an editor's shared link, or Public View from a filtered grid):
    // drop them rather than let /api answer 400 with no way back.
    if (!isAdmin && hasPeopleFilter(state.route.query)) dropPeopleFilters();
    const q = state.route.query.get("q") || "";
    const mediumStr = state.route.query.get("medium") || "";
    const qualityStr = state.route.query.get("quality") || "";
    const genreStr = state.route.query.get("genre") || "";
    const rating = state.route.query.get("rating") || "";
    const watched = state.route.query.get("watched") || "";
    const kind = state.route.query.get("kind") || "";
    const both = state.route.query.get("both") || "";
    const format = state.route.query.get("format") || "";
    const service = state.route.query.get("service") || "";
    const sort = state.route.query.get("sort") || "title";
    const locationId = (state.route.query.get("location_id") || state.route.query.get("location") || "").trim();

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

    state.grid.q = q;
    state.grid.medium = medium;
    state.grid.quality = quality;
    state.grid.genre = genre;
    state.grid.rating = rating;
    state.grid.watched = watched;
    state.grid.unseen_by = state.route.query.get("unseen_by") || "";
    state.grid.attendance = state.route.query.get("attendance") || "";
    state.grid.location_id = locationId;
    state.grid.kind = kind;
    state.grid.both = both;
    state.grid.format = format;
    state.grid.service = service;
    state.grid.sort = sort;

    container.innerHTML = `
      <div class="toolbar">
        <div class="search-wrap">
          <span class="search-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
          </span>
          <input type="text" id="grid-search" class="search-input" placeholder="Search collection…" value="${esc(q)}" autocomplete="off" autocorrect="off">
          ${q ? `<button type="button" id="grid-clear-search" class="clear-btn" aria-label="Clear search">✕</button>` : ""}
        </div>

        <div class="filter-row">
          <div class="filter-bar" id="filter-bar">
            <!-- Populated from facets -->
          </div>
          <div>
            <select id="grid-sort" class="sort-select" aria-label="Sort by">
              <option value="title" ${sort === "title" ? "selected" : ""}>Title (A–Z)</option>
              <option value="added" ${sort === "added" ? "selected" : ""}>Recently Added</option>
              <option value="year" ${sort === "year" ? "selected" : ""}>Release Year</option>
              <option value="rating" ${sort === "rating" ? "selected" : ""}>Highest Rated</option>
              <option value="unrated" ${sort === "unrated" ? "selected" : ""}>Unrated</option>
              <option value="last_watched" ${sort === "last_watched" ? "selected" : ""}>Last Watched</option>
              <option value="least_watched" ${sort === "least_watched" ? "selected" : ""}>Least Recently / Never Watched</option>
            </select>
          </div>
        </div>
      </div>

      <div id="grid-container">
        <div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading movies…</div>
      </div>
    `;

    // Bind search and sort events
    const searchInput = document.getElementById("grid-search");
    const clearBtn = document.getElementById("grid-clear-search");
    const sortSelect = document.getElementById("grid-sort");

    let searchTimeout;
    searchInput?.addEventListener("input", (e) => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        applyGridParam("q", e.target.value.trim());
      }, 350);
    });

    clearBtn?.addEventListener("click", () => {
      applyGridParam("q", "");
    });

    sortSelect?.addEventListener("change", (e) => {
      applyGridParam("sort", e.target.value);
    });

    // Load facets and titles in parallel
    const apiPrefix = isAdmin ? "/admin/api" : "/api";
    try {
      const [facetsData, titlesData] = await Promise.all([
        api(`${apiPrefix}/facets`),
        api(`${apiPrefix}/titles?${state.route.query.toString()}`),
      ]);

      if (isAdmin && state.peopleReady) await state.peopleReady;   // the Watched popover names people
      state.grid.facets = facetsData;
      state.grid.titles = titlesData.titles;

      renderFilterBar();
      renderGridContent();

      // Restore scroll position if returning from a detail view
      if (state.grid.scrollY > 0) {
        window.scrollTo({ top: state.grid.scrollY, behavior: "instant" });
      }
    } catch (err) {
      // An editor's people filter the server rejects (a deleted person, or nobody set up): retry once without it.
      if (isAdmin && !retried && err.status === 400 && hasPeopleFilter(state.route.query)) {
        dropPeopleFilters();
        toast(err.message || "That people filter no longer applies.");
        await renderGrid(container, { retried: true });
        return;
      }
      const gridContainer = document.getElementById("grid-container");
      if (gridContainer) {
        gridContainer.innerHTML = `
          <div class="empty-state">
            <h2>Unable to load collection</h2>
            <p>${esc(err.message || "Failed to connect to Matinee server")}</p>
            <button class="btn" onclick="location.reload()">Retry</button>
          </div>
        `;
      }
    }
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

  function getRatingLabel(rating) {
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
    const watched = state.grid.watched || "";
    const unseenBy = state.grid.unseen_by || "";
    const attendance = state.grid.attendance || "";
    const showWho = isAdmin && activePeople().length > 0;   // editor-only: both filters need people
    const whoActive = Boolean(unseenBy) || Boolean(attendance);
    const locationId = state.grid.location_id || "";

    const hasFilters =
      whoActive ||
      medium.length > 0 ||
      quality.length > 0 ||
      genre.length > 0 ||
      Boolean(rating) ||
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
    const ratingLabel = getRatingLabel(rating);

    let locationLabel = "All Locations";
    if (locationId && f.locations) {
      const matchLoc = f.locations.find((l) => String(l.id) === String(locationId));
      if (matchLoc) locationLabel = matchLoc.name;
    }

    const genresList = f.genres || [];

    bar.innerHTML = `
      <!-- Medium Dropdown -->
      <div class="filter-popover" id="popover-medium">
        <button type="button" class="filter-btn ${medium.length ? "active" : ""}" data-toggle="popover" aria-expanded="false" aria-haspopup="true">
          <span>${esc(mediumLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="menu">
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
        <button type="button" class="filter-btn ${quality.length ? "active" : ""}" data-toggle="popover" aria-expanded="false" aria-haspopup="true">
          <span>${esc(qualityLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="menu">
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
        <button type="button" class="filter-btn ${genre.length ? "active" : ""}" data-toggle="popover" aria-expanded="false" aria-haspopup="true">
          <span>${esc(genreLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="menu">
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
        <button type="button" class="filter-btn ${watched || whoActive ? "active" : ""}" data-toggle="popover" aria-expanded="false" aria-haspopup="true">
          <span>${esc(watchedLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="menu">
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
        <button type="button" class="filter-btn ${rating ? "active" : ""}" data-toggle="popover" aria-expanded="false" aria-haspopup="true">
          <span>${esc(ratingLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="menu">
          <label class="popover-item is-all">
            <input type="checkbox" data-facet="rating" data-val="" ${!rating ? "checked" : ""}>
            <span>All Ratings</span>
          </label>
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
        <button type="button" class="filter-btn ${locationId ? "active" : ""}" data-toggle="popover" aria-expanded="false" aria-haspopup="true">
          ${icon("pin")}<span>${esc(locationLabel)}</span>
          <span class="caret">▾</span>
        </button>
        <div class="popover-menu" role="menu">
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
        document.querySelectorAll(".filter-popover.open").forEach((p) => {
          p.classList.remove("open");
          p.querySelector(".filter-btn")?.setAttribute("aria-expanded", "false");
        });
        if (!isOpen) {
          popover.classList.add("open");
          btn.setAttribute("aria-expanded", "true");
          state.openPopoverId = popover.id;
        } else {
          state.openPopoverId = null;
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
        } else if (facet === "rating") {
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

    // Reset button
    const resetBtn = document.getElementById("filter-reset-btn");
    resetBtn?.addEventListener("click", () => {
      state.openPopoverId = null;
      const q = new URLSearchParams(state.route.query);
      q.delete("medium");
      q.delete("quality");
      q.delete("genre");
      q.delete("rating");
      q.delete("watched");
      q.delete("unseen_by");
      q.delete("attendance");
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
        Boolean(state.grid.watched) ||
        Boolean(state.grid.unseen_by) ||
        Boolean(state.grid.attendance) ||
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
            <button class="btn btn-ghost" onclick="location.hash='#/'">Clear all filters</button>
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
              ? `<img class="poster-img" src="${posterSrc}" loading="lazy" alt="${esc(t.title)}" onerror="this.onerror=null;this.parentElement.innerHTML='<div class=\\'poster-ph\\'>${esc(t.title)}<br>${t.year || ''}</div>';">`
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

export { hashWithoutPeopleFilters, renderGrid };
