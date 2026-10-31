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
    const format = state.route.query.get("format") || "";
    const service = state.route.query.get("service") || "";
    const genre = state.route.query.get("genre") || "";
    const sort = state.route.query.get("sort") || "added";

    state.grid.q = q;
    state.grid.kind = kind;
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
    const hasFilters = state.grid.kind || state.grid.format || state.grid.service || state.grid.genre;
    items.push(`
      <button class="filter-pill ${!hasFilters ? "active" : ""}" data-action="clear-filters">
        All
      </button>
    `);

    // Kinds
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
          applyGridParam(facet, val);
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
            const posterHtml = t.poster_file
              ? `<img class="poster-img" src="/images/${esc(t.poster_file)}" loading="lazy" alt="${esc(t.title)}" onerror="this.onerror=null;this.parentElement.innerHTML='<div class=\\'poster-ph\\'>${esc(t.title)}<br>${t.year || ''}</div>';">`
              : `<div class="poster-ph"><span>${esc(t.title)}<br><small style="font-weight:400;color:var(--ink-3)">${t.year || ""}</small></span></div>`;

            const chipsHtml = (t.formats || [])
              .map((fmt) => `<span class="chip ${getChipClass(fmt)}">${esc(fmt)}</span>`)
              .join("");

            return `
              <a href="#/t/${t.id}" class="movie-card" data-id="${t.id}">
                <div class="poster-wrap">
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

    const heroBgStyle = title.backdrop_file
      ? `style="background-image: url('/images/${esc(title.backdrop_file)}');"`
      : "";

    const posterHtml = title.poster_file
      ? `<img src="/images/${esc(title.poster_file)}" alt="${esc(title.title)}" onerror="this.onerror=null;this.parentElement.innerHTML='<div class=\\'poster-ph\\'>${esc(title.title)}</div>';">`
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
                        <span class="chip ${chipCls}">${esc(label)}</span>
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

  // ================================================================= 8.1 / 8.2 Add Flow
  async function renderAdd(container) {
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
          <h1 style="font-size:var(--fs-xl);font-weight:800;margin-top:var(--s2)">Add a Movie</h1>
          <p style="color:var(--ink-2);font-size:var(--fs-sm);margin-top:var(--s1)">
            Search TMDB by title (e.g. "Dune" or "Dune 1984") to find and add copies to your collection.
          </p>
        </div>

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
    `;

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
  async function openCopyModal(movie) {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;

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
            <div style="font-size:var(--fs-xs);color:var(--ink-3)">Checking collection…</div>
          </div>

          <div id="modal-duplicate-warning" style="display:none" class="notice-banner notice-warn">
            Warning: You already own this format.
          </div>

          <form id="copy-form" style="display:flex;flex-direction:column;gap:var(--s4)">
            <div class="form-group">
              <label class="form-label">Kind</label>
              <div class="toggle-group" id="kind-toggle">
                <button type="button" class="toggle-btn active" data-kind="physical">Physical</button>
                <button type="button" class="toggle-btn" data-kind="digital">Digital</button>
              </div>
            </div>

            <div class="form-group">
              <label class="form-label">Format</label>
              <div class="chip-select-grid" id="format-select-grid">
                <!-- Injected based on kind -->
              </div>
            </div>

            <div class="form-group" id="service-group" style="display:none">
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
      kind: "physical",
      format: "Blu-ray",
      service: "",
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
        if (owned.owned && owned.summary) {
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
      if (statusBox) statusBox.innerHTML = ``;
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
          if (res.title_id) {
            navigate(`#/t/${res.title_id}`);
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
