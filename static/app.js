/**
 * Matinee — phone-first family movie collection browser & catalog manager.
 * Vanilla JS with hash routing: #/, #/t/<id>, #/add, #/about.
 */
(() => {
  "use strict";

  // ================================================================= Constants
  const FORMATS = {
    physical: ["4K UHD", "Blu-ray", "3D Blu-ray", "DVD", "VHS"],
    digital: ["Digital 4K", "Digital HD", "Digital SD"],
  };

  const SERVICES = [
    "Movies Anywhere",
    "Apple TV",
    "Fandango at Home",
    "Amazon",
    "Google Play",
    "Plex/Jellyfin",
    "Other",
  ];

  const TMDB_IMG_THUMB = "https://image.tmdb.org/t/p/w154";

  // Check if current URL path is within the admin section
  const isAdmin = location.pathname.startsWith("/admin");

  // ================================================================= State
  const state = {
    route: { path: "/", params: {}, query: new URLSearchParams() },
    grid: {
      q: "",
      medium: [],
      quality: [],
      genre: [],
      rating: "",
      watched: "",
      kind: "",
      format: "",
      service: "",
      both: "",
      sort: "title",
      scrollY: 0,
      lastHash: "#/",
      titles: null,
      facets: null,
      loaded: false,
    },
    add: {
      q: "",
      results: [],
      error: null,
      loading: false,
      selectedMovie: null,
      ownedInfo: null,
    },
  };

  // ================================================================= Helpers
  function esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  const escapeHtml = esc;

  let toastTimer = null;
  function toast(msg, duration = 4000) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.remove("show");
    }, duration);
  }

  async function api(path, opts = {}) {
    const headers = { Accept: "application/json", ...(opts.headers || {}) };
    if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, { ...opts, headers });

    if (res.status === 401) {
      if (isAdmin) {
        toast("Your sign-in expired. Reload the page to sign in again.");
      } else {
        toast("Sign in to edit");
      }
      const err = new Error("Sign in to edit");
      err.status = 401;
      throw err;
    }

    if (!res.ok) {
      let data = {};
      try {
        data = await res.json();
      } catch {
        // non-json response
      }
      const err = new Error(data.error || res.statusText || "Request failed");
      err.status = res.status;
      err.reason = data.reason;
      throw err;
    }

    return await res.json();
  }

  function formatRuntime(minutes) {
    if (!minutes || minutes <= 0) return "";
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (h > 0 && m > 0) return `${h}h ${m}m`;
    if (h > 0) return `${h}h`;
    return `${m}m`;
  }

  function getChipClass(format) {
    if (format === "4K UHD") return "k-4k-uhd";
    if (format === "Digital 4K") return "k-digital-4k";
    if (format === "3D Blu-ray") return "k-3d-bluray";
    if (format === "Blu-ray") return "k-bluray";
    if (format === "DVD") return "k-dvd";
    if (format === "VHS") return "k-vhs";
    if (format === "Digital HD") return "k-digital-hd";
    if (format === "Digital SD") return "k-digital-sd";
    return "k-dvd";
  }

  function getChipIcon(format) {
    if (format === "4K UHD" || format === "Blu-ray" || format === "3D Blu-ray" || format === "DVD" || format === "VHS") {
      return "💿 ";
    }
    if (format === "Digital 4K" || format === "Digital HD" || format === "Digital SD") {
      return "☁️ ";
    }
    return "";
  }

  function formatCopyLabel(copy) {
    if (copy.kind === "digital") {
      return `${copy.format}${copy.service ? " · " + copy.service : ""}`;
    }
    return copy.format;
  }

  const BUCKET_LABELS = {
    past_year: "Within the Past Year",
    "1_2_years": "1–2 Years Ago",
    "2_5_years": "2–5 Years Ago",
    "5_10_years": "5–10 Years Ago",
    over_10_years: "Over 10 Years Ago",
    unknown: "Date Unknown",
  };

  function formatRelativeDate(isoDateStr) {
    if (!isoDateStr) return "Never";
    const parts = isoDateStr.split("-").map(Number);
    const target = new Date(parts[0], parts[1] - 1, parts[2]);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffMs = today - target;
    const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays <= 0) return "today";
    if (diffDays === 1) return "yesterday";
    if (diffDays < 7) return `${diffDays} days ago`;
    const diffWeeks = Math.floor(diffDays / 7);
    if (diffWeeks < 5) return diffWeeks === 1 ? "1 week ago" : `${diffWeeks} weeks ago`;
    const diffMonths = Math.floor(diffDays / 30.4375);
    if (diffMonths < 12) return diffMonths === 1 ? "1 month ago" : `${diffMonths} months ago`;
    const diffYears = Math.floor(diffDays / 365.25);
    return diffYears === 1 ? "1 year ago" : `${diffYears} years ago`;
  }

  function formatReadableDate(isoDateStr) {
    if (!isoDateStr) return "";
    const parts = isoDateStr.split("-").map(Number);
    const date = new Date(parts[0], parts[1] - 1, parts[2]);
    return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }

  function formatLastWatched(lastWatchedDate, watchCount = 0) {
    if (lastWatchedDate) {
      const relative = formatRelativeDate(lastWatchedDate);
      const readable = formatReadableDate(lastWatchedDate);
      return `Watched ${relative} (${readable})`;
    }
    if (watchCount > 0) {
      return "Watched (Date unknown)";
    }
    return "Never watched";
  }

  // ================================================================= Router
  function parseHash(hashStr) {
    const raw = (hashStr || "").replace(/^#/, "").trim();
    if (!raw || raw === "/") {
      return { path: "/", params: {}, query: new URLSearchParams() };
    }

    const [pathPart, queryPart] = raw.split("?");
    const query = new URLSearchParams(queryPart || "");
    const segments = pathPart.replace(/^\/+|\/+$/g, "").split("/");

    if (segments.length === 0 || segments[0] === "") {
      return { path: "/", params: {}, query };
    }
    if (segments[0] === "t" && segments[1]) {
      return { path: "/t/:id", params: { id: segments[1] }, query };
    }
    if (segments[0] === "add") {
      return { path: "/add", params: {}, query };
    }
    if (segments[0] === "about") {
      return { path: "/about", params: {}, query };
    }
    if (segments[0] === "stats") {
      return { path: "/stats", params: {}, query };
    }

    return { path: "/404", params: {}, query };
  }

  function updateHeader() {
    const brand = document.getElementById("brand-link");
    const nav = document.getElementById("nav-actions");
    if (!brand || !nav) return;

    brand.href = "#/";
    brand.innerHTML = `<span>Matinee</span>${isAdmin ? '<span class="badge-admin">Admin</span>' : ""}`;

    if (isAdmin) {
      nav.innerHTML = `
        <button type="button" class="btn btn-sm btn-ghost" id="btn-header-movie-night" title="Movie Night Picker">
          🎲 Movie Night
        </button>
        <button type="button" class="btn btn-sm btn-ghost" id="btn-header-locations" title="Manage Shelves &amp; Locations">
          📍 Locations
        </button>
        <a href="#/stats" class="btn btn-sm btn-ghost" id="btn-header-stats">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
          Stats
        </a>
        <a href="#/add" class="btn btn-sm" id="btn-header-add">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          Add Movie
        </a>
      `;
    } else {
      nav.innerHTML = `
        <button type="button" class="btn btn-sm btn-ghost" id="btn-header-movie-night" title="Movie Night Picker">
          🎲 Movie Night
        </button>
        <a href="#/stats" class="btn btn-sm btn-ghost" id="btn-header-stats">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
          Stats
        </a>
        <a href="/admin${location.hash || ""}" class="btn btn-sm btn-ghost" id="btn-header-signin">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
          Sign In
        </a>
      `;
    }
    document.getElementById("btn-header-movie-night")?.addEventListener("click", () => openMovieNightModal());
    document.getElementById("btn-header-locations")?.addEventListener("click", () => openLocationsManagerModal());

    const footerLinks = document.getElementById("footer-links");
    if (footerLinks) {
      if (isAdmin) {
        footerLinks.innerHTML = `
          <a href="#/">Collection</a> &nbsp;·&nbsp;
          <a href="#/stats">Stats</a> &nbsp;·&nbsp;
          <a href="#/about">About &amp; TMDB Attribution</a> &nbsp;·&nbsp;
          <a href="/${location.hash || ""}">Public View</a>
        `;
      } else {
        footerLinks.innerHTML = `
          <a href="#/">Collection</a> &nbsp;·&nbsp;
          <a href="#/stats">Stats</a> &nbsp;·&nbsp;
          <a href="#/about">About &amp; TMDB Attribution</a> &nbsp;·&nbsp;
          <a href="/admin${location.hash || ""}">Admin Sign In</a>
        `;
      }
    }
  }

  function navigate(hash) {
    location.hash = hash;
  }

  async function handleRoute() {
    closeModal();
    stopBarcodeScanner();
    const prevRoute = state.route;
    state.route = parseHash(location.hash);
    updateHeader();

    const view = document.getElementById("view");
    if (!view) return;

    if (state.route.path === "/") {
      state.grid.lastHash = location.hash || "#/";
      await renderGrid(view);
    } else if (state.route.path === "/t/:id") {
      // Save scroll position and active grid hash before leaving grid
      if (prevRoute.path === "/") {
        state.grid.scrollY = window.scrollY;
        const qStr = prevRoute.query.toString();
        state.grid.lastHash = qStr ? `#/?${qStr}` : "#/";
      }
      await renderDetail(view, state.route.params.id);
    } else if (state.route.path === "/add") {
      if (prevRoute.path === "/") {
        state.grid.scrollY = window.scrollY;
      }
      await renderAdd(view);
    } else if (state.route.path === "/stats") {
      if (prevRoute.path === "/") {
        state.grid.scrollY = window.scrollY;
      }
      await renderStats(view);
    } else if (state.route.path === "/about") {
      if (prevRoute.path === "/") {
        state.grid.scrollY = window.scrollY;
      }
      renderAbout(view);
    } else {
      renderNotFound(view);
    }
  }

  // ================================================================= 7.2 Poster Grid
  async function renderGrid(container) {
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

      state.grid.facets = facetsData;
      state.grid.titles = titlesData.titles;

      renderFilterBar();
      renderGridContent();

      // Restore scroll position if returning from a detail view
      if (state.grid.scrollY > 0) {
        window.scrollTo({ top: state.grid.scrollY, behavior: "instant" });
      }
    } catch (err) {
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
    if (medium.includes("both")) return "Medium: Disc + Stream 💿☁️";
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

  function getWatchedLabel(watched) {
    if (!watched) return "Watched: All";
    if (watched === "watched") return "Watched: Seen";
    if (watched === "unwatched") return "Watched: Unseen";
    return `Watched: ${watched}`;
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
    const locationId = state.grid.location_id || "";

    const hasFilters =
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
    const watchedLabel = getWatchedLabel(watched);
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
            <span>Both Disc + Streaming 💿☁️</span>
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
        <button type="button" class="filter-btn ${watched ? "active" : ""}" data-toggle="popover" aria-expanded="false" aria-haspopup="true">
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
          <span>📍 ${esc(locationLabel)}</span>
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
          </div>
        `;
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

            const bothBadge = t.has_both ? `<span class="badge-both" title="Disc + Digital">💿☁️</span>` : "";
            const ratingBadge = (t.user_rating !== null && t.user_rating !== undefined)
              ? `<span class="badge-rating" title="Personal Rating: ${Number(t.user_rating).toFixed(1)}">★ ${Number(t.user_rating).toFixed(1)}</span>`
              : "";
            const chipsHtml = (t.formats || [])
              .map((fmt) => `<span class="chip ${getChipClass(fmt)}">${getChipIcon(fmt)}${esc(fmt)}</span>`)
              .join("");

            return `
              <a href="#/t/${t.id}" class="movie-card" data-id="${t.id}">
                <div class="poster-wrap">
                  ${bothBadge}
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

  // ================================================================= 7.3 Detail Page
  async function renderDetail(container, titleId) {
    window.scrollTo({ top: 0, behavior: "instant" });
    container.innerHTML = `
      <div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading movie details…</div>
    `;

    const apiPrefix = isAdmin ? "/admin/api" : "/api";
    let title;
    try {
      title = await api(`${apiPrefix}/titles/${titleId}`);
    } catch (err) {
      if (err.status === 404) {
        container.innerHTML = `
          <div class="empty-state">
            <h2>Movie not found</h2>
            <p>This movie does not exist in the collection.</p>
            <a href="#/" class="btn">Back to collection</a>
          </div>
        `;
      } else {
        container.innerHTML = `
          <div class="empty-state">
            <h2>Unable to load movie</h2>
            <p>${esc(err.message)}</p>
            <a href="#/" class="btn btn-ghost">Back to collection</a>
          </div>
        `;
      }
      return;
    }

    const runtimeStr = formatRuntime(title.runtime);
    const metaParts = [];
    if (title.year) metaParts.push(esc(title.year));
    if (runtimeStr) metaParts.push(esc(runtimeStr));
    if (title.certification) {
      metaParts.push(`<span class="cert-badge">${esc(title.certification)}</span>`);
    }
    if (title.tmdb_vote && title.tmdb_vote > 0) {
      metaParts.push(`<span class="score">★ ${title.tmdb_vote.toFixed(1)}</span>`);
    }

    const heroBgSrc = title.backdrop_url || (title.backdrop_file ? `/images/${esc(title.backdrop_file)}` : null);
    const heroBgStyle = heroBgSrc
      ? `style="background-image: url('${heroBgSrc}');"`
      : "";

    const posterDetailSrc = title.poster_large_url || title.poster_url || (title.poster_file ? `/images/${esc(title.poster_file)}` : null);
    const posterHtml = posterDetailSrc
      ? `<img src="${posterDetailSrc}" alt="${esc(title.title)}" onerror="this.onerror=null;this.parentElement.innerHTML='<div class=\\'poster-ph\\'>${esc(title.title)}</div>';">`
      : `<div class="poster-ph" style="height:100%">${esc(title.title)}</div>`;

    const copies = title.copies || [];

    // Ensure we have active titles list for adjacent navigation
    if (!state.grid.titles || !state.grid.titles.length) {
      try {
        const titlesData = await api(`${apiPrefix}/titles?sort=title`);
        state.grid.titles = titlesData.titles || [];
      } catch (e) {
        state.grid.titles = [];
      }
    }

    const titlesList = state.grid.titles || [];
    const currentId = Number(titleId);
    const currentIndex = titlesList.findIndex((t) => t.id === currentId);
    const prevMovie = currentIndex > 0 ? titlesList[currentIndex - 1] : null;
    const nextMovie = currentIndex >= 0 && currentIndex < titlesList.length - 1 ? titlesList[currentIndex + 1] : null;

    container.innerHTML = `
      <div class="detail-view">
        <div class="detail-nav-bar">
          <a href="${state.grid.lastHash || "#/"}" class="back-link" id="btn-back">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            Back to collection
          </a>
          <div class="detail-adjacent-nav">
            <a href="${prevMovie ? `#/t/${prevMovie.id}` : "#"}"
               class="nav-arrow-btn ${!prevMovie ? "disabled" : ""}"
               id="btn-prev-movie"
               title="${prevMovie ? `Previous: ${esc(prevMovie.title)} (←)` : "No previous movie"}"
               aria-label="Previous movie"
               ${!prevMovie ? 'aria-disabled="true" tabindex="-1"' : ""}>
              ‹ Prev
            </a>
            <a href="${nextMovie ? `#/t/${nextMovie.id}` : "#"}"
               class="nav-arrow-btn ${!nextMovie ? "disabled" : ""}"
               id="btn-next-movie"
               title="${nextMovie ? `Next: ${esc(nextMovie.title)} (→)` : "No next movie"}"
               aria-label="Next movie"
               ${!nextMovie ? 'aria-disabled="true" tabindex="-1"' : ""}>
              Next ›
            </a>
          </div>
        </div>

        <section class="hero">
          <div class="hero-bg ${title.backdrop_file ? "" : "fallback"}" ${heroBgStyle}></div>
          <div class="hero-body">
            <div class="hero-poster">
              ${posterHtml}
            </div>
            <div class="hero-info">
              <h1 class="hero-title">${esc(title.title)}</h1>
              <div class="meta-line">
                ${metaParts.join(' <span style="opacity:0.4">·</span> ')}
              </div>
              ${
                title.collection_name
                  ? `<div style="font-size:var(--fs-sm);color:var(--amber);font-weight:600">${esc(title.collection_name)}</div>`
                  : ""
              }
              ${
                title.genres && title.genres.length > 0
                  ? `<div class="chips" style="margin-top:var(--s1)">
                      ${title.genres.map((g) => `<span class="chip" style="background:var(--raised);color:var(--ink-2)">${esc(g)}</span>`).join("")}
                    </div>`
                  : ""
              }
              ${
                isAdmin
                  ? `
                <div class="rating-widget" id="rating-widget">
                  <div class="rating-header">
                    <span class="rating-label">Personal Rating</span>
                    <span class="rating-score-display" id="rating-display">
                      ${title.user_rating !== null && title.user_rating !== undefined ? `★ ${Number(title.user_rating).toFixed(1)} <small style="font-size:var(--fs-xs);font-weight:400;color:var(--ink-2)">/ 10</small>` : "Unrated"}
                    </span>
                  </div>
                  <div class="rating-controls">
                    <button type="button" class="btn btn-sm btn-ghost rating-step-btn" id="btn-rating-minus" aria-label="Decrease rating by 0.5">−</button>
                    <input type="range" class="rating-slider" id="rating-slider" min="0" max="10" step="0.5" value="${title.user_rating !== null && title.user_rating !== undefined ? title.user_rating : 7.0}">
                    <button type="button" class="btn btn-sm btn-ghost rating-step-btn" id="btn-rating-plus" aria-label="Increase rating by 0.5">+</button>
                    <button type="button" class="btn btn-sm btn-ghost" id="btn-rating-clear" ${title.user_rating === null || title.user_rating === undefined ? "disabled" : ""}>Clear</button>
                  </div>
                </div>
              `
                  : title.user_rating !== null && title.user_rating !== undefined
                  ? `
                <div class="rating-widget-public">
                  <span class="rating-label">Personal Rating:</span>
                  <span class="badge-rating-detail">★ ${Number(title.user_rating).toFixed(1)} / 10</span>
                </div>
              `
                  : ""
              }
            </div>
          </div>
        </section>

        ${
          title.overview
            ? `
          <section class="detail-section">
            <h3>Overview</h3>
            <p class="overview-text">${esc(title.overview)}</p>
          </section>
        `
            : ""
        }

        <section class="detail-section watch-section">
          <div class="watch-section-header">
            <div>
              <h3 style="margin-bottom:2px">Watch History</h3>
              <div class="last-watched-tag" id="last-watched-tag">
                ${formatLastWatched(title.last_watched, title.watch_count)}
              </div>
            </div>
            ${
              isAdmin
                ? `
              <div class="watch-header-actions">
                <button type="button" class="btn btn-sm" id="btn-watched-today">+ Watched Today</button>
              </div>
            `
                : ""
            }
          </div>

          ${
            isAdmin
              ? `
            <div class="watch-log-form">
              <select id="select-watch-timing" class="watch-timing-select" aria-label="Select watch timing">
                <option value="exact">Exact Date</option>
                <option value="past_year">Watched Within the Past Year</option>
                <option value="1_2_years">Watched 1–2 Years Ago</option>
                <option value="2_5_years">Watched 2–5 Years Ago</option>
                <option value="5_10_years">Watched 5–10 Years Ago</option>
                <option value="over_10_years">Watched Over 10 Years Ago</option>
                <option value="unknown">Date Unknown (Historical)</option>
              </select>
              <div class="watch-log-inputs" id="wrap-watch-date">
                <input type="date" id="input-watch-date" class="watch-date-input" max="${new Date().toISOString().slice(0, 10)}">
              </div>
              <button type="button" class="btn btn-sm btn-ghost" id="btn-log-watch" style="width:100%;margin-top:var(--s1)">Log Past Watch</button>
            </div>
          `
              : ""
          }

          ${
            !title.watches || title.watches.length === 0
              ? `<div style="color:var(--ink-2);padding:var(--s2) 0">No recorded watches yet.</div>`
              : `
            <div class="watch-log-list" id="watch-log-list">
              ${title.watches
                .map(
                  (w, idx) => `
                <div class="watch-row ${idx >= 3 ? "watch-row-extra hidden" : ""}" data-watch-id="${w.id}">
                  <div class="watch-row-main" style="display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap">
                    ${
                      w.date_bucket && BUCKET_LABELS[w.date_bucket]
                        ? (w.date_bucket === "unknown"
                            ? `<span class="watch-row-date">📅 Watched (Date unknown)</span><span class="badge badge-subtle">Historical</span>`
                            : `<span class="watch-row-date">📅 Watched ${esc(BUCKET_LABELS[w.date_bucket])}</span><span class="badge badge-subtle">Estimated</span>`
                          )
                        : (w.watched_date
                            ? `<span class="watch-row-date">📅 ${formatReadableDate(w.watched_date)}</span><span class="watch-row-rel">(${formatRelativeDate(w.watched_date)})</span>`
                            : `<span class="watch-row-date">📅 Watched (Date unknown)</span><span class="badge badge-subtle">Historical</span>`
                          )
                    }
                  </div>
                  ${w.notes ? `<div class="watch-row-notes" style="width:100%;font-size:var(--fs-xs);color:var(--ink-2);margin-top:2px;padding-left:4px">${esc(w.notes)}</div>` : ""}
                  ${
                    isAdmin
                      ? `<button type="button" class="btn btn-sm btn-danger btn-delete-watch" data-id="${w.id}" title="Delete watch entry" style="margin-left:auto;padding:2px 8px;font-size:var(--fs-xs)">✕</button>`
                      : ""
                  }
                </div>
              `
                )
                .join("")}
              ${
                title.watches.length > 3
                  ? `<button type="button" class="btn btn-sm btn-ghost watch-toggle-btn" id="btn-toggle-watches">Show all ${title.watches.length} watches</button>`
                  : ""
              }
            </div>
          `
          }
        </section>

        <section class="detail-section">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--s3)">
            <h3 style="margin-bottom:0">Owned Copies (${copies.length})</h3>
            ${
              isAdmin
                ? `<button class="btn btn-sm" id="btn-add-copy">+ Add copy</button>`
                : ""
            }
          </div>

          ${
            copies.length === 0
              ? `<div style="color:var(--ink-2);padding:var(--s2) 0">Not in the collection right now.</div>`
              : `
            <div class="copies-list">
              ${copies
                .map((cp) => {
                  const label = formatCopyLabel(cp);
                  const chipCls = getChipClass(cp.format);
                  const locBadge =
                    cp.kind === "physical" && cp.location_path
                      ? `<span class="copy-location">📍 ${esc(cp.location_path)}</span>`
                      : "";
                  const addedInfo = cp.added_by
                    ? `<span class="copy-meta">Added by ${esc(cp.added_by)}</span>`
                    : "";

                  return `
                    <div class="copy-row" data-copy-id="${cp.id}">
                      <div class="copy-info">
                        <span class="chip ${chipCls}">${getChipIcon(cp.format)} ${esc(label)}</span>
                        ${locBadge}
                        ${addedInfo}
                      </div>
                      ${
                        isAdmin
                          ? `
                        <div class="copy-actions">
                          <button class="btn btn-sm btn-ghost btn-edit-copy" data-copy="${esc(JSON.stringify(cp))}">Edit</button>
                          <button class="btn btn-sm btn-danger btn-delete-copy" data-id="${cp.id}">Delete</button>
                        </div>
                      `
                          : ""
                      }
                    </div>
                  `;
                })
                .join("")}
            </div>
          `
          }

          ${
            isAdmin
              ? `
            <div class="admin-bar">
              <button class="btn btn-sm btn-ghost" id="btn-refresh-tmdb">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                Refresh from TMDB
              </button>
              <button class="btn btn-sm btn-danger" id="btn-delete-title" style="margin-left:auto">
                Delete title
              </button>
            </div>
          `
              : ""
          }
        </section>
      </div>
    `;

    // Hook up Back button to maintain scroll and active filters
    document.getElementById("btn-back")?.addEventListener("click", (e) => {
      e.preventDefault();
      location.hash = state.grid.lastHash || "#/";
    });

    // Editor controls (7.4)
    if (isAdmin) {
      document.getElementById("btn-add-copy")?.addEventListener("click", () => {
        openCopyModal({ tmdb_id: title.tmdb_id, title_id: title.id, title: title.title });
      });

      container.querySelectorAll(".btn-edit-copy").forEach((btn) => {
        btn.addEventListener("click", () => {
          const cp = JSON.parse(btn.dataset.copy);
          openEditCopyModal(cp, title);
        });
      });

      container.querySelectorAll(".btn-delete-copy").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const copyId = btn.dataset.id;
          if (!confirm("Delete this copy?")) return;
          try {
            await api(`/admin/api/copies/${copyId}`, { method: "DELETE" });
            toast("Copy deleted");
            await renderDetail(container, titleId);
          } catch (err) {
            toast(err.message || "Failed to delete copy");
          }
        });
      });

      document.getElementById("btn-refresh-tmdb")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-refresh-tmdb");
        if (btn) btn.disabled = true;
        try {
          await api(`/admin/api/titles/${titleId}/refresh`, { method: "POST" });
          toast("Refreshed from TMDB");
          await renderDetail(container, titleId);
        } catch (err) {
          toast(err.message || "Failed to refresh");
        } finally {
          if (btn) btn.disabled = false;
        }
      });

      document.getElementById("btn-delete-title")?.addEventListener("click", async () => {
        if (!confirm(`Delete "${title.title}" and all its copies from the collection?`)) return;
        try {
          await api(`/admin/api/titles/${titleId}`, { method: "DELETE" });
          toast("Title deleted");
          navigate("#/");
        } catch (err) {
          toast(err.message || "Failed to delete title");
        }
      });

      // Rating control listeners
      const slider = document.getElementById("rating-slider");
      const display = document.getElementById("rating-display");
      const clearBtn = document.getElementById("btn-rating-clear");
      const minusBtn = document.getElementById("btn-rating-minus");
      const plusBtn = document.getElementById("btn-rating-plus");

      let currentRating = title.user_rating;
      let saveTimer = null;

      const triggerSave = (val) => {
        currentRating = val;
        if (display) {
          display.innerHTML =
            val !== null && val !== undefined
              ? `★ ${Number(val).toFixed(1)} <small style="font-size:var(--fs-xs);font-weight:400;color:var(--ink-2)">/ 10</small>`
              : "Unrated";
        }
        if (clearBtn) clearBtn.disabled = val === null || val === undefined;
        if (slider && val !== null && val !== undefined) slider.value = val;

        clearTimeout(saveTimer);
        saveTimer = setTimeout(async () => {
          try {
            await api(`${apiPrefix}/titles/${titleId}/rating`, {
              method: "PUT",
              body: { rating: val },
            });
            title.user_rating = val;
            if (state.grid.titles) {
              const gt = state.grid.titles.find((t) => t.id === Number(titleId));
              if (gt) gt.user_rating = val;
            }
            toast(val !== null && val !== undefined ? `Rating saved (★ ${Number(val).toFixed(1)})` : "Rating cleared");
          } catch (e) {
            toast(e.message || "Failed to save rating");
          }
        }, 300);
      };

      slider?.addEventListener("input", (e) => {
        triggerSave(Number(e.target.value));
      });

      minusBtn?.addEventListener("click", () => {
        const base = currentRating !== null && currentRating !== undefined ? currentRating : 7.0;
        const val = Math.max(0.0, Math.round((base - 0.5) * 2) / 2);
        triggerSave(val);
      });

      plusBtn?.addEventListener("click", () => {
        const base = currentRating !== null && currentRating !== undefined ? currentRating : 7.0;
        const val = Math.min(10.0, Math.round((base + 0.5) * 2) / 2);
        triggerSave(val);
      });

      clearBtn?.addEventListener("click", () => {
        triggerSave(null);
      });

      // Watch history listeners
      const selectTiming = document.getElementById("select-watch-timing");
      const wrapDate = document.getElementById("wrap-watch-date");
      const inputWatchDate = document.getElementById("input-watch-date");
      const btnLogWatch = document.getElementById("btn-log-watch");

      selectTiming?.addEventListener("change", () => {
        const mode = selectTiming.value;
        if (mode === "exact") {
          if (wrapDate) wrapDate.style.display = "flex";
          if (btnLogWatch) btnLogWatch.textContent = "Log Past Date";
        } else {
          if (wrapDate) wrapDate.style.display = "none";
          if (btnLogWatch) btnLogWatch.textContent = "Log Approximate Watch";
        }
      });

      document.getElementById("btn-watched-today")?.addEventListener("click", async () => {
        try {
          const todayIso = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
          await api(`${apiPrefix}/titles/${titleId}/watches`, {
            method: "POST",
            body: { watched_date: todayIso },
          });
          toast("Marked as watched today!");
          await renderDetail(container, titleId);
        } catch (e) {
          toast(e.message || "Failed to mark as watched");
        }
      });

      btnLogWatch?.addEventListener("click", async () => {
        const mode = selectTiming ? selectTiming.value : "exact";
        let payload = {};
        if (mode === "exact") {
          const val = inputWatchDate?.value?.trim();
          if (!val) {
            toast("Please choose a date");
            return;
          }
          payload = { watched_date: val };
        } else {
          payload = { date_bucket: mode };
        }
        try {
          await api(`${apiPrefix}/titles/${titleId}/watches`, {
            method: "POST",
            body: payload,
          });
          toast("Watch logged!");
          await renderDetail(container, titleId);
        } catch (e) {
          toast(e.message || "Failed to log watch");
        }
      });

      container.querySelectorAll(".btn-delete-watch").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const watchId = btn.dataset.id;
          if (!confirm("Remove this watch date?")) return;
          try {
            await api(`${apiPrefix}/titles/${titleId}/watches/${watchId}`, { method: "DELETE" });
            toast("Watch entry removed");
            await renderDetail(container, titleId);
          } catch (e) {
            toast(e.message || "Failed to delete watch entry");
          }
        });
      });
    }

    // Toggle extra watches
    const toggleWatchesBtn = document.getElementById("btn-toggle-watches");
    toggleWatchesBtn?.addEventListener("click", () => {
      const extras = container.querySelectorAll(".watch-row-extra");
      const isHidden = extras[0]?.classList.contains("hidden");
      extras.forEach((el) => el.classList.toggle("hidden", !isHidden));
      toggleWatchesBtn.textContent = isHidden ? "Show fewer" : `Show all ${title.watches.length} watches`;
    });
  }

  // ================================================================= 8.1 / 8.2 Add Flow & Barcode Scanner
  let scannerStream = null;
  let scannerAnimId = null;
  let isScannerPaused = false;

  function pauseBarcodeScanner() {
    isScannerPaused = true;
  }

  function resumeBarcodeScanner() {
    isScannerPaused = false;
  }

  function playBeep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      gain.gain.setValueAtTime(0.2, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.15);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.15);
    } catch (e) {}
  }

  function getBatchQueue() {
    try {
      return JSON.parse(localStorage.getItem("matinee_scan_queue") || "[]");
    } catch (e) {
      return [];
    }
  }

  function setBatchQueue(q) {
    try {
      localStorage.setItem("matinee_scan_queue", JSON.stringify(q));
    } catch (e) {}
  }

  function stopBarcodeScanner() {
    if (scannerAnimId) {
      cancelAnimationFrame(scannerAnimId);
      scannerAnimId = null;
    }
    if (scannerStream) {
      scannerStream.getTracks().forEach((track) => track.stop());
      scannerStream = null;
    }
    isScannerPaused = false;
  }

  async function startBarcodeScanner(videoEl, reticleEl, onBarcodeDetected) {
    if (!("BarcodeDetector" in window)) {
      return { supported: false };
    }
    try {
      const detector = new window.BarcodeDetector({ formats: ["upc_a", "upc_e", "ean_13"] });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      scannerStream = stream;
      videoEl.srcObject = stream;
      await videoEl.play();

      let lastDetected = null;
      let lastTime = 0;

      async function scanLoop() {
        if (!isScannerPaused && videoEl.readyState >= 2) {
          try {
            const barcodes = await detector.detect(videoEl);
            if (!isScannerPaused && barcodes.length > 0) {
              const raw = barcodes[0].rawValue;
              const now = Date.now();
              if (raw !== lastDetected || now - lastTime > 2500) {
                lastDetected = raw;
                lastTime = now;
                reticleEl.classList.add("success");
                playBeep();
                setTimeout(() => reticleEl.classList.remove("success"), 400);
                onBarcodeDetected(raw);
              }
            }
          } catch (e) {}
        }
        scannerAnimId = requestAnimationFrame(scanLoop);
      }
      scanLoop();
      return { supported: true };
    } catch (err) {
      return { supported: true, error: err.message };
    }
  }

  async function renderAdd(container) {
    // Stop any active scanner before re-rendering
    stopBarcodeScanner();

    if (!isAdmin) {
      container.innerHTML = `
        <div class="empty-state">
          <h2>Editor Sign-in Required</h2>
          <p>Adding movies requires signing in with an editor account.</p>
          <div style="display:flex;gap:var(--s2);justify-content:center;margin-top:var(--s3)">
            <a href="#/" class="btn btn-ghost">Back to collection</a>
            <a href="/admin#/add" class="btn">Sign In as Editor</a>
          </div>
        </div>
      `;
      return;
    }

    const initialQ = state.route.query.get("q") || state.add.q || "";

    container.innerHTML = `
      <div class="add-view">
        <div style="margin-bottom:var(--s4)">
          <a href="#/" class="back-link">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            Back to collection
          </a>
          <h1 style="font-size:var(--fs-xl);font-weight:800;margin-top:var(--s2)">Add Movies</h1>
        </div>

        <div class="scanner-tabs">
          <button type="button" class="scanner-tab-btn active" id="tab-search">🔍 Search Title</button>
          <button type="button" class="scanner-tab-btn" id="tab-scan">📷 Scan Barcode</button>
        </div>

        <!-- Panel 1: Search -->
        <div id="search-panel">
          <p style="color:var(--ink-2);font-size:var(--fs-sm);margin-bottom:var(--s3)">
            Search TMDB by title (e.g. "Dune" or "Dune 1984") to find and add copies to your collection.
          </p>
          <div class="search-wrap">
            <span class="search-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            </span>
            <input type="text" id="tmdb-search-input" class="search-input" placeholder="Search TMDB (at least 2 characters)…" value="${esc(initialQ)}" autocomplete="off">
            ${initialQ ? `<button type="button" id="tmdb-clear-search" class="clear-btn" aria-label="Clear">✕</button>` : ""}
          </div>

          <div id="tmdb-results-container">
            <!-- Results injected here -->
          </div>
        </div>

        <!-- Panel 2: Barcode Scanner -->
        <div id="scanner-panel" style="display:none">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--s3);flex-wrap:wrap;gap:var(--s2)">
            <div class="toggle-group" id="scan-mode-toggle">
              <button type="button" class="toggle-btn active" data-mode="single">Single Scan</button>
              <button type="button" class="toggle-btn" data-mode="batch">Batch Mode (Shelf)</button>
            </div>
            <div id="scanner-status" style="font-size:var(--fs-xs);color:var(--ink-2)">Camera ready</div>
          </div>

          <div class="scanner-viewport" id="scanner-viewport">
            <video id="scanner-video" class="scanner-video" playsinline muted autoplay></video>
            <div class="scanner-reticle" id="scanner-reticle">
              <div class="scanner-laser"></div>
            </div>
          </div>

          <div style="max-width:500px;margin:0 auto var(--s4)">
            <div style="display:flex;gap:var(--s2)">
              <input type="text" id="manual-upc-input" class="search-input" inputmode="numeric" placeholder="Enter UPC barcode number…" style="flex:1">
              <button type="button" id="manual-upc-btn" class="btn">Lookup</button>
            </div>
          </div>

          <!-- Batch Queue Area -->
          <div id="batch-area" class="batch-panel" style="display:none">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--s3)">
              <div class="batch-queue-counter">
                <span>📦</span>
                <span id="batch-count-label">0 Discs in Queue</span>
              </div>
              <div style="display:flex;gap:var(--s2)">
                <button type="button" id="btn-batch-clear" class="btn btn-ghost" style="font-size:var(--fs-xs)">Clear Queue</button>
                <button type="button" id="btn-batch-review" class="btn" style="font-size:var(--fs-xs)">Review &amp; Add All</button>
              </div>
            </div>
            <div id="batch-queue-preview" style="font-size:var(--fs-xs);color:var(--ink-2)"></div>
          </div>

          <div id="upc-result-container" style="margin-top:var(--s4)"></div>
        </div>
      </div>
    `;

    // Tab switcher
    const tabSearch = document.getElementById("tab-search");
    const tabScan = document.getElementById("tab-scan");
    const searchPanel = document.getElementById("search-panel");
    const scannerPanel = document.getElementById("scanner-panel");

    let currentMode = "single";

    tabSearch?.addEventListener("click", () => {
      stopBarcodeScanner();
      tabSearch.classList.add("active");
      tabScan.classList.remove("active");
      searchPanel.style.display = "block";
      scannerPanel.style.display = "none";
    });

    tabScan?.addEventListener("click", async () => {
      tabScan.classList.add("active");
      tabSearch.classList.remove("active");
      searchPanel.style.display = "none";
      scannerPanel.style.display = "block";
      initScanner();
    });

    // Scanner Mode Switcher
    const modeBtns = document.querySelectorAll("#scan-mode-toggle .toggle-btn");
    modeBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        modeBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentMode = btn.dataset.mode;
        resumeBarcodeScanner();
        updateBatchUI();
      });
    });

    // Manual UPC lookup
    const manualInput = document.getElementById("manual-upc-input");
    const manualBtn = document.getElementById("manual-upc-btn");
    manualBtn?.addEventListener("click", () => {
      const code = manualInput.value.trim();
      if (code) handleBarcodeScanned(code);
    });
    manualInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const code = manualInput.value.trim();
        if (code) handleBarcodeScanned(code);
      }
    });

    // Batch Actions
    const btnBatchClear = document.getElementById("btn-batch-clear");
    const btnBatchReview = document.getElementById("btn-batch-review");
    btnBatchClear?.addEventListener("click", () => {
      if (confirm("Clear all scanned barcodes from the queue?")) {
        setBatchQueue([]);
        updateBatchUI();
      }
    });
    btnBatchReview?.addEventListener("click", () => {
      reviewBatchQueue();
    });

    function updateBatchUI() {
      const batchArea = document.getElementById("batch-area");
      const countLabel = document.getElementById("batch-count-label");
      const preview = document.getElementById("batch-queue-preview");
      const queue = getBatchQueue();

      if (currentMode === "batch" || queue.length > 0) {
        if (batchArea) batchArea.style.display = "block";
        if (countLabel) countLabel.textContent = `${queue.length} Disc${queue.length === 1 ? "" : "s"} in Queue`;
        if (preview) {
          preview.textContent = queue.length > 0
            ? `Barcodes ready: ${queue.slice(-5).join(", ")}${queue.length > 5 ? ` and ${queue.length - 5} more` : ""}`
            : "Scan barcodes continuously to stack them into your queue.";
        }
      } else if (batchArea) {
        batchArea.style.display = "none";
      }
    }

    async function handleBarcodeScanned(code) {
      if (currentMode === "batch") {
        const queue = getBatchQueue();
        if (!queue.includes(code)) {
          queue.push(code);
          setBatchQueue(queue);
          toast(`Scanned UPC ${code} (${queue.length} in queue)`);
        } else {
          toast(`UPC ${code} already in queue`);
        }
        updateBatchUI();
        if (manualInput) manualInput.value = "";
      } else {
        // Single Scan Mode: pause camera scanner so loop doesn't continuously trigger
        pauseBarcodeScanner();
        await lookupSingleUpc(code);
      }
    }

    async function lookupSingleUpc(code) {
      const resultContainer = document.getElementById("upc-result-container");
      if (!resultContainer) return;
      resultContainer.innerHTML = `<div style="text-align:center;padding:24px;color:var(--ink-2)">Looking up barcode ${esc(code)}…</div>`;

      try {
        const data = await api(`/admin/api/upc/${encodeURIComponent(code)}`);
        const results = data.results || [];
        if (results.length === 0) {
          resultContainer.innerHTML = `
            <div class="empty-state">
              <h2>Product Found, but no TMDB match</h2>
              <p>Product: <strong>${esc(data.raw_title)}</strong></p>
              <p>Try searching TMDB directly by title.</p>
              <button type="button" class="btn btn-ghost" id="btn-cancel-upc" style="margin-top:var(--s2)">Dismiss & Scan Again</button>
            </div>
          `;
          document.getElementById("btn-cancel-upc")?.addEventListener("click", () => {
            resultContainer.innerHTML = "";
            resumeBarcodeScanner();
          });
          return;
        }

        const isMulti = results.length > 1;
        resultContainer.innerHTML = `
          <div style="background:var(--card);border:1px solid var(--line);border-radius:var(--r-card);padding:var(--s4);max-width:540px;margin:0 auto">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--s1)">
              <span style="font-size:var(--fs-xs);color:var(--ink-3)">BARCODE: ${esc(data.upc)}</span>
              <span class="chip k4">${esc(data.format_hint)}</span>
            </div>
            <div style="font-size:var(--fs-sm);color:var(--ink-2);margin-bottom:var(--s2)">Found: <strong>${esc(data.raw_title)}</strong></div>

            ${
              data.already_owned
                ? `<div class="duplicate-banner" style="margin-bottom:var(--s3)">⚠️ In collection: ${esc(data.owned_summary)}. Adding this will attach your physical disc.</div>`
                : ""
            }

            ${
              isMulti
                ? `<div style="font-size:var(--fs-xs);color:var(--ink-3);margin-bottom:var(--s2);text-transform:uppercase;letter-spacing:0.5px">Select matching title (${results.length} found):</div>`
                : ""
            }

            <div style="display:flex;flex-direction:column;gap:var(--s2);margin-bottom:var(--s3)">
              ${results.slice(0, 5).map((m, idx) => `
                <div style="display:flex;gap:var(--s3);align-items:center;padding:var(--s2);border:1px solid var(--line);border-radius:var(--r-card);background:var(--bg)">
                  ${m.thumb ? `<img src="${esc(m.thumb)}" style="width:40px;height:60px;border-radius:4px;object-fit:cover;flex-shrink:0" alt="">` : '<div style="width:40px;height:60px;background:var(--card);border-radius:4px;flex-shrink:0"></div>'}
                  <div style="flex:1;min-width:0">
                    <div style="font-weight:700;font-size:var(--fs-sm);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(m.title)}</div>
                    <div style="font-size:var(--fs-xs);color:var(--ink-2)">${m.year || ""} ${m.already_owned ? `<span style="color:var(--amber);margin-left:4px">⚠️ Owned: ${esc(m.owned_summary || "")}</span>` : ""}</div>
                  </div>
                  <button type="button" class="btn btn-sm btn-select-candidate" data-idx="${idx}">
                    Add Disc
                  </button>
                </div>
              `).join("")}
            </div>

            <div style="display:flex;justify-content:flex-end">
              <button type="button" class="btn btn-ghost" id="btn-cancel-upc">Cancel / Scan Again</button>
            </div>
          </div>
        `;

        resultContainer.querySelectorAll(".btn-select-candidate").forEach((btn) => {
          btn.addEventListener("click", () => {
            const idx = parseInt(btn.dataset.idx, 10);
            const selectedMovie = results[idx];
            if (selectedMovie) {
              openCopyModal(selectedMovie, {
                kind: "physical",
                format: data.format_hint,
                duplicateNotice: selectedMovie.already_owned ? (selectedMovie.owned_summary || data.owned_summary) : (data.already_owned ? data.owned_summary : null),
              });
            }
          });
        });

        document.getElementById("btn-cancel-upc")?.addEventListener("click", () => {
          resultContainer.innerHTML = "";
          resumeBarcodeScanner();
        });
      } catch (err) {
        resultContainer.innerHTML = `
          <div class="empty-state">
            <h2>Lookup Failed</h2>
            <p>${esc(err.message || "Product not found for this barcode.")}</p>
            <button type="button" class="btn btn-ghost" id="btn-cancel-upc" style="margin-top:var(--s2)">Dismiss & Scan Again</button>
          </div>
        `;
        document.getElementById("btn-cancel-upc")?.addEventListener("click", () => {
          resultContainer.innerHTML = "";
          resumeBarcodeScanner();
        });
      }
    }

    async function reviewBatchQueue() {
      const queue = getBatchQueue();
      if (queue.length === 0) {
        toast("Queue is empty");
        return;
      }

      const modalRoot = document.getElementById("modal-root");
      if (!modalRoot) return;

      modalRoot.innerHTML = `
        <div class="modal-overlay" id="modal-overlay">
          <div class="modal-dialog" style="max-width:600px;max-height:85vh;overflow-y:auto">
            <div class="modal-header">
              <div>
                <div class="modal-title">Review Scanned Discs (${queue.length})</div>
                <div style="font-size:var(--fs-xs);color:var(--ink-3)">Resolve and import your physical discs in bulk</div>
              </div>
              <button type="button" class="btn-close" id="btn-batch-modal-close">✕</button>
            </div>

            <div id="batch-items-container" style="padding:var(--s3) 0">
              <div style="text-align:center;padding:24px;color:var(--ink-2)">Resolving ${queue.length} barcodes…</div>
            </div>

            <div class="form-actions" style="margin-top:var(--s4)">
              <button type="button" class="btn btn-ghost" id="btn-batch-modal-cancel">Close</button>
              <button type="button" class="btn" id="btn-batch-import-all">Import All Selected</button>
            </div>
          </div>
        </div>
      `;

      document.getElementById("btn-batch-modal-close")?.addEventListener("click", () => modalRoot.innerHTML = "");
      document.getElementById("btn-batch-modal-cancel")?.addEventListener("click", () => modalRoot.innerHTML = "");

      // Resolve items sequentially
      const itemsContainer = document.getElementById("batch-items-container");
      const resolved = [];

      for (let i = 0; i < queue.length; i++) {
        const upc = queue[i];
        if (itemsContainer) {
          itemsContainer.innerHTML = `<div style="text-align:center;padding:24px;color:var(--ink-2)">Resolving barcode ${i + 1} of ${queue.length}…</div>`;
        }
        try {
          const res = await api(`/admin/api/upc/${encodeURIComponent(upc)}`);
          if (res.results && res.results.length > 0) {
            resolved.push({ upc, ok: true, data: res });
          } else {
            resolved.push({ upc, ok: false, error: "No TMDB match" });
          }
        } catch (e) {
          resolved.push({ upc, ok: false, error: e.message || "Lookup error" });
        }
      }

      itemsContainer.innerHTML = resolved
        .map((item, idx) => {
          if (!item.ok) {
            return `
              <div style="padding:var(--s2);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;opacity:0.6">
                <div>
                  <div style="font-weight:600;font-size:var(--fs-sm)">UPC: ${esc(item.upc)}</div>
                  <div style="font-size:var(--fs-xs);color:var(--accent-text)">${esc(item.error)}</div>
                </div>
              </div>
            `;
          }
          const d = item.data;
          const movie = d.results[0];
          return `
            <div style="padding:var(--s2) 0;border-bottom:1px solid var(--line);display:flex;gap:var(--s3);align-items:center">
              <input type="checkbox" class="batch-chk" data-idx="${idx}" checked style="width:18px;height:18px">
              ${movie.thumb ? `<img src="${esc(movie.thumb)}" style="width:36px;height:54px;border-radius:4px;object-fit:cover" alt="">` : ""}
              <div style="flex:1">
                <div style="font-weight:700;font-size:var(--fs-sm)">${esc(movie.title)} <span style="font-weight:400;color:var(--ink-2)">(${movie.year || ""})</span></div>
                <div style="font-size:var(--fs-xs);color:var(--ink-2)"><span class="chip k4" style="height:18px;font-size:10px">${esc(d.format_hint)}</span> ${d.already_owned ? `<span style="color:var(--amber);margin-left:6px">⚠️ Owned: ${esc(d.owned_summary)}</span>` : ""}</div>
              </div>
            </div>
          `;
        })
        .join("");

      document.getElementById("btn-batch-import-all")?.addEventListener("click", async () => {
        const chks = itemsContainer.querySelectorAll(".batch-chk:checked");
        let addedCount = 0;
        const importedUpcs = new Set();
        const btn = document.getElementById("btn-batch-import-all");
        if (btn) btn.disabled = true;

        for (const chk of chks) {
          const idx = parseInt(chk.dataset.idx, 10);
          const item = resolved[idx];
          if (item?.ok && item.data.results?.[0]) {
            try {
              await api("/admin/api/copies", {
                method: "POST",
                body: JSON.stringify({
                  tmdb_id: item.data.results[0].tmdb_id,
                  kind: "physical",
                  format: item.data.format_hint,
                  service: null,
                }),
              });
              addedCount++;
              importedUpcs.add(item.upc);
            } catch (err) {}
          }
        }

        const remaining = queue.filter((code) => !importedUpcs.has(code));
        setBatchQueue(remaining);
        updateBatchUI();
        modalRoot.innerHTML = "";
        if (addedCount > 0) {
          toast(`Successfully added ${addedCount} physical disc copies!`);
        }
        if (remaining.length > 0) {
          toast(`${remaining.length} barcode(s) remaining in queue`);
        }
        navigate("#/");
      });
    }

    async function initScanner() {
      const video = document.getElementById("scanner-video");
      const reticle = document.getElementById("scanner-reticle");
      const status = document.getElementById("scanner-status");
      updateBatchUI();

      if (!video || !reticle) return;
      const res = await startBarcodeScanner(video, reticle, (barcode) => {
        handleBarcodeScanned(barcode);
      });

      if (!res.supported) {
        if (status) status.innerHTML = `<span style="color:var(--amber)">Native Barcode Detection not supported in this browser. Use manual UPC entry below.</span>`;
      } else if (res.error) {
        if (status) status.innerHTML = `<span style="color:var(--accent-text)">Camera access denied or unavailable (${esc(res.error)}). Use manual entry below.</span>`;
      } else {
        if (status) status.textContent = "Camera active: place barcode in box";
      }
    }

    // Bind search listeners
    const searchInput = document.getElementById("tmdb-search-input");
    const clearBtn = document.getElementById("tmdb-clear-search");

    let debounceTimer;
    searchInput?.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      const val = e.target.value.trim();
      state.add.q = val;
      debounceTimer = setTimeout(() => {
        executeTmdbSearch(val);
      }, 400);
    });

    clearBtn?.addEventListener("click", () => {
      if (searchInput) searchInput.value = "";
      state.add.q = "";
      executeTmdbSearch("");
    });

    if (initialQ.length >= 2) {
      executeTmdbSearch(initialQ);
    }
  }

  async function executeTmdbSearch(query) {
    const container = document.getElementById("tmdb-results-container");
    if (!container) return;

    if (!query || query.length < 2) {
      container.innerHTML = ``;
      return;
    }

    container.innerHTML = `<div style="text-align:center;padding:32px;color:var(--ink-2)">Searching TMDB…</div>`;

    try {
      const data = await api(`/admin/api/tmdb/search?q=${encodeURIComponent(query)}`);
      const results = data.results || [];
      state.add.results = results;

      if (results.length === 0) {
        container.innerHTML = `
          <div class="empty-state" style="margin-top:var(--s4)">
            <h2>No matches</h2>
            <p>No TMDB matches for &#8216;${esc(query)}&#8217;. Check spelling or try fewer words.</p>
          </div>
        `;
        return;
      }

      container.innerHTML = `
        <div class="results-list">
          ${results
            .map((m) => {
              const thumbUrl = m.poster_path ? `${TMDB_IMG_THUMB}${m.poster_path}` : "";
              const thumbHtml = thumbUrl
                ? `<img class="result-thumb" src="${esc(thumbUrl)}" loading="lazy" alt="${esc(m.title)}" onerror="this.style.display='none';">`
                : `<div class="result-thumb poster-ph" style="font-size:10px">${esc(m.title)}</div>`;

              return `
                <div class="result-card" data-tmdb-id="${m.tmdb_id}">
                  ${thumbHtml}
                  <div class="result-info">
                    <div class="result-title">${esc(m.title)}</div>
                    <div class="result-year">${m.year || "Unknown year"}</div>
                    ${m.overview ? `<div class="result-overview">${esc(m.overview)}</div>` : ""}
                    ${m.owned ? `<span class="badge-owned">In collection</span>` : ""}
                  </div>
                </div>
              `;
            })
            .join("")}
        </div>
      `;

      container.querySelectorAll(".result-card").forEach((card) => {
        card.addEventListener("click", () => {
          const tmdbId = parseInt(card.dataset.tmdbId, 10);
          const movie = state.add.results.find((r) => r.tmdb_id === tmdbId);
          if (movie) {
            openCopyModal(movie);
          }
        });
      });
    } catch (err) {
      let msg = "TMDB lookup failed.";
      if (err.reason === "not_configured" || err.message?.includes("not configured")) {
        msg = "TMDB is not configured.";
      } else if (err.reason === "rate_limited" || err.status === 429 || err.message?.includes("busy")) {
        msg = "TMDB is busy. Try again in a minute.";
      } else if (err.reason === "unavailable" || err.status === 502) {
        msg = "TMDB is unavailable right now.";
      } else if (err.message) {
        msg = err.message;
      }

      container.innerHTML = `
        <div class="empty-state" style="margin-top:var(--s4)">
          <h2>Search Error</h2>
          <p>${esc(msg)}</p>
        </div>
      `;
    }
  }

  // ================================================================= Copy Form Modal (8.2)
  async function openCopyModal(movie, prefill = {}) {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

    let locationsList = [];
    try {
      const locData = await api("/api/locations");
      locationsList = locData.locations || [];
    } catch {
      locationsList = [];
    }

    const initialKind = prefill.kind || "physical";
    const initialFormat = prefill.format || (initialKind === "physical" ? "4K UHD" : "Digital HD");
    const initialService = prefill.service || "";

    modalRoot.innerHTML = `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog">
          <div class="modal-header">
            <div>
              <div class="modal-title">${esc(movie.title)}</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">${movie.year || ""}</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>

          <div id="modal-owned-status">
            ${
              prefill.duplicateNotice
                ? `<div class="duplicate-banner">⚠️ In collection: ${esc(prefill.duplicateNotice)}. Adding this will attach your physical disc.</div>`
                : `<div style="font-size:var(--fs-xs);color:var(--ink-3)">Checking collection…</div>`
            }
          </div>

          <div id="modal-duplicate-warning" style="display:none" class="notice-banner notice-warn">
            Warning: You already own this format.
          </div>

          <form id="copy-form" style="display:flex;flex-direction:column;gap:var(--s4)">
            <div class="form-group">
              <label class="form-label">Kind</label>
              <div class="toggle-group" id="kind-toggle">
                <button type="button" class="toggle-btn ${initialKind === "physical" ? "active" : ""}" data-kind="physical">Physical</button>
                <button type="button" class="toggle-btn ${initialKind === "digital" ? "active" : ""}" data-kind="digital">Digital</button>
              </div>
            </div>

            <div class="form-group">
              <label class="form-label">Format</label>
              <div class="chip-select-grid" id="format-select-grid">
                <!-- Injected based on kind -->
              </div>
            </div>

            <div class="form-group" id="service-group" style="${initialKind === "digital" ? "" : "display:none"}">
              <label class="form-label">Service</label>
              <div class="chip-select-grid" id="service-select-grid">
                <!-- Injected for digital -->
              </div>
            </div>

            <div class="form-group" id="location-group" style="${initialKind === "physical" ? "display:flex;flex-direction:column;gap:var(--s1)" : "display:none"}">
              <label class="form-label">Storage Location</label>
              <select id="select-copy-location" class="form-select" style="width:100%;padding:var(--s2);background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--ink)">
                <option value="">(Unassigned / No shelf)</option>
                ${locationsList.map((l) => `<option value="${l.id}">${esc(l.path)}</option>`).join("")}
              </select>
            </div>

            <div class="form-actions">
              <button type="button" class="btn btn-ghost" id="btn-modal-cancel">Cancel</button>
              <button type="submit" class="btn" id="btn-save-copy">Save Copy</button>
              <button type="button" class="btn btn-ghost" id="btn-save-add-another">Save &amp; Add Another</button>
            </div>
          </form>
        </div>
      </div>
    `;

    // Local form state
    const formState = {
      tmdb_id: movie.tmdb_id,
      kind: initialKind,
      format: initialFormat,
      service: initialService,
      ownedCopies: [],
    };

    function updateFormatGrid() {
      const grid = document.getElementById("format-select-grid");
      if (!grid) return;
      const formats = FORMATS[formState.kind] || [];
      if (!formats.includes(formState.format)) {
        formState.format = formats[0];
      }

      grid.innerHTML = formats
        .map(
          (fmt) => `
          <button type="button" class="select-chip ${fmt === formState.format ? "active" : ""}" data-fmt="${esc(fmt)}">
            ${esc(fmt)}
          </button>
        `
        )
        .join("");

      grid.querySelectorAll(".select-chip").forEach((btn) => {
        btn.addEventListener("click", () => {
          formState.format = btn.dataset.fmt;
          updateFormatGrid();
          checkDuplicateWarning();
        });
      });
    }

    function updateServiceGrid() {
      const srvGroup = document.getElementById("service-group");
      const grid = document.getElementById("service-select-grid");
      if (!srvGroup || !grid) return;

      if (formState.kind === "digital") {
        srvGroup.style.display = "flex";
        if (!formState.service) {
          formState.service = SERVICES[0];
        }
        grid.innerHTML = SERVICES.map(
          (srv) => `
          <button type="button" class="select-chip ${srv === formState.service ? "active" : ""}" data-srv="${esc(srv)}">
            ${esc(srv)}
          </button>
        `
        ).join("");

        grid.querySelectorAll(".select-chip").forEach((btn) => {
          btn.addEventListener("click", () => {
            formState.service = btn.dataset.srv;
            updateServiceGrid();
          });
        });
      } else {
        srvGroup.style.display = "none";
        formState.service = null;
      }
    }

    function checkDuplicateWarning() {
      const warnBox = document.getElementById("modal-duplicate-warning");
      if (!warnBox) return;

      const hasDuplicate = formState.ownedCopies.some(
        (cp) => cp.kind === formState.kind && cp.format === formState.format
      );

      if (hasDuplicate) {
        warnBox.textContent = `A ${formState.format} copy is already owned in your collection.`;
        warnBox.style.display = "block";
      } else {
        warnBox.style.display = "none";
      }
    }

    // Kind toggle events
    document.querySelectorAll("#kind-toggle .toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#kind-toggle .toggle-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        formState.kind = btn.dataset.kind;
        const locGroup = document.getElementById("location-group");
        if (locGroup) {
          locGroup.style.display = formState.kind === "physical" ? "flex" : "none";
        }
        updateFormatGrid();
        updateServiceGrid();
        checkDuplicateWarning();
      });
    });

    updateFormatGrid();
    updateServiceGrid();

    // Check what is already owned from TMDB API
    try {
      const owned = await api(`/admin/api/tmdb/${movie.tmdb_id}/owned`);
      formState.ownedCopies = owned.copies || [];
      const statusBox = document.getElementById("modal-owned-status");
      if (statusBox) {
        if (prefill.duplicateNotice) {
          statusBox.innerHTML = `
            <div class="duplicate-banner">⚠️ In collection: ${esc(prefill.duplicateNotice)}. Adding this will attach your physical disc.</div>
          `;
        } else if (owned.owned && owned.summary) {
          statusBox.innerHTML = `
            <div class="notice-banner notice-info">
              <strong>Already in the collection:</strong> ${esc(owned.summary)}
            </div>
          `;
        } else {
          statusBox.innerHTML = ``;
        }
      }
      checkDuplicateWarning();
    } catch {
      const statusBox = document.getElementById("modal-owned-status");
      if (statusBox && !prefill.duplicateNotice) statusBox.innerHTML = ``;
    }

    // Modal close & click outside
    document.getElementById("btn-modal-close")?.addEventListener("click", closeModal);
    document.getElementById("btn-modal-cancel")?.addEventListener("click", closeModal);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") closeModal();
    });

    // Form submission
    async function submitCopy(andAddAnother = false) {
      const locSelect = document.getElementById("select-copy-location");
      const locVal = locSelect?.value ? parseInt(locSelect.value, 10) : null;
      const payload = {
        tmdb_id: formState.tmdb_id,
        kind: formState.kind,
        format: formState.format,
        service: formState.kind === "digital" ? formState.service : null,
        location_id: formState.kind === "physical" ? locVal : null,
      };

      const saveBtn = document.getElementById("btn-save-copy");
      const addAnotherBtn = document.getElementById("btn-save-add-another");
      if (saveBtn) saveBtn.disabled = true;
      if (addAnotherBtn) addAnotherBtn.disabled = true;

      try {
        const res = await api("/admin/api/copies", {
          method: "POST",
          body: payload,
        });

        toast("Copy added to collection");

        if (andAddAnother) {
          // Re-fetch owned status and update form
          try {
            const owned = await api(`/admin/api/tmdb/${movie.tmdb_id}/owned`);
            formState.ownedCopies = owned.copies || [];
            const statusBox = document.getElementById("modal-owned-status");
            if (statusBox && owned.summary) {
              statusBox.innerHTML = `
                <div class="notice-banner notice-info">
                  <strong>Already in the collection:</strong> ${esc(owned.summary)}
                </div>
              `;
            }
            checkDuplicateWarning();
          } catch {
            // ignore
          }
          if (saveBtn) saveBtn.disabled = false;
          if (addAnotherBtn) addAnotherBtn.disabled = false;
        } else {
          closeModal();
          // Navigate to title detail page
          const targetId = res.id || res.title_id;
          if (targetId) {
            navigate(`#/t/${targetId}`);
          } else {
            navigate("#/");
          }
        }
      } catch (err) {
        toast(err.message || "Failed to save copy");
        // Form state survives on error
        if (saveBtn) saveBtn.disabled = false;
        if (addAnotherBtn) addAnotherBtn.disabled = false;
      }
    }

    document.getElementById("copy-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      submitCopy(false);
    });

    document.getElementById("btn-save-add-another")?.addEventListener("click", () => {
      submitCopy(true);
    });
  }

  // Modal for editing an existing copy
  async function openEditCopyModal(copy, title) {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

    let locationsList = [];
    try {
      const locData = await api("/api/locations");
      locationsList = locData.locations || [];
    } catch {
      locationsList = [];
    }

    modalRoot.innerHTML = `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog">
          <div class="modal-header">
            <div>
              <div class="modal-title">Edit Copy</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">${esc(title.title)}</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>

          <form id="edit-copy-form" style="display:flex;flex-direction:column;gap:var(--s4)">
            <div class="form-group">
              <label class="form-label">Kind</label>
              <div class="toggle-group" id="edit-kind-toggle">
                <button type="button" class="toggle-btn ${copy.kind === "physical" ? "active" : ""}" data-kind="physical">Physical</button>
                <button type="button" class="toggle-btn ${copy.kind === "digital" ? "active" : ""}" data-kind="digital">Digital</button>
              </div>
            </div>

            <div class="form-group">
              <label class="form-label">Format</label>
              <div class="chip-select-grid" id="edit-format-select-grid"></div>
            </div>

            <div class="form-group" id="edit-service-group" style="display:${copy.kind === "digital" ? "flex" : "none"}">
              <label class="form-label">Service</label>
              <div class="chip-select-grid" id="edit-service-select-grid"></div>
            </div>

            <div class="form-group" id="edit-location-group" style="${copy.kind === "physical" ? "display:flex;flex-direction:column;gap:var(--s1)" : "display:none"}">
              <label class="form-label">Storage Location</label>
              <select id="edit-select-copy-location" class="form-select" style="width:100%;padding:var(--s2);background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--ink)">
                <option value="">(Unassigned / No shelf)</option>
                ${locationsList.map((l) => `<option value="${l.id}" ${copy.location_id === l.id ? "selected" : ""}>${esc(l.path)}</option>`).join("")}
              </select>
            </div>

            <div class="form-actions">
              <button type="button" class="btn btn-ghost" id="btn-modal-cancel">Cancel</button>
              <button type="submit" class="btn" id="btn-edit-save">Save Changes</button>
            </div>
          </form>
        </div>
      </div>
    `;

    const formState = {
      kind: copy.kind,
      format: copy.format,
      service: copy.service || (copy.kind === "digital" ? SERVICES[0] : null),
    };

    function updateFormatGrid() {
      const grid = document.getElementById("edit-format-select-grid");
      if (!grid) return;
      const formats = FORMATS[formState.kind] || [];
      if (!formats.includes(formState.format)) {
        formState.format = formats[0];
      }

      grid.innerHTML = formats
        .map(
          (fmt) => `
          <button type="button" class="select-chip ${fmt === formState.format ? "active" : ""}" data-fmt="${esc(fmt)}">
            ${esc(fmt)}
          </button>
        `
        )
        .join("");

      grid.querySelectorAll(".select-chip").forEach((btn) => {
        btn.addEventListener("click", () => {
          formState.format = btn.dataset.fmt;
          updateFormatGrid();
        });
      });
    }

    function updateServiceGrid() {
      const srvGroup = document.getElementById("edit-service-group");
      const grid = document.getElementById("edit-service-select-grid");
      if (!srvGroup || !grid) return;

      if (formState.kind === "digital") {
        srvGroup.style.display = "flex";
        if (!formState.service) {
          formState.service = SERVICES[0];
        }
        grid.innerHTML = SERVICES.map(
          (srv) => `
          <button type="button" class="select-chip ${srv === formState.service ? "active" : ""}" data-srv="${esc(srv)}">
            ${esc(srv)}
          </button>
        `
        ).join("");

        grid.querySelectorAll(".select-chip").forEach((btn) => {
          btn.addEventListener("click", () => {
            formState.service = btn.dataset.srv;
            updateServiceGrid();
          });
        });
      } else {
        srvGroup.style.display = "none";
        formState.service = null;
      }
    }

    document.querySelectorAll("#edit-kind-toggle .toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#edit-kind-toggle .toggle-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        formState.kind = btn.dataset.kind;
        const locGroup = document.getElementById("edit-location-group");
        if (locGroup) {
          locGroup.style.display = formState.kind === "physical" ? "flex" : "none";
        }
        updateFormatGrid();
        updateServiceGrid();
      });
    });

    updateFormatGrid();
    updateServiceGrid();

    document.getElementById("btn-modal-close")?.addEventListener("click", closeModal);
    document.getElementById("btn-modal-cancel")?.addEventListener("click", closeModal);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") closeModal();
    });

    document.getElementById("edit-copy-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const locSelect = document.getElementById("edit-select-copy-location");
      const locVal = locSelect?.value ? parseInt(locSelect.value, 10) : null;
      try {
        await api(`/admin/api/copies/${copy.id}`, {
          method: "PUT",
          body: {
            kind: formState.kind,
            format: formState.format,
            service: formState.kind === "digital" ? formState.service : null,
            location_id: formState.kind === "physical" ? locVal : null,
          },
        });
        toast("Copy updated");
        closeModal();
        const view = document.getElementById("view");
        if (view) renderDetail(view, title.id);
      } catch (err) {
        toast(err.message || "Failed to update copy");
      }
    });
  }

  function closeModal() {
    const modalRoot = document.getElementById("modal-root");
    if (modalRoot) modalRoot.innerHTML = "";
    resumeBarcodeScanner();
  }

  // Close modal or filter popovers on Escape key, or navigate adjacent movies on ArrowLeft/ArrowRight
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeModal();
      state.openPopoverId = null;
      document.querySelectorAll(".filter-popover.open").forEach((p) => {
        p.classList.remove("open");
        p.querySelector(".filter-btn")?.setAttribute("aria-expanded", "false");
      });
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      if (state.route.path === "/t/:id") {
        const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
        if (activeTag === "input" || activeTag === "textarea" || activeTag === "select") return;
        const modalRoot = document.getElementById("modal-root");
        if (modalRoot && modalRoot.children.length > 0) return;

        if (e.key === "ArrowLeft") {
          const prevBtn = document.getElementById("btn-prev-movie");
          if (prevBtn && !prevBtn.classList.contains("disabled") && prevBtn.getAttribute("href") && prevBtn.getAttribute("href") !== "#") {
            e.preventDefault();
            location.hash = prevBtn.getAttribute("href");
          }
        } else if (e.key === "ArrowRight") {
          const nextBtn = document.getElementById("btn-next-movie");
          if (nextBtn && !nextBtn.classList.contains("disabled") && nextBtn.getAttribute("href") && nextBtn.getAttribute("href") !== "#") {
            e.preventDefault();
            location.hash = nextBtn.getAttribute("href");
          }
        }
      }
    }
  });

  // Close filter popovers on outside click
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".filter-popover")) {
      state.openPopoverId = null;
      document.querySelectorAll(".filter-popover.open").forEach((p) => {
        p.classList.remove("open");
        p.querySelector(".filter-btn")?.setAttribute("aria-expanded", "false");
      });
    }
  });

  // ================================================================= 8.1 Stats View
  async function renderStats(container) {
    container.innerHTML = `
      <div class="stats-view">
        <div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading statistics…</div>
      </div>
    `;

    const apiPrefix = isAdmin ? "/admin/api" : "/api";
    let data;
    try {
      data = await api(`${apiPrefix}/stats`);
    } catch (err) {
      container.innerHTML = `
        <div class="stats-view">
          <div class="empty-state">
            <h2>Unable to load stats</h2>
            <p>${esc(err.message || "Failed to connect to Matinee server")}</p>
            <button class="btn" onclick="location.reload()">Retry</button>
          </div>
        </div>
      `;
      return;
    }

    const ov = data.overview || {};
    const qualities = data.qualities || [];
    const formats = data.formats || [];
    const genres = data.genres || [];
    const services = data.services || [];
    const ws = data.watched_stats || {};
    const rs = data.rating_stats || {};

    const maxGenreCount = Math.max(1, ...genres.map((g) => g.count));

    container.innerHTML = `
      <div class="stats-view">
        <div class="stats-header">
          <div>
            <a href="#/" class="back-link">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
              Back to collection
            </a>
          </div>
          <h1 style="margin-top:var(--s2)">Collection Stats</h1>
          <p>Breakdown of movies, formats, qualities, and services in your family library.</p>
        </div>

        <!-- Overview Cards -->
        <section>
          <div class="stats-grid-overview">
            <div class="stat-card">
              <div class="stat-icon">🎬</div>
              <div class="stat-val">${ov.titles_count ?? 0}</div>
              <div class="stat-lbl">Unique Movies</div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">💿</div>
              <div class="stat-val">${ov.physical_count ?? 0}</div>
              <div class="stat-lbl">Physical Discs</div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">☁️</div>
              <div class="stat-val">${ov.digital_count ?? 0}</div>
              <div class="stat-lbl">Digital Copies</div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">💿☁️</div>
              <div class="stat-val">${ov.dual_count ?? 0}</div>
              <div class="stat-lbl">Dual-Owned</div>
            </div>
          </div>
        </section>

        <!-- Quality Breakdown -->
        <section>
          <h2 class="stats-section-title">Breakdown by Quality</h2>
          <div class="quality-grid">
            ${qualities
              .map((q) => {
                const fillClass = q.quality === "4K" ? "k4" : q.quality === "HD" ? "hd" : "sd";
                return `
                <div class="quality-card">
                  <div class="quality-card-head">
                    <span class="quality-card-title">${esc(q.quality)}</span>
                    <span class="quality-card-count">${q.count} movies (${q.percent}%)</span>
                  </div>
                  <div class="stat-progress-bar">
                    <div class="stat-progress-fill ${fillClass}" style="width: ${Math.min(100, q.percent)}%"></div>
                  </div>
                </div>
              `;
              })
              .join("")}
          </div>
        </section>

        <!-- Formats Breakdown -->
        <section>
          <h2 class="stats-section-title">Breakdown by Format</h2>
          <div class="formats-grid">
            ${formats
              .map((f) => {
                const chipClass = getChipClass(f.format);
                const chipIcon = getChipIcon(f.format);
                return `
                <div class="format-stat-item">
                  <span class="chip ${chipClass}">${chipIcon}${esc(f.format)}</span>
                  <span class="format-count-badge">${f.count}</span>
                </div>
              `;
              })
              .join("")}
          </div>
        </section>

        <!-- Genre Breakdown -->
        <section>
          <h2 class="stats-section-title">Breakdown by Genre</h2>
          ${
            genres.length === 0
              ? `<div style="color:var(--ink-3);font-size:var(--fs-sm)">No genre data recorded.</div>`
              : `
            <div class="genres-list">
              ${genres
                .map((g) => {
                  const pct = Math.round((g.count / maxGenreCount) * 100);
                  return `
                  <div class="genre-row">
                    <div class="genre-row-meta">
                      <span>${esc(g.genre)}</span>
                      <span style="color:var(--ink-2)">${g.count}</span>
                    </div>
                    <div class="genre-bar-track">
                      <div class="genre-bar-fill" style="width: ${pct}%"></div>
                    </div>
                  </div>
                `;
                })
                .join("")}
            </div>
          `
          }
        </section>

        <!-- Digital Services Breakdown -->
        <section>
          <h2 class="stats-section-title">Digital Services</h2>
          ${
            services.length === 0
              ? `<div style="color:var(--ink-3);font-size:var(--fs-sm)">No digital service copies recorded.</div>`
              : `
            <div class="services-grid">
              ${services
                .map(
                  (s) => `
                <div class="service-card">
                  <span class="service-card-name">${esc(s.service)}</span>
                  <span class="format-count-badge">${s.count}</span>
                </div>
              `
                )
                .join("")}
            </div>
          `
          }
        </section>

        <!-- Activity & Ratings Breakdown -->
        <section>
          <h2 class="stats-section-title">Activity &amp; Ratings</h2>
          <div class="stats-activity-grid">
            <div class="stat-activity-card">
              <div class="stat-activity-head">
                <span class="stat-activity-title">Watched Movies</span>
                <span class="stat-activity-metric">${ws.total_watched_titles ?? 0} / ${ov.titles_count ?? 0} (${ws.watched_percent ?? 0}%)</span>
              </div>
              <div class="stat-progress-bar">
                <div class="stat-progress-fill" style="width: ${Math.min(100, ws.watched_percent ?? 0)}%; background: #22c55e;"></div>
              </div>
              <div class="stat-activity-meta">
                <span>${ws.watched_this_year ?? 0} watched this year</span>
                <span>·</span>
                <span>${ws.watched_this_month ?? 0} this month</span>
                <span>·</span>
                <span>${ws.total_watch_events ?? 0} total plays</span>
              </div>
            </div>

            <div class="stat-activity-card">
              <div class="stat-activity-head">
                <span class="stat-activity-title">Family Ratings</span>
                <span class="stat-activity-metric">${rs.average_rating ? `★ ${Number(rs.average_rating).toFixed(1)} / 10` : "No ratings yet"}</span>
              </div>
              <div class="stat-progress-bar">
                <div class="stat-progress-fill" style="width: ${Math.min(100, rs.rated_percent ?? 0)}%; background: var(--amber);"></div>
              </div>
              <div class="stat-activity-meta">
                <span>${rs.rated_titles_count ?? 0} rated (${rs.rated_percent ?? 0}%)</span>
                <span>·</span>
                <span>${rs.high_rated_count ?? 0} rated 8.0+ ★</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    `;
  }

  // ================================================================= 9.1 About View
  function renderAbout(container) {
    container.innerHTML = `
      <div class="about-view">
        <div>
          <a href="#/" class="back-link">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            Back to collection
          </a>
          <h1 style="font-size:var(--fs-xl);font-weight:800;margin-top:var(--s2)">About Matinee</h1>
          <p style="color:var(--ink-2);margin-top:var(--s2);line-height:1.6">
            Matinee is a self-hosted family movie tracker that brings together our physical discs (4K UHD, Blu-ray, DVD, VHS) and digital libraries (Movies Anywhere, Apple TV, Amazon, and more) into a single unified catalog.
          </p>
        </div>

        <div class="tmdb-attribution">
          <img src="/static/tmdb.svg" alt="TMDB Logo" class="tmdb-logo">
          <p style="font-size:var(--fs-sm);color:var(--ink-2);line-height:1.5">
            This product uses the TMDB API but is not endorsed or certified by TMDB.
          </p>
          <a href="https://www.themoviedb.org/" target="_blank" rel="noopener noreferrer" style="font-size:var(--fs-sm)">
            themoviedb.org
          </a>
        </div>
      </div>
    `;
  }

  // 404 View
  function renderNotFound(container) {
    container.innerHTML = `
      <div class="empty-state">
        <h2>Page not found</h2>
        <p>The page you requested could not be found.</p>
        <a href="#/" class="btn">Back to collection</a>
      </div>
    `;
  }

  // ================================================================= Movie Night Modal
  async function openMovieNightModal() {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

    let currentMode = "forgotten_gems";
    let currentPick = null;

    function renderModalSkeleton() {
      modalRoot.innerHTML = `
        <div class="modal-overlay" id="modal-overlay">
          <div class="modal-dialog movie-night-dialog">
            <div class="modal-header">
              <h2>🎲 Movie Night Picker</h2>
              <button class="btn-close" id="btn-movie-night-close" aria-label="Close">&times;</button>
            </div>
            <div class="movie-night-tabs">
              <button type="button" class="movie-night-tab ${currentMode === "forgotten_gems" ? "active" : ""}" data-mode="forgotten_gems">Forgotten Gems</button>
              <button type="button" class="movie-night-tab ${currentMode === "quick_picks" ? "active" : ""}" data-mode="quick_picks">Quick Picks</button>
              <button type="button" class="movie-night-tab ${currentMode === "never_watched" ? "active" : ""}" data-mode="never_watched">Never Watched</button>
              <button type="button" class="movie-night-tab ${currentMode === "genre_mood" ? "active" : ""}" data-mode="genre_mood">Surprise Me</button>
            </div>
            <div class="movie-night-content" id="movie-night-content">
              <div class="movie-night-loading">Picking a movie…</div>
            </div>
            <div class="modal-actions movie-night-actions">
              <button type="button" class="btn btn-secondary" id="btn-movie-night-roll">🎲 Roll Again</button>
              <button type="button" class="btn btn-primary" id="btn-movie-night-view" disabled>Open Movie</button>
              <button type="button" class="btn btn-ghost" id="btn-movie-night-cancel">Close</button>
            </div>
          </div>
        </div>
      `;

      modalRoot.querySelectorAll(".movie-night-tab").forEach(tab => {
        tab.addEventListener("click", () => {
          modalRoot.querySelectorAll(".movie-night-tab").forEach(t => t.classList.remove("active"));
          tab.classList.add("active");
          currentMode = tab.dataset.mode;
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

    async function fetchPick() {
      const content = document.getElementById("movie-night-content");
      const viewBtn = document.getElementById("btn-movie-night-view");
      if (!content) return;
      content.innerHTML = `<div class="movie-night-loading">Picking a movie…</div>`;
      if (viewBtn) viewBtn.disabled = true;

      const apiPrefix = isAdmin ? "/admin/api" : "/api";
      try {
        const res = await api(`${apiPrefix}/recommendations?mode=${encodeURIComponent(currentMode)}&limit=1&random=1`);
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
                ${currentPick.genres && currentPick.genres.length > 0 ? `<div class="movie-night-genres">${currentPick.genres.map(g => `<span class="genre-tag">${esc(g)}</span>`).join(" ")}</div>` : ""}
                <div class="movie-night-reason">${esc(currentPick.recommendation_reason || "Curated pick")}</div>
              </div>
            </div>
          `;
          if (viewBtn) viewBtn.disabled = false;
        } else {
          currentPick = null;
          content.innerHTML = `
            <div class="movie-night-empty movie-night-animated">
              <div style="font-size:2rem;margin-bottom:8px">🍿</div>
              <p style="font-weight:600;color:var(--ink);margin-bottom:4px">No matching movies found</p>
              <p style="font-size:var(--fs-xs);color:var(--ink-2)">Try rolling in another category like "Quick Picks" or "Surprise Me"!</p>
            </div>
          `;
          if (viewBtn) viewBtn.disabled = true;
        }
      } catch (err) {
        content.innerHTML = `<div class="movie-night-empty movie-night-animated"><p>Failed to load recommendation: ${esc(err.message || String(err))}</p></div>`;
        if (viewBtn) viewBtn.disabled = true;
      }
    }

    renderModalSkeleton();
    await fetchPick();
  }

  // ================================================================= Locations Manager Modal
  async function openLocationsManagerModal() {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

    let locations = [];
    try {
      const data = await api("/admin/api/locations");
      locations = data.locations || [];
    } catch (err) {
      toast(err.message || "Failed to load locations");
      return;
    }

    modalRoot.innerHTML = `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog" style="max-width:640px">
          <div class="modal-header">
            <div>
              <div class="modal-title">📍 Shelves &amp; Locations</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">Manage physical storage locations (rooms, cabinets, shelves, bins)</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>

          <div style="display:flex;flex-direction:column;gap:var(--s4)">
            <!-- Add New Location Form -->
            <form id="add-location-form" style="display:flex;flex-direction:column;gap:var(--s3);padding:var(--s3);background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm)">
              <div style="font-weight:600;font-size:var(--fs-sm)">Add New Shelf / Location</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--s2)">
                <div class="form-group">
                  <label class="form-label" style="font-size:var(--fs-xs)">Name</label>
                  <input type="text" id="loc-add-name" class="form-input" placeholder="e.g. Media Cabinet, Shelf 2" required style="padding:var(--s2);font-size:var(--fs-sm)">
                </div>
                <div class="form-group">
                  <label class="form-label" style="font-size:var(--fs-xs)">Parent Location (Optional)</label>
                  <select id="loc-add-parent" class="form-select" style="padding:var(--s2);font-size:var(--fs-sm)">
                    <option value="">(None - Top Level)</option>
                    ${locations.map(l => `<option value="${l.id}">${esc(l.path)}</option>`).join("")}
                  </select>
                </div>
              </div>
              <div style="display:flex;justify-content:flex-end">
                <button type="submit" class="btn btn-sm" id="btn-loc-submit">＋ Add Location</button>
              </div>
            </form>

            <!-- Locations Tree List -->
            <div>
              <div style="font-weight:600;font-size:var(--fs-sm);margin-bottom:var(--s2)">Existing Locations (${locations.length})</div>
              ${
                locations.length === 0
                  ? `<div style="text-align:center;padding:var(--s4);color:var(--ink-3);font-size:var(--fs-sm)">No physical locations configured yet. Add one above!</div>`
                  : `
                <div class="locations-list">
                  ${locations
                    .map(
                      loc => `
                    <div class="location-row" data-id="${loc.id}">
                      <div class="location-row-main" style="padding-left:calc(var(--s1) + ${loc.depth * 16}px)">
                        <span>${loc.depth === 0 ? "🏠" : "📁"}</span>
                        <span class="location-path">${esc(loc.name)}</span>
                        ${loc.depth > 0 ? `<span style="font-size:var(--fs-xs);color:var(--ink-3)">(${esc(loc.path)})</span>` : ""}
                        <span class="location-count-pill">${loc.copy_count} ${loc.copy_count === 1 ? "copy" : "copies"}</span>
                      </div>
                      <div class="location-actions">
                        <button type="button" class="btn btn-xs btn-ghost btn-loc-edit" data-id="${loc.id}" data-name="${esc(loc.name)}" data-parent="${loc.parent_id || ""}" title="Rename">✏️ Rename</button>
                        <button type="button" class="btn btn-xs btn-ghost btn-loc-delete" data-id="${loc.id}" data-name="${esc(loc.name)}" data-copies="${loc.copy_count}" title="Delete" style="color:var(--danger)">🗑️</button>
                      </div>
                    </div>
                  `
                    )
                    .join("")}
                </div>
              `
              }
            </div>

            <div class="modal-actions" style="margin-top:var(--s2)">
              <button type="button" class="btn btn-ghost" id="btn-modal-cancel">Close</button>
            </div>
          </div>
        </div>
      </div>
    `;

    document.getElementById("btn-modal-close")?.addEventListener("click", closeModal);
    document.getElementById("btn-modal-cancel")?.addEventListener("click", closeModal);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") closeModal();
    });

    // Add location form submit
    document.getElementById("add-location-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const nameInput = document.getElementById("loc-add-name");
      const parentSelect = document.getElementById("loc-add-parent");
      const name = nameInput ? nameInput.value.trim() : "";
      const parent_id = parentSelect && parentSelect.value ? parseInt(parentSelect.value, 10) : null;
      if (!name) return;

      try {
        await api("/admin/api/locations", {
          method: "POST",
          body: { name, parent_id },
        });
        toast(`Location "${name}" added`);
        await openLocationsManagerModal();
      } catch (err) {
        toast(err.message || "Failed to add location");
      }
    });

    // Edit location button
    modalRoot.querySelectorAll(".btn-loc-edit").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const currentName = btn.dataset.name;
        const newName = window.prompt("Rename location:", currentName);
        if (newName === null) return;
        const trimmed = newName.trim();
        if (!trimmed || trimmed === currentName) return;

        try {
          await api(`/admin/api/locations/${id}`, {
            method: "PUT",
            body: { name: trimmed },
          });
          toast("Location updated");
          await openLocationsManagerModal();
        } catch (err) {
          toast(err.message || "Failed to update location");
        }
      });
    });

    // Delete location button
    modalRoot.querySelectorAll(".btn-loc-delete").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const name = btn.dataset.name;
        const copies = parseInt(btn.dataset.copies, 10) || 0;
        const confirmMsg = copies > 0
          ? `Delete "${name}"? This location currently holds ${copies} copies (which will become unassigned), and any child sub-shelves will be deleted.`
          : `Delete "${name}"?`;
        if (!window.confirm(confirmMsg)) return;

        try {
          await api(`/admin/api/locations/${id}`, {
            method: "DELETE",
          });
          toast("Location deleted");
          await openLocationsManagerModal();
        } catch (err) {
          toast(err.message || "Failed to delete location");
        }
      });
    });
  }

  // ================================================================= Initialization
  window.addEventListener("hashchange", handleRoute);
  window.addEventListener("DOMContentLoaded", handleRoute);
})();
