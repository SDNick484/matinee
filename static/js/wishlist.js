// Matinee — wish list (editors only): the want form, "Add to a wish list…" and the Wanted view.
import { api, esc, FORMATS, getChipClass, isAdmin, state, toast } from "./core.js";
import { getChipIcon } from "./icons.js";
import { navigate } from "./router.js";
import { closeModal } from "./forms.js";

  // ================================================================= Wish list
  const WANT_NOTE_MAX = 140;   // db.NOTE_MAX: the server answers 400 past it

  // "4K UHD" chip, or a plain "Any format" chip for a want with no format.
  function wantChipHTML(fmt) {
    return fmt
      ? `<span class="chip ${getChipClass(fmt)}">${getChipIcon(fmt)} ${esc(fmt)}</span>`
      : `<span class="chip k-any">Any format</span>`;
  }

  function pushedNames(wishes) {
    return [...new Set((wishes || []).map((w) => w.person_name).filter(Boolean))];
  }

  // The editor block on a title page. title.want is null when nothing is wanted. A title with no copies reads
  // "Want to buy"; an owned one reads "Wanted: 4K UHD". "Add to a wish list…" needs a want to push and the
  // Wish Lists app to answer (state.me.wishlist, probed by /admin/api/me).
  function wantSectionHTML(title) {
    const want = title.want;
    const names = pushedNames(title.wishes);
    const pushed = names.length ? `<div class="want-pushed">On ${names.map((n) => esc(n) + "’s").join(", ")} wish list</div>` : "";
    if (!want) {
      return `<div class="want-block" id="want-block">
        <div class="want-row"><span class="want-label">Not on the wish list</span>
          <button type="button" class="btn btn-sm btn-ghost" id="btn-want">Want…</button></div>${pushed}</div>`;
    }
    const label = title.owned ? `Wanted: ${esc(want.wanted_format || "any format")}` : "Want to buy";
    return `<div class="want-block has-want" id="want-block">
      <div class="want-row"><span class="want-label">${label}</span>${title.owned ? "" : wantChipHTML(want.wanted_format)}</div>
      ${want.note ? `<div class="want-note">${esc(want.note)}</div>` : ""}
      ${pushed}
      <div class="want-actions">
        <button type="button" class="btn btn-sm btn-ghost" id="btn-want">Want…</button>
        ${state.me && state.me.wishlist ? `<button type="button" class="btn btn-sm" id="btn-wish">Add to a wish list…</button>` : ""}
        <button type="button" class="btn btn-sm btn-ghost" id="btn-want-remove">Remove</button>
      </div></div>`;
  }

  // Wire the block's buttons; `refresh` redraws the title page after a change.
  function bindWantSection(title, refresh) {
    document.getElementById("btn-want")?.addEventListener("click", () => {
      openWantModal({ title_id: title.id, title: title.title, year: title.year, want: title.want, onSaved: refresh });
    });
    document.getElementById("btn-wish")?.addEventListener("click", () => openWishlistModal(title, refresh));
    document.getElementById("btn-want-remove")?.addEventListener("click", async () => {
      try {
        await api(`/admin/api/titles/${title.id}/want`, { method: "DELETE" });
        toast("Removed from the wish list");
        refresh();
      } catch (err) {
        toast(err.message || "Could not remove the want");
      }
    });
  }

  // The want form: a format (or Any) and an optional note. `movie` names the title by title_id, or by tmdb_id when
  // Matinee doesn't have it yet (the server then creates it with no copies). onSaved(response) runs after a save.
  function openWantModal(movie) {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;
    const formats = [...FORMATS.physical, ...FORMATS.digital];
    let format = movie.want ? movie.want.wanted_format : null;
    modalRoot.innerHTML = `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog">
          <div class="modal-header">
            <div>
              <div class="modal-title">Want to buy</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">${esc(movie.title)}${movie.year ? ` (${esc(movie.year)})` : ""}</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>
          <form id="want-form" style="display:flex;flex-direction:column;gap:var(--s4)">
            <div class="form-group">
              <label class="form-label">Format</label>
              <div class="chip-select-grid" id="want-format-grid"></div>
              <div class="form-hint" style="font-size:var(--fs-xs);color:var(--ink-3)">Any means the first copy you add, in any format, ends the want.</div>
            </div>
            <div class="form-group">
              <label class="form-label" for="want-note">Note</label>
              <input type="text" id="want-note" class="form-input" maxlength="${WANT_NOTE_MAX}" autocomplete="off"
                     placeholder="The Studio Ghibli box, a steelbook…" value="${esc(movie.want ? movie.want.note || "" : "")}">
            </div>
            <div class="form-actions">
              <button type="button" class="btn btn-ghost" id="btn-want-cancel">Cancel</button>
              <button type="submit" class="btn" id="btn-want-save">Save want</button>
            </div>
          </form>
        </div>
      </div>`;

    const grid = document.getElementById("want-format-grid");
    function drawFormats() {
      grid.innerHTML = [[null, "Any"], ...formats.map((f) => [f, f])]
        .map(([f, label]) => `<button type="button" class="select-chip ${f === format ? "active" : ""}" data-fmt="${esc(f || "")}" aria-pressed="${f === format}">${esc(label)}</button>`)
        .join("");
      grid.querySelectorAll(".select-chip").forEach((btn) => {
        btn.addEventListener("click", () => {
          format = btn.dataset.fmt || null;
          drawFormats();
        });
      });
    }
    drawFormats();

    document.getElementById("btn-modal-close")?.addEventListener("click", closeModal);
    document.getElementById("btn-want-cancel")?.addEventListener("click", closeModal);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") closeModal();
    });
    document.getElementById("want-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const save = document.getElementById("btn-want-save");
      if (save) save.disabled = true;
      const body = { format, note: document.getElementById("want-note").value.trim() || null };
      if (movie.title_id) body.title_id = movie.title_id;
      else body.tmdb_id = movie.tmdb_id;
      try {
        const res = await api("/admin/api/wants", { method: "POST", body });
        toast(movie.want ? "Want updated" : "Added to the wish list");
        closeModal();
        if (movie.onSaved) movie.onSaved(res);
        else if (res.id) navigate(`#/t/${res.id}`);
      } catch (err) {
        toast(err.message || "Could not save the want");
        if (save) save.disabled = false;   // the form keeps what was typed
      }
    });
  }

  // Cookbook-Collection's person picker: whose list, plus an optional price and priority.
  async function openWishlistModal(title, refresh) {
    let people;
    try {
      people = (await api("/admin/api/wishlist/people")).people;
    } catch (err) {
      toast(err.message || "The wish list app isn't answering");
      return;
    }
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;
    const dot = (c) => (/^#[0-9a-f]{3,8}$/i.test(c || "") ? `<span class="wish-dot" style="background:${esc(c)}"></span>` : "");
    modalRoot.innerHTML = `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog">
          <div class="modal-header">
            <div>
              <div class="modal-title">Add to a wish list</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">${esc(title.title)}${title.want && title.want.wanted_format ? ` · ${esc(title.want.wanted_format)}` : ""}</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>
          <form id="wish-form" style="display:flex;flex-direction:column;gap:var(--s4)">
            <p class="form-hint" style="margin:0;font-size:var(--fs-xs);color:var(--ink-3)">It goes on that person's list with a shopping link for the title, year and format.</p>
            <div class="wish-people" role="radiogroup" aria-label="Whose wish list">
              ${people.map((p) => `<label class="wish-person"><input type="radio" name="pid" value="${p.id}" required><span>${dot(p.color)}${esc(p.name)}</span></label>`).join("")}
            </div>
            <div class="copy-more-row">
              <div class="form-group"><label class="form-label" for="wish-price">Price (optional)</label>
                <input type="text" id="wish-price" class="form-input" inputmode="decimal" autocomplete="off" placeholder="29.99"></div>
              <div class="form-group"><label class="form-label" for="wish-priority">Priority</label>
                <select id="wish-priority" class="form-select"><option value="1">High</option><option value="2" selected>Normal</option><option value="3">Low</option></select></div>
            </div>
            <div class="form-actions">
              <button type="button" class="btn btn-ghost" id="btn-wish-cancel">Cancel</button>
              <button type="submit" class="btn" id="btn-wish-add">Add</button>
            </div>
          </form>
        </div>
      </div>`;
    document.getElementById("btn-modal-close")?.addEventListener("click", closeModal);
    document.getElementById("btn-wish-cancel")?.addEventListener("click", closeModal);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") closeModal();
    });
    document.getElementById("wish-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const add = document.getElementById("btn-wish-add");
      if (add) add.disabled = true;
      const body = {
        person_id: Number(new FormData(e.target).get("pid")),
        priority: Number(document.getElementById("wish-priority").value),
      };
      const price = document.getElementById("wish-price").value.trim();
      if (price) body.price = price;
      try {
        const res = await api(`/admin/api/titles/${title.id}/wishlist`, { method: "POST", body });
        toast(`Added to ${res.person}’s wish list`);
        closeModal();
        refresh();
      } catch (err) {
        toast(err.message || "Could not add it to the wish list");
        if (add) add.disabled = false;
      }
    });
  }

  // #/wanted: every want as a poster row (editors only; it is not on the dashboard).
  async function renderWanted(container) {
    const seq = state.routeSeq;
    if (!isAdmin) {
      container.innerHTML = `
        <div class="empty-state">
          <h2>Editor Sign-in Required</h2>
          <p>The wish list is only visible to editors.</p>
          <a href="/admin#/wanted" class="btn">Sign In as Editor</a>
        </div>`;
      return;
    }
    container.innerHTML = `<div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading the wish list…</div>`;
    let wants;
    try {
      wants = (await api("/admin/api/wants")).wants;
    } catch (err) {
      if (seq !== state.routeSeq) return;
      container.innerHTML = `<div class="empty-state"><h2>Unable to load the wish list</h2><p>${esc(err.message)}</p></div>`;
      return;
    }
    if (seq !== state.routeSeq) return;
    container.innerHTML = `
      <div class="wanted-view">
        <h1 style="font-size:var(--fs-xl);font-weight:800;margin:var(--s2) 0 var(--s1)">Wanted</h1>
        <p style="color:var(--ink-2);font-size:var(--fs-sm);margin:0">Movies to buy. Adding a copy in the format wanted takes it off the list.</p>
        ${wants.length === 0
          ? `<div class="empty-state" style="margin-top:var(--s4)"><h2>Nothing wanted yet</h2><p>Use Want to buy on a title page, or when you add a movie.</p></div>`
          : `<div class="results-list" id="wanted-list">${wants.map((w) => {
              const thumb = w.poster_url
                ? `<img class="result-thumb" src="${esc(w.poster_url)}" loading="lazy" alt="" onerror="this.style.display='none';">`
                : `<div class="result-thumb poster-ph" style="font-size:10px">${esc(w.title)}</div>`;
              return `<a class="result-card wanted-row" href="#/t/${w.title_id}" data-title-id="${w.title_id}">
                ${thumb}
                <div class="result-info">
                  <div class="result-title">${esc(w.title)}</div>
                  <div class="result-year">${w.year ? esc(w.year) : ""}</div>
                  <div>${wantChipHTML(w.wanted_format)}</div>
                  ${w.note ? `<div class="want-note">${esc(w.note)}</div>` : ""}
                  ${w.owned_formats.length ? `<div class="want-pushed">Owned: ${esc(w.owned_formats.join(", "))}</div>` : ""}
                  ${w.pushed_to.length ? `<div class="want-pushed">On ${w.pushed_to.map((n) => esc(n) + "’s").join(", ")} wish list</div>` : ""}
                </div></a>`;
            }).join("")}</div>`}
      </div>`;
  }

export { bindWantSection, openWantModal, renderWanted, wantSectionHTML };
