// Matinee — constants, shared state and helpers (api, esc, toast, cert and chip helpers).

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
    // Bumped by handleRoute on every navigation. An async view render keeps the value it started with and stops
    // after an await once it has changed, so a late response never overwrites the view the user moved on to.
    routeSeq: 0,
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
    // GET /admin/api/me, fetched once on admin load; null = unknown/failed (no Ask entry points).
    me: null,
    // GET /admin/api/people (editors only); [] for anonymous visitors or on any load error.
    people: [],
    // Promise from the one loadPeople() router.js starts for editors; detail awaits it (free once settled).
    peopleReady: null,
    // AI search thread, JS memory only (a reload clears it). thread alternates {q}/{a};
    // results[k] holds the hydrated rows for the k-th answer.
    ask: { thread: [], results: [], draft: "", busy: false, error: null, turnsLeft: null },
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

  const CERT_CHOICES = ["G", "PG", "PG-13", "R", "NC-17", "NR"];

  function certBadgeHtml(title) {
    if (!title.certification) return "";
    const edited = title.certification_override
      ? ` <span class="cert-edited" title="Overrides TMDB (${esc(title.certification_tmdb || "none")})">edited</span>`
      : "";
    return `<span class="cert-badge">${esc(title.certification)}</span>${edited}`;
  }

  function certSlotHtml(title) {
    if (!isAdmin) return certBadgeHtml(title);
    const opts = [`<option value="">TMDB (${esc(title.certification_tmdb || "none")})</option>`]
      .concat(CERT_CHOICES.map(c => `<option value="${c}"${title.certification_override === c ? " selected" : ""}>${c}</option>`));
    return `${certBadgeHtml(title)} <select class="cert-override" id="cert-override" aria-label="Certification">${opts.join("")}</select>`;
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

  // Money is stored as whole cents and typed as dollars. "24.99", "$24.99" and "1,499" give 2499 and 149900; blank
  // gives null (no price); anything else gives undefined (the form says so instead of guessing). Parsed from the
  // text, not through a float, and clamped one cent past the server's $100,000.00 cap so an absurd amount still
  // reaches the server and gets its own message.
  const PRICE_CLAMP_CENTS = 10_000_001;
  function dollarsToCents(text) {
    const t = String(text ?? "").trim();
    if (!t) return null;
    const m = /^\$?\s*(\d{1,3}(?:,\d{3})+|\d*)(?:\.(\d{1,2}))?$/.exec(t);
    if (!m) return undefined;
    const whole = m[1].replace(/,/g, "");
    const frac = m[2] || "";
    if (!whole && !frac) return undefined; // "$" or "."
    return Math.min(Number(whole || "0") * 100 + Number((frac + "0").slice(0, 2)), PRICE_CLAMP_CENTS);
  }

  // 2499 -> "$24.99", 149900 -> "$1,499.00"; null or a non-number -> "".
  function centsToDollars(cents) {
    if (typeof cents !== "number" || !Number.isFinite(cents)) return "";
    return `$${Math.floor(cents / 100).toLocaleString("en-US")}.${String(cents % 100).padStart(2, "0")}`;
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

export {
  api, centsToDollars, certSlotHtml, dollarsToCents, esc, formatRuntime, FORMATS, getChipClass, isAdmin, SERVICES, state, TMDB_IMG_THUMB,
  toast
};
