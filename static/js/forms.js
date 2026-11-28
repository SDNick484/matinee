// Matinee — copy form modal, custom covers, closeModal and global key/click handlers.
import { api, centsToDollars, closeFilterPopovers, dollarsToCents, esc, FORMATS, redrawIfCurrent, SERVICES, state, toast } from "./core.js";
import { icon } from "./icons.js";
import { navigate } from "./router.js";
import { renderDetail } from "./detail.js";
import { resumeBarcodeScanner } from "./add.js";
import { openWantModal } from "./wishlist.js";
import { closeModals, onModalClose, openModal } from "./modal.js";

  // ================================================================= "More": edition, digital code, date, price
  // One disclosure shared by the add and edit forms (ids start with `p`: "add-" or "edit-"). Closed on a new copy,
  // open on an existing one that already has something in it. The code applies to discs only; edition, date and
  // price apply to every copy.
  const CODE_CHOICES = [["none", "None"], ["unredeemed", "Unredeemed"], ["redeemed", "Redeemed"], ["expired", "Expired"]];

  function moreFieldsHTML(p, v) {
    const filled = v.edition || v.purchased_on || v.price_cents != null || (v.code_status && v.code_status !== "none");
    const price = typeof v.price_cents === "number" ? centsToDollars(v.price_cents).slice(1).replace(/,/g, "") : "";
    return `
      <details class="copy-more" id="${p}more"${filled ? " open" : ""}>
        <summary>More <span class="copy-more-hint">edition, code, date, price</span></summary>
        <div class="copy-more-body">
          <div class="form-group">
            <label class="form-label" for="${p}edition">Edition</label>
            <input type="text" id="${p}edition" class="form-input" maxlength="60" autocomplete="off"
                   placeholder="Steelbook, Criterion Collection…" value="${esc(v.edition || "")}">
          </div>
          <div class="form-group" id="${p}code-group" style="${v.kind === "physical" ? "" : "display:none"}">
            <label class="form-label">Digital code</label>
            <div class="chip-select-grid" id="${p}code-grid"></div>
          </div>
          <div class="copy-more-row">
            <div class="form-group">
              <label class="form-label" for="${p}purchased">Purchased</label>
              <input type="date" id="${p}purchased" class="form-input" value="${esc(v.purchased_on || "")}">
            </div>
            <div class="form-group">
              <label class="form-label" for="${p}price">Price</label>
              <input type="text" id="${p}price" class="form-input" inputmode="decimal" autocomplete="off"
                     placeholder="24.99" value="${esc(price)}">
            </div>
          </div>
        </div>
      </details>`;
  }

  function renderCodeGrid(p, formState) {
    const grid = document.getElementById(`${p}code-grid`);
    if (!grid) return;
    grid.innerHTML = CODE_CHOICES.map(
      ([code, label]) =>
        `<button type="button" class="select-chip ${code === formState.code_status ? "active" : ""}" data-code="${code}">${label}</button>`
    ).join("");
    grid.querySelectorAll(".select-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        formState.code_status = btn.dataset.code;
        renderCodeGrid(p, formState);
      });
    });
  }

  // The code chips follow the Physical/Digital toggle.
  function showCodeGroup(p, kind) {
    const group = document.getElementById(`${p}code-group`);
    if (group) group.style.display = kind === "physical" ? "flex" : "none";
  }

  // The four values for the request body, or null (after saying why) when the price can't be read. A digital copy
  // always sends code_status "none": the server refuses anything else for it.
  function readMoreFields(p, formState) {
    const priceInput = document.getElementById(`${p}price`);
    const price = dollarsToCents(priceInput?.value);
    if (price === undefined) {
      const more = document.getElementById(`${p}more`);
      if (more) more.open = true;
      priceInput?.focus();
      toast("Price must look like 24.99.");
      return null;
    }
    return {
      edition: (document.getElementById(`${p}edition`)?.value || "").trim() || null,
      code_status: formState.kind === "physical" ? formState.code_status : "none",
      purchased_on: document.getElementById(`${p}purchased`)?.value || null,
      price_cents: price,
    };
  }

  function clearMoreFields(p, formState) {
    for (const id of ["edition", "purchased", "price"]) {
      const el = document.getElementById(`${p}${id}`);
      if (el) el.value = "";
    }
    formState.code_status = "none";
    renderCodeGrid(p, formState);
    for (const f of COPY_LINKS) {   // one link belongs to one copy
      const link = document.getElementById(`${p}${f.key}`);
      if (link) link.value = "";
      setLinkError(p, f.key, "");
    }
  }

  // ================================================================= Play links (digital copies only)
  // Two fields in the main part of the form, shown while the kind is Digital, in this order: the Fandango at Home
  // link (the primary way to play), then the YouTube link (spec 2026-11-27-fandango-play-design §5). The server owns
  // the rules (it parses any address of that kind or a bare id and answers 400 or 409 with a sentence that names the
  // service); each field only shows its stored id as a full address and puts its own sentence next to itself.
  const COPY_LINKS = [
    { key: "fandango", label: "Fandango at Home link", placeholder: "Paste the film's Fandango at Home address",
      idKey: "fandango_id", send: "fandango_link", names: /Fandango at Home/,
      full: (id) => `https://athome.fandango.com/content/browse/details/title/${id}` },
    { key: "youtube", label: "YouTube link", placeholder: "Paste the film's YouTube address",
      idKey: "youtube_id", send: "youtube_link", names: /YouTube/,
      full: (id) => `https://www.youtube.com/watch?v=${id}` },
  ];

  function linkFieldsHTML(p, v) {
    return COPY_LINKS.map((f) => {
      const full = v[f.idKey] ? f.full(v[f.idKey]) : "";
      return `
      <div class="form-group" id="${p}${f.key}-group" style="${v.kind === "digital" ? "" : "display:none"}">
        <label class="form-label" for="${p}${f.key}">${f.label}</label>
        <input type="text" id="${p}${f.key}" class="form-input" inputmode="url" autocomplete="off" autocapitalize="off"
               spellcheck="false" placeholder="${esc(f.placeholder)}" value="${esc(full)}">
        <div class="form-field-error" id="${p}${f.key}-error" role="alert" hidden></div>
      </div>`;
    }).join("");
  }

  function showLinkGroups(p, kind) {
    for (const f of COPY_LINKS) {
      const group = document.getElementById(`${p}${f.key}-group`);
      if (group) group.style.display = kind === "digital" ? "" : "none";
    }
  }

  // The request fields: each pasted text (empty clears it) for a digital copy, nothing for a physical one. With
  // `copy` (an edit), a link whose id the copy arrived without is left out, so it is saved without being touched.
  function readCopyLinks(p, kind, copy = null) {
    if (kind !== "digital") return {};
    const out = {};
    for (const f of COPY_LINKS) {
      if (copy && !(f.idKey in copy)) continue;
      out[f.send] = (document.getElementById(`${p}${f.key}`)?.value || "").trim();
    }
    return out;
  }

  function setLinkError(p, key, message) {
    const box = document.getElementById(`${p}${key}-error`);
    if (!box) return;
    box.textContent = message || "";
    box.hidden = !message;
    if (message) document.getElementById(`${p}${key}`)?.focus();
  }

  // True when `err` was about one of the links: shown beside that field, so the caller shows no toast.
  function showLinkError(p, err) {
    if (err?.status !== 400 && err?.status !== 409) return false;
    const field = COPY_LINKS.find((f) => f.names.test(err.message || ""));
    const group = field && document.getElementById(`${p}${field.key}-group`);
    if (!group || group.style.display === "none") return false;   // hidden (a physical copy): a toast says it
    for (const f of COPY_LINKS) setLinkError(p, f.key, "");
    setLinkError(p, field.key, err.message);
    return true;
  }

  function watchLinkFields(p) {
    for (const f of COPY_LINKS) {
      document.getElementById(`${p}${f.key}`)?.addEventListener("input", () => setLinkError(p, f.key, ""));
    }
  }

  // ================================================================= Copy Form Modal (8.2)
  async function openCopyModal(movie, prefill = {}) {
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

    const modal = openModal({
      title: movie.title,
      subtitle: movie.year ? String(movie.year) : "",
      content: `
          <div id="modal-owned-status">
            ${
              prefill.duplicateNotice
                ? `<div class="duplicate-banner">${icon("warn")} In collection: ${esc(prefill.duplicateNotice)}. Adding this will attach your physical disc.</div>`
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
              <select id="select-copy-location" class="form-select">
                <option value="">(Unassigned / No shelf)</option>
                ${locationsList.map((l) => `<option value="${l.id}">${esc(l.path)}</option>`).join("")}
              </select>
            </div>

            ${linkFieldsHTML("add-", { kind: initialKind })}

            ${moreFieldsHTML("add-", { kind: initialKind })}

            <div class="form-actions">
              <button type="button" class="btn btn-ghost" id="btn-modal-cancel" data-modal-close>Cancel</button>
              <button type="button" class="btn btn-ghost" id="btn-want-to-buy" style="display:none">Want to buy…</button>
              <button type="submit" class="btn" id="btn-save-copy">Save Copy</button>
              <button type="button" class="btn btn-ghost" id="btn-save-add-another">Save &amp; Add Another</button>
            </div>
          </form>
      `,
    });
    if (!modal) return;

    // Local form state
    const formState = {
      tmdb_id: movie.tmdb_id,
      kind: initialKind,
      format: initialFormat,
      service: initialService,
      code_status: "none",
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
        showCodeGroup("add-", formState.kind);
        showLinkGroups("add-", formState.kind);
        updateFormatGrid();
        updateServiceGrid();
        checkDuplicateWarning();
      });
    });

    updateFormatGrid();
    updateServiceGrid();
    renderCodeGrid("add-", formState);
    watchLinkFields("add-");

    // "Want to buy…" sits beside Save Copy for a movie Matinee doesn't have yet: it creates the title with no copies
    // plus a want. A movie that is already here (even as an earlier Want to buy) uses the title page's Want….
    function showWantButton(owned) {
      const btn = document.getElementById("btn-want-to-buy");
      if (btn) btn.style.display = owned && !owned.title_id && !movie.title_id ? "" : "none";
    }
    document.getElementById("btn-want-to-buy")?.addEventListener("click", () => {
      openWantModal({ tmdb_id: movie.tmdb_id, title: movie.title, year: movie.year });
    });

    // Bind the form's handlers now, before any request: a tap on Save Copy before the owned-copies answer arrives
    // would otherwise be a native form submit (the page reloads and nothing is saved).
    // The close button, Cancel (data-modal-close), the backdrop and Escape are openModal's, bound already.
    document.getElementById("copy-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      submitCopy(false);
    });

    document.getElementById("btn-save-add-another")?.addEventListener("click", () => {
      submitCopy(true);
    });

    // Check what is already owned from TMDB API
    try {
      const owned = await api(`/admin/api/tmdb/${movie.tmdb_id}/owned`);
      if (!modal.overlay.isConnected) return;   // the form was closed (or saved) before the answer came
      formState.ownedCopies = owned.copies || [];
      showWantButton(owned);
      const statusBox = document.getElementById("modal-owned-status");
      if (statusBox) {
        if (prefill.duplicateNotice) {
          statusBox.innerHTML = `
            <div class="duplicate-banner">${icon("warn")} In collection: ${esc(prefill.duplicateNotice)}. Adding this will attach your physical disc.</div>
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

    // Form submission
    async function submitCopy(andAddAnother = false) {
      const locSelect = document.getElementById("select-copy-location");
      const locVal = locSelect?.value ? parseInt(locSelect.value, 10) : null;
      const more = readMoreFields("add-", formState);
      if (!more) return; // an unreadable price: said so, nothing sent
      const payload = {
        tmdb_id: formState.tmdb_id,
        kind: formState.kind,
        format: formState.format,
        service: formState.kind === "digital" ? formState.service : null,
        location_id: formState.kind === "physical" ? locVal : null,
        ...more,
        ...readCopyLinks("add-", formState.kind),
      };

      const seq = state.routeSeq;
      const saveBtn = document.getElementById("btn-save-copy");
      const addAnotherBtn = document.getElementById("btn-save-add-another");
      if (saveBtn?.disabled) return;   // a save is already out
      if (saveBtn) saveBtn.disabled = true;
      if (addAnotherBtn) addAnotherBtn.disabled = true;

      try {
        const res = await api("/admin/api/copies", {
          method: "POST",
          body: payload,
        });

        toast(res.want_cleared ? "Copy added. It's off the wish list." : "Copy added to collection");

        if (andAddAnother) {
          // Re-fetch owned status and update form
          try {
            const owned = await api(`/admin/api/tmdb/${movie.tmdb_id}/owned`);
            formState.ownedCopies = owned.copies || [];
            showWantButton(owned);
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
          clearMoreFields("add-", formState); // the next copy has its own edition, code, date and price
          if (saveBtn) saveBtn.disabled = false;
          if (addAnotherBtn) addAnotherBtn.disabled = false;
        } else {
          closeModal();
          // Navigate to title detail page
          const targetId = res.id || res.title_id;
          if (targetId && location.hash.split("?")[0] === `#/t/${targetId}`) {
            // already on this title (Add copy from its page): setting the same hash fires no hashchange, so the
            // page would keep showing the old copies and want. Redraw it.
            const view = document.getElementById("view");
            if (view) redrawIfCurrent(seq, () => renderDetail(view, targetId));
          } else if (targetId) {
            navigate(`#/t/${targetId}`);
          } else {
            navigate("#/");
          }
        }
      } catch (err) {
        if (!showLinkError("add-", err)) toast(err.message || "Failed to save copy");
        // Form state survives on error
        if (saveBtn) saveBtn.disabled = false;
        if (addAnotherBtn) addAnotherBtn.disabled = false;
      }
    }
  }

  // Modal for editing an existing copy
  async function openEditCopyModal(copy, title) {
    let locationsList = [];
    try {
      const locData = await api("/api/locations");
      locationsList = locData.locations || [];
    } catch {
      locationsList = [];
    }

    if (!openModal({
      title: "Edit Copy",
      subtitle: title.title,
      content: `
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
              <select id="edit-select-copy-location" class="form-select">
                <option value="">(Unassigned / No shelf)</option>
                ${locationsList.map((l) => `<option value="${l.id}" ${copy.location_id === l.id ? "selected" : ""}>${esc(l.path)}</option>`).join("")}
              </select>
            </div>

            ${linkFieldsHTML("edit-", copy)}

            ${moreFieldsHTML("edit-", copy)}

            <div class="form-actions">
              <button type="button" class="btn btn-ghost" id="btn-modal-cancel" data-modal-close>Cancel</button>
              <button type="submit" class="btn" id="btn-edit-save">Save Changes</button>
            </div>
          </form>
      `,
    })) return;

    const formState = {
      kind: copy.kind,
      format: copy.format,
      service: copy.service || (copy.kind === "digital" ? SERVICES[0] : null),
      code_status: copy.code_status || "none",
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
        showCodeGroup("edit-", formState.kind);
        showLinkGroups("edit-", formState.kind);
        updateFormatGrid();
        updateServiceGrid();
      });
    });

    updateFormatGrid();
    updateServiceGrid();
    renderCodeGrid("edit-", formState);
    watchLinkFields("edit-");

    document.getElementById("edit-copy-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const locSelect = document.getElementById("edit-select-copy-location");
      const locVal = locSelect?.value ? parseInt(locSelect.value, 10) : null;
      const more = readMoreFields("edit-", formState);
      if (!more) return; // an unreadable price: said so, nothing sent
      // The code status, date and price are editor-only: a copy that arrived without them (it never does under
      // /admin) is saved without touching them, so a form that could not show them cannot wipe them.
      const hasDetails = "code_status" in copy;
      if (!hasDetails) {
        delete more.code_status;
        delete more.purchased_on;
        delete more.price_cents;
      }
      // Like the three details above: a copy that arrived without an id is saved without touching it.
      const links = readCopyLinks("edit-", formState.kind, copy);
      const seq = state.routeSeq;
      try {
        await api(`/admin/api/copies/${copy.id}`, {
          method: "PUT",
          body: {
            kind: formState.kind,
            format: formState.format,
            service: formState.kind === "digital" ? formState.service : null,
            location_id: formState.kind === "physical" ? locVal : null,
            ...more,
            ...links,
          },
        });
        toast("Copy updated");
        closeModal();
        const view = document.getElementById("view");
        if (view) redrawIfCurrent(seq, () => renderDetail(view, title.id));
      } catch (err) {
        if (!showLinkError("edit-", err)) toast(err.message || "Failed to update copy");
      }
    });
  }

  // ================================================================= Custom covers
  const MAX_COVER_BYTES = 8 * 1024 * 1024;

  // Phone photos are 3–12 MB; scale the longest side to 1170px (TMDB's w780 poster height)
  // and re-encode as JPEG in the browser so the server needs no image library.
  // Falls back to the original file if the browser can't decode it (e.g. HEIC).
  async function downscaleImage(file, maxSide = 1170, quality = 0.88) {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
      const w = Math.round(bmp.width * scale);
      const h = Math.round(bmp.height * scale);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#16161c"; // transparent PNG areas become the card surface, not black
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(bmp, 0, 0, w, h);
      if (bmp.close) bmp.close();
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
      return blob && blob.size ? blob : file;
    } catch {
      return file;
    }
  }

  function openCoverPreviewModal(title, blob, onUpload) {
    const url = URL.createObjectURL(blob);
    const sizeKb = Math.max(1, Math.round(blob.size / 1024));
    const modal = openModal({
      title: "New cover",
      iconHTML: icon("image"),
      subtitle: `${title.title} · ${sizeKb} KB`,
      className: "cover-preview-dialog",
      titleId: "cover-preview-title",
      onClose: () => URL.revokeObjectURL(url),
      content: `
          <img class="cover-preview-img" src="${url}" alt="New cover for ${esc(title.title)}">
          <div class="modal-actions">
            <button type="button" class="btn btn-ghost" id="btn-cover-cancel" data-modal-close>Cancel</button>
            <button type="button" class="btn" id="btn-cover-upload">Use this cover</button>
          </div>
      `,
    });
    if (!modal) {
      URL.revokeObjectURL(url);
      return;
    }
    document.getElementById("btn-cover-upload")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "Uploading…";
      try {
        await onUpload();
        modal.close();
      } catch (err) {
        toast(err.message || "Upload failed");
        btn.disabled = false;
        btn.textContent = "Use this cover";
      }
    });
  }

  // Closes whatever modal is open (a saved form, a route change); modal.js also closes one on Escape, its close
  // button and a tap on the backdrop, and each of those resumes the barcode scanner through the hook below.
  onModalClose(resumeBarcodeScanner);
  function closeModal() {
    closeModals();
    resumeBarcodeScanner();
  }

  // Escape on a title page with nothing open goes back to the collection, as the Back link does. "Nothing open" is
  // judged here, in the capture phase, before any handler (lists.js on document, the one below) has closed a sheet:
  // an Escape that closes something must not also leave the page.
  let escapeLeavesTitle = false;
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const tag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
    escapeLeavesTitle = state.route?.path === "/t/:id"
      && !(e.altKey || e.ctrlKey || e.metaKey || e.shiftKey)
      && !["input", "textarea", "select"].includes(tag)
      && !document.querySelector("#modal-root > *, .modal-overlay, .filter-popover.open");
  }, true);

  // Close filter popovers on Escape key, or navigate adjacent movies on ArrowLeft/ArrowRight. An open modal takes the
  // Escape first (modal.js stops it here), so this runs when none is open.
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (escapeLeavesTitle) {
        escapeLeavesTitle = false;
        location.hash = state.grid.lastHash || "#/";
        return;
      }
      closeFilterPopovers();
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
    if (!e.target.closest(".filter-popover")) closeFilterPopovers();
  });

export {
  closeModal, downscaleImage, MAX_COVER_BYTES, openCopyModal, openCoverPreviewModal, openEditCopyModal
};
