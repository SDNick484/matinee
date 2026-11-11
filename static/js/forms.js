// Matinee — copy form modal, custom covers, closeModal and global key/click handlers.
import { api, esc, FORMATS, SERVICES, state, toast } from "./core.js";
import { icon } from "./icons.js";
import { navigate } from "./router.js";
import { renderDetail } from "./detail.js";
import { resumeBarcodeScanner } from "./add.js";

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
              <select id="edit-select-copy-location" class="form-select">
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
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;
    const url = URL.createObjectURL(blob);
    const sizeKb = Math.max(1, Math.round(blob.size / 1024));
    modalRoot.innerHTML = `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog cover-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="cover-preview-title">
          <div class="modal-header">
            <div>
              <div class="modal-title" id="cover-preview-title">${icon("image")} New cover</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">${esc(title.title)} · ${sizeKb} KB</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>
          <img class="cover-preview-img" src="${url}" alt="New cover for ${esc(title.title)}">
          <div class="modal-actions">
            <button type="button" class="btn btn-ghost" id="btn-cover-cancel">Cancel</button>
            <button type="button" class="btn" id="btn-cover-upload">Use this cover</button>
          </div>
        </div>
      </div>
    `;
    const done = () => {
      URL.revokeObjectURL(url);
      closeModal();
    };
    document.getElementById("btn-modal-close")?.addEventListener("click", done);
    document.getElementById("btn-cover-cancel")?.addEventListener("click", done);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") done();
    });
    document.getElementById("btn-cover-upload")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "Uploading…";
      try {
        await onUpload();
        done();
      } catch (err) {
        toast(err.message || "Upload failed");
        btn.disabled = false;
        btn.textContent = "Use this cover";
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

export {
  closeModal, downscaleImage, MAX_COVER_BYTES, openCopyModal, openCoverPreviewModal, openEditCopyModal
};
