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
      kind: "",
      format: "",
      service: "",
      genre: "",
      sort: "added",
      scrollY: 0,
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
    if (format === "4K UHD" || format === "Digital 4K") return "k4";
    if (format === "Blu-ray" || format === "3D Blu-ray") return "kb";
    if (format === "DVD" || format === "VHS") return "ks";
    if (format === "Digital HD" || format === "Digital SD") return "kd";
    return "ks";
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
        <a href="#/add" class="btn btn-sm" id="btn-header-add">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
          Add Movie
        </a>
      `;
    } else {
      nav.innerHTML = ``;
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
      await renderGrid(view);
    } else if (state.route.path === "/t/:id") {
      // Save scroll position before leaving grid
      if (prevRoute.path === "/") {
        state.grid.scrollY = window.scrollY;
      }
      await renderDetail(view, state.route.params.id);
    } else if (state.route.path === "/add") {
      if (prevRoute.path === "/") {
        state.grid.scrollY = window.scrollY;
      }
      await renderAdd(view);
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
    const kind = state.route.query.get("kind") || "";
    const both = state.route.query.get("both") || "";
    const format = state.route.query.get("format") || "";
    const service = state.route.query.get("service") || "";
    const genre = state.route.query.get("genre") || "";
    const sort = state.route.query.get("sort") || "added";

    state.grid.q = q;
    state.grid.kind = kind;
    state.grid.both = both;
    state.grid.format = format;
    state.grid.service = service;
    state.grid.genre = genre;
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
          <div class="filter-scroll" id="facets-bar">
            <!-- Populated from facets -->
          </div>
          <div>
            <select id="grid-sort" class="sort-select" aria-label="Sort by">
              <option value="added" ${sort === "added" ? "selected" : ""}>Recently Added</option>
              <option value="title" ${sort === "title" ? "selected" : ""}>Title (A–Z)</option>
              <option value="year" ${sort === "year" ? "selected" : ""}>Release Year</option>
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

      renderFacetsBar();
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

  function renderFacetsBar() {
    const bar = document.getElementById("facets-bar");
    if (!bar) return;

    const f = state.grid.facets || {};
    const items = [];

    // All button
    const hasFilters = state.grid.kind || state.grid.both || state.grid.format || state.grid.service || state.grid.genre;
    items.push(`
      <button class="filter-pill ${!hasFilters ? "active" : ""}" data-action="clear-filters">
        All
      </button>
    `);

    // Kinds & Both
    if (f.kinds?.includes("physical") && f.kinds?.includes("digital")) {
      items.push(`
        <button class="filter-pill ${state.grid.kind === "physical" ? "active" : ""}" data-facet="kind" data-val="physical">
          Physical
        </button>
        <button class="filter-pill ${state.grid.kind === "digital" ? "active" : ""}" data-facet="kind" data-val="digital">
          Digital
        </button>
      `);
    }
    if (f.has_both) {
      items.push(`
        <button class="filter-pill ${state.grid.both === "1" ? "active" : ""}" data-facet="both" data-val="1">
          💿+☁️ Disc + Digital
        </button>
      `);
    }

    // Top genres
    (f.genres || []).forEach((g) => {
      items.push(`
        <button class="filter-pill ${state.grid.genre === g ? "active" : ""}" data-facet="genre" data-val="${esc(g)}">
          ${esc(g)}
        </button>
      `);
    });

    // Formats
    (f.formats || []).forEach((fmt) => {
      items.push(`
        <button class="filter-pill ${state.grid.format === fmt ? "active" : ""}" data-facet="format" data-val="${esc(fmt)}">
          ${esc(fmt)}
        </button>
      `);
    });

    // Services
    (f.services || []).forEach((srv) => {
      items.push(`
        <button class="filter-pill ${state.grid.service === srv ? "active" : ""}" data-facet="service" data-val="${esc(srv)}">
          ${esc(srv)}
        </button>
      `);
    });

    bar.innerHTML = items.join("");

    bar.querySelectorAll(".filter-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        if (pill.dataset.action === "clear-filters") {
          const q = new URLSearchParams(state.route.query);
          q.delete("kind");
          q.delete("both");
          q.delete("format");
          q.delete("service");
          q.delete("genre");
          const qStr = q.toString();
          location.hash = qStr ? `#/?${qStr}` : "#/";
          return;
        }

        const facet = pill.dataset.facet;
        const val = pill.dataset.val;
        if (state.grid[facet] === val) {
          applyGridParam(facet, "");
        } else {
          if (facet === "both") {
            const q = new URLSearchParams(state.route.query);
            q.delete("kind");
            q.set("both", val);
            const qStr = q.toString();
            location.hash = qStr ? `#/?${qStr}` : "#/";
          } else if (facet === "kind") {
            const q = new URLSearchParams(state.route.query);
            q.delete("both");
            q.set("kind", val);
            const qStr = q.toString();
            location.hash = qStr ? `#/?${qStr}` : "#/";
          } else {
            applyGridParam(facet, val);
          }
        }
      });
    });
  }

  function renderGridContent() {
    const el = document.getElementById("grid-container");
    if (!el) return;

    const titles = state.grid.titles || [];

    if (titles.length === 0) {
      const q = state.grid.q;
      const hasFilters = state.grid.kind || state.grid.format || state.grid.service || state.grid.genre;

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

            const bothBadge = t.has_both ? `<span class="badge-both">Disc + Digital</span>` : "";
            const chipsHtml = (t.formats || [])
              .map((fmt) => `<span class="chip ${getChipClass(fmt)}">${getChipIcon(fmt)}${esc(fmt)}</span>`)
              .join("");

            return `
              <a href="#/t/${t.id}" class="movie-card" data-id="${t.id}">
                <div class="poster-wrap">
                  ${bothBadge}
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

    container.innerHTML = `
      <div class="detail-view">
        <div>
          <a href="#/" class="back-link" id="btn-back">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            Back to collection
          </a>
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
                  const addedInfo = cp.added_by
                    ? `<span class="copy-meta">Added by ${esc(cp.added_by)}</span>`
                    : "";

                  return `
                    <div class="copy-row" data-copy-id="${cp.id}">
                      <div class="copy-info">
                        <span class="chip ${chipCls}">${getChipIcon(cp.format)} ${esc(label)}</span>
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

    // Hook up Back button to maintain scroll
    document.getElementById("btn-back")?.addEventListener("click", (e) => {
      e.preventDefault();
      history.back();
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
    }
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
          <a href="#/" class="btn">Back to collection</a>
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
      const payload = {
        tmdb_id: formState.tmdb_id,
        kind: formState.kind,
        format: formState.format,
        service: formState.kind === "digital" ? formState.service : null,
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
  function openEditCopyModal(copy, title) {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

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
      try {
        await api(`/admin/api/copies/${copy.id}`, {
          method: "PUT",
          body: {
            kind: formState.kind,
            format: formState.format,
            service: formState.kind === "digital" ? formState.service : null,
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

  // Close modal on Escape key
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeModal();
    }
  });

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

  // ================================================================= Initialization
  window.addEventListener("hashchange", handleRoute);
  window.addEventListener("DOMContentLoaded", handleRoute);
})();
