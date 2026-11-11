// Matinee — locations manager modal.
import { api, esc, toast } from "./core.js";
import { icon } from "./icons.js";
import { closeModal } from "./forms.js";

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
              <div class="modal-title">${icon("pin")} Shelves &amp; Locations</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">Manage physical storage locations (rooms, cabinets, shelves, bins)</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>

          <div style="display:flex;flex-direction:column;gap:var(--s4)">
            <!-- Add New Location Form -->
            <form id="add-location-form" style="display:flex;flex-direction:column;gap:var(--s3);padding:var(--s3);background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-sm)">
              <div style="font-weight:600;font-size:var(--fs-sm)">Add New Shelf / Location</div>
              <div class="loc-form-grid">
                <div class="form-group">
                  <label class="form-label" style="font-size:var(--fs-xs)">Name</label>
                  <input type="text" id="loc-add-name" class="form-input" placeholder="e.g. Media Cabinet, Shelf 2" required autocapitalize="words" enterkeyhint="done" autocomplete="off" autocorrect="off" spellcheck="false">
                </div>
                <div class="form-group">
                  <label class="form-label" style="font-size:var(--fs-xs)">Parent Location (Optional)</label>
                  <select id="loc-add-parent" class="form-select">
                    <option value="">(None - Top Level)</option>
                    ${locations.map(l => `<option value="${l.id}">${esc(l.path)}</option>`).join("")}
                  </select>
                </div>
              </div>
              <div class="loc-form-submit">
                <button type="submit" class="btn" id="btn-loc-submit">＋ Add Location</button>
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
                        <span class="location-icon">${icon(loc.depth === 0 ? "home" : "folder")}</span>
                        <span class="location-path">${esc(loc.name)}</span>
                        ${loc.depth > 0 ? `<span class="location-fullpath">(${esc(loc.path)})</span>` : ""}
                        <span class="location-count-pill">${loc.copy_count} ${loc.copy_count === 1 ? "copy" : "copies"}</span>
                      </div>
                      <div class="location-actions">
                        <button type="button" class="btn btn-xs btn-ghost btn-loc-edit" data-id="${loc.id}" data-name="${esc(loc.name)}" data-parent="${loc.parent_id || ""}" title="Rename" aria-label="Rename ${esc(loc.name)}">${icon("edit")} Rename</button>
                        <button type="button" class="btn btn-xs btn-ghost btn-loc-delete" data-id="${loc.id}" data-name="${esc(loc.name)}" data-copies="${loc.copy_count}" title="Delete" aria-label="Delete ${esc(loc.name)}" style="color:var(--danger)">${icon("trash")}</button>
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

export { openLocationsManagerModal };
