// Matinee — hash router, header, /admin/api/me and handleRoute.
import { api, isAdmin, state } from "./core.js";
import { icon } from "./icons.js";
import { hashWithoutEditorFilters, renderGrid } from "./grid.js";
import { renderDetail } from "./detail.js";
import { renderAdd, stopBarcodeScanner } from "./add.js";
import { closeModal } from "./forms.js";
import { renderStats } from "./stats.js";
import { renderAbout, renderNotFound } from "./about.js";
import { renderAsk } from "./ask.js";
import { openMovieNightModal } from "./movie-night.js";
import { openLocationsManagerModal } from "./locations.js";
import { loadPeople, openPeopleModal } from "./people.js";
import { renderWanted } from "./wishlist.js";

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
    if (segments[0] === "ask") {
      return { path: "/ask", params: {}, query };
    }
    if (segments[0] === "wanted") {
      return { path: "/wanted", params: {}, query };
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
        ${state.me && state.me.ask ? `<a href="#/ask" class="btn btn-sm btn-ghost" id="btn-header-ask" title="Ask about the collection">
          ${icon("chat")} Ask
        </a>` : ""}
        <button type="button" class="btn btn-sm btn-ghost" id="btn-header-movie-night" title="Movie Night Picker">
          ${icon("dice")} Movie Night
        </button>
        <button type="button" class="btn btn-sm btn-ghost" id="btn-header-locations" title="Manage Shelves &amp; Locations">
          ${icon("pin")} Locations
        </button>
        <button type="button" class="btn btn-sm btn-ghost" id="btn-header-people" title="Manage People">
          ${icon("people")} People
        </button>
        <a href="#/wanted" class="btn btn-sm btn-ghost" id="btn-header-wanted" title="Movies we want to buy">
          Wanted
        </a>
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
          ${icon("dice")} Movie Night
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
    document.getElementById("btn-header-people")?.addEventListener("click", () => openPeopleModal());

    // Header admin bar is a swipeable row on phones; mark and reveal the current page's button.
    const activeId = { "/ask": "btn-header-ask", "/wanted": "btn-header-wanted", "/stats": "btn-header-stats", "/add": "btn-header-add" }[state.route?.path];
    const activeBtn = activeId ? document.getElementById(activeId) : null;
    if (activeBtn) activeBtn.setAttribute("aria-current", "page");
    bindScrollRow(nav, activeBtn);

    const footerLinks = document.getElementById("footer-links");
    if (footerLinks) {
      if (isAdmin) {
        footerLinks.innerHTML = `
          <a href="#/">Collection</a> &nbsp;·&nbsp;
          <a href="#/stats">Stats</a> &nbsp;·&nbsp;
          <a href="#/about">About &amp; TMDB Attribution</a> &nbsp;·&nbsp;
          <a href="/${location.hash ? hashWithoutEditorFilters(location.hash) : ""}">Public View</a>
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

  // Horizontal scroll row: toggles .has-more / .has-less so CSS can fade the
  // edge that hides more buttons. Binds listeners once per element.
  function bindScrollRow(el, activeEl) {
    if (!el) return;
    const update = () => {
      const max = el.scrollWidth - el.clientWidth;
      el.classList.toggle("has-more", max > 4 && el.scrollLeft < max - 4);
      el.classList.toggle("has-less", el.scrollLeft > 4);
    };
    if (!el.dataset.scrollRow) {
      el.dataset.scrollRow = "1";
      el.addEventListener("scroll", update, { passive: true });
      window.addEventListener("resize", update);
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(update);
    }
    if (activeEl && el.scrollWidth > el.clientWidth) {
      activeEl.scrollIntoView({ inline: "center", block: "nearest" });
    }
    update();
  }

  function navigate(hash) {
    location.hash = hash;
  }

  // Editor capabilities (e.g. the Ask flag). Plain fetch, not api(): a 401 here
  // just means "no Ask entry points", not a sign-in-expired toast.
  async function loadMe() {
    try {
      const res = await fetch("/admin/api/me", { headers: { Accept: "application/json" } });
      state.me = res.ok ? await res.json() : null;
    } catch {
      state.me = null;
    }
  }
  // Do not let a slow /admin/api/me hold first paint: wait at most 2.5 s, then
  // refresh the header once it lands so the Ask button still appears.
  const mePromise = isAdmin ? loadMe() : Promise.resolve();
  const meReady = isAdmin
    ? Promise.race([mePromise, new Promise((r) => setTimeout(r, 2500))])
    : mePromise;
  mePromise.then(() => {
    if (isAdmin) updateHeader();
  });
  // People (editors only): never awaited, so it cannot hold first paint.
  if (isAdmin) state.peopleReady = loadPeople();

  async function handleRoute() {
    closeModal();
    stopBarcodeScanner();
    const prevRoute = state.route;
    state.route = parseHash(location.hash);
    state.routeSeq += 1;
    const seq = state.routeSeq;
    await meReady;
    if (seq !== state.routeSeq) return;   // a newer hashchange already took over
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
    } else if (state.route.path === "/ask" && isAdmin) {
      if (prevRoute.path === "/") state.grid.scrollY = window.scrollY;
      renderAsk(view);
    } else if (state.route.path === "/wanted") {
      if (prevRoute.path === "/") state.grid.scrollY = window.scrollY;
      await renderWanted(view);
    } else {
      renderNotFound(view);
    }
  }

export { handleRoute, navigate };
