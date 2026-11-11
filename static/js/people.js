// Matinee — household people: state.people, person dots/toggles, and the People manager modal.
import { api, esc, state, toast } from "./core.js";
import { icon } from "./icons.js";
import { closeModal } from "./forms.js";

  const COLORS = ["green", "blue", "red", "pink", "purple"];

  // Quiet load (plain fetch, not api()): any failure just leaves state.people empty.
  async function loadPeople() {
    try {
      const res = await fetch("/admin/api/people", { headers: { Accept: "application/json" } });
      const data = res.ok ? await res.json() : null;
      state.people = data && Array.isArray(data.people) ? data.people : [];
    } catch {
      state.people = [];
    }
  }

  function activePeople() {
    return (state.people || []).filter((p) => p.active);
  }

  function personInitial(p) {
    return esc((p.name || "?").charAt(0).toUpperCase());
  }

  // 16 px display dots for the people in `ids` (state.people order). `assumed` ("everyone" | "adults", or
  // true for everyone) appends a grey "?" dot and the matching caption.
  function personDotsHTML(ids, { assumed } = {}) {
    const wanted = new Set(ids || []);
    const dots = (state.people || [])
      .filter((p) => wanted.has(p.id))
      .map((p) => `<span class="person-dot person-${esc(p.color)}" role="img" title="${esc(p.name)}" aria-label="${esc(p.name)}, watched">${personInitial(p)}</span>`)
      .join("");
    if (!assumed) return `<span class="person-dots">${dots}</span>`;
    const who = assumed === "adults" ? "adults" : "everyone";
    return `<span class="person-dots">${dots}<span class="person-dot person-unknown" title="Assumed ${who}" aria-hidden="true">?</span><span class="person-assumed">assumed ${who}</span></span>`;
  }

  // One 44 px toggle per active person (plus any inactive person in selectedIds). selectedIds are pressed; groupLabel names the group for screen readers.
  function personTogglesHTML(selectedIds, groupLabel) {
    const on = new Set(selectedIds || []);
    // An inactive person stays visible while they are one of the selected (a past watch they were at).
    const buttons = (state.people || [])
      .filter((p) => p.active || on.has(p.id))
      .map((p) => `<button type="button" class="person-toggle person-${esc(p.color)}" data-person-id="${p.id}" aria-pressed="${on.has(p.id) ? "true" : "false"}" aria-label="${esc(p.name)}" title="${esc(p.name)}">${personInitial(p)}</button>`)
      .join("");
    return `<div class="person-toggles" role="group" aria-label="${esc(groupLabel || "People")}">${buttons}</div>`;
  }

  function readToggles(root) {
    return Array.from(root.querySelectorAll('.person-toggle[aria-pressed="true"]'))
      .map((b) => parseInt(b.dataset.personId, 10))
      .filter((n) => Number.isInteger(n));
  }

  // ================================================================= People manager modal
  function personRowHTML(p, idx, total) {
    const swatches = COLORS.map((c) => `<button type="button" class="people-swatch person-${c}" data-act="color" data-color="${c}" aria-pressed="${p.color === c ? "true" : "false"}" aria-label="Color ${c} for ${esc(p.name)}" title="${c}"></button>`).join("");
    const kindBtn = (k, label) => `<button type="button" class="people-kind${p.kind === k ? " is-on" : ""}" data-act="kind" data-kind="${k}" aria-pressed="${p.kind === k ? "true" : "false"}" aria-label="${label}: ${esc(p.name)}">${label}</button>`;
    const removeBtn = p.watch_count === 0
      ? `<button type="button" class="btn btn-ghost people-btn" data-act="delete" aria-label="Delete ${esc(p.name)}" style="color:var(--danger)">${icon("trash")} Delete</button>`
      : p.active
        ? `<button type="button" class="btn btn-ghost people-btn" data-act="deactivate" aria-label="Deactivate ${esc(p.name)}">Deactivate</button>`
        : `<button type="button" class="btn btn-ghost people-btn" data-act="activate" aria-label="Activate ${esc(p.name)}">Activate</button>`;
    // A person with watches can be deactivated but not deleted; an inactive one with none can still be deleted.
    const activateBtn = p.watch_count === 0 && !p.active
      ? `<button type="button" class="btn btn-ghost people-btn" data-act="activate" aria-label="Activate ${esc(p.name)}">Activate</button>`
      : "";
    return `
      <div class="people-row${p.active ? "" : " is-inactive"}" data-id="${p.id}">
        <div class="people-row-top">
          <span class="person-dot person-${esc(p.color)}" aria-hidden="true">${personInitial(p)}</span>
          <input type="text" class="form-input people-name" value="${esc(p.name)}" maxlength="40" aria-label="Name for ${esc(p.name)}" autocomplete="off" autocorrect="off" spellcheck="false" enterkeyhint="done">
          <span class="people-meta">${p.watch_count} ${p.watch_count === 1 ? "watch" : "watches"}${p.active ? "" : " · inactive"}</span>
        </div>
        <div class="people-row-controls">
          <div class="people-kinds" role="group" aria-label="Adult or kid for ${esc(p.name)}">${kindBtn("adult", "Adult")}${kindBtn("kid", "Kid")}</div>
          <div class="people-swatches" role="group" aria-label="Color for ${esc(p.name)}">${swatches}</div>
        </div>
        <div class="people-row-actions">
          <button type="button" class="btn btn-ghost people-btn people-move" data-act="up" aria-label="Move ${esc(p.name)} up" ${idx === 0 ? "disabled" : ""}>↑</button>
          <button type="button" class="btn btn-ghost people-btn people-move" data-act="down" aria-label="Move ${esc(p.name)} down" ${idx === total - 1 ? "disabled" : ""}>↓</button>
          ${activateBtn}${removeBtn}
        </div>
      </div>`;
  }

  function peopleModalHTML() {
    const people = state.people || [];
    const used = new Set(people.filter((p) => p.active).map((p) => p.color));
    const nextColor = COLORS.find((c) => !used.has(c)) || COLORS[0];
    return `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog people-dialog" style="max-width:640px" role="dialog" aria-modal="true" aria-label="People">
          <div class="modal-header">
            <div>
              <div class="modal-title">${icon("dot")} People</div>
              <div style="font-size:var(--fs-xs);color:var(--ink-3)">Who watches and rates here: name, adult or kid, color, order</div>
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>
          <div style="display:flex;flex-direction:column;gap:var(--s4)">
            <div class="people-list">
              ${people.length ? people.map((p, i) => personRowHTML(p, i, people.length)).join("") : `<div style="text-align:center;padding:var(--s4);color:var(--ink-3);font-size:var(--fs-sm)">No people yet. Add one below.</div>`}
            </div>
            <form id="add-person-form" class="people-add">
              <div style="font-weight:600;font-size:var(--fs-sm)">Add person</div>
              <div class="people-add-grid">
                <input type="text" id="person-add-name" class="form-input" placeholder="First name" maxlength="40" required autocapitalize="words" autocomplete="off" autocorrect="off" spellcheck="false" enterkeyhint="done" aria-label="New person's name">
                <select id="person-add-kind" class="form-select" aria-label="Adult or kid">
                  <option value="adult">Adult</option>
                  <option value="kid">Kid</option>
                </select>
                <select id="person-add-color" class="form-select" aria-label="Color">
                  ${COLORS.map((c) => `<option value="${c}"${c === nextColor ? " selected" : ""}>${c}</option>`).join("")}
                </select>
              </div>
              <button type="submit" class="btn people-btn">＋ Add person</button>
            </form>
            <div class="modal-actions"><button type="button" class="btn btn-ghost people-btn" id="btn-modal-cancel">Close</button></div>
          </div>
        </div>
      </div>`;
  }

  // True once a change in the People modal has been saved; closing the modal then re-renders the current view
  // when it shows people (the grid's Unseen by filters, a title's Who watched row). Other views (Add, Stats,
  // About, Ask) are left alone: re-running their route would reset a half-typed search or the scanner.
  // Reset each time the modal opens.
  let peopleChanged = false;

  function peopleModalOpen() {
    return Boolean(document.querySelector("#modal-root .people-dialog"));
  }

  function closePeopleModal() {
    const wasOpen = peopleModalOpen();
    closeModal();
    if (wasOpen && peopleChanged) {
      peopleChanged = false;
      if (!["/", "/t/:id"].includes(state.route?.path)) return;
      // main.js routes on hashchange; this re-renders the current route in place (no history entry).
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }

  // Escape: forms.js closes any modal on window; this runs first (document) so a change is not lost.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && peopleModalOpen()) closePeopleModal();
  });

  async function renderPeopleModal() {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;
    const prev = modalRoot.querySelector(".modal-dialog");
    const scroll = prev ? prev.scrollTop : 0;
    modalRoot.innerHTML = peopleModalHTML();
    const dialog = modalRoot.querySelector(".modal-dialog");
    if (dialog) dialog.scrollTop = scroll;

    document.getElementById("btn-modal-close")?.addEventListener("click", closePeopleModal);
    document.getElementById("btn-modal-cancel")?.addEventListener("click", closePeopleModal);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") closePeopleModal();
    });

    // Every write goes through send(): a saved one marks the view for a refresh when the modal closes.
    const send = async (path, opts) => {
      const res = await api(path, opts);
      peopleChanged = true;
      return res;
    };
    // Run a change, show the server's message on failure, then reload and re-render either way.
    const change = async (fn, okMsg) => {
      try {
        await fn();
        if (okMsg) toast(okMsg);
      } catch (err) {
        toast(err.message || "Could not save");
      }
      await loadPeople();
      await renderPeopleModal();
    };
    const put = (id, body) => send(`/admin/api/people/${id}`, { method: "PUT", body });

    document.getElementById("add-person-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const name = document.getElementById("person-add-name").value.trim();
      if (!name) return;
      const kind = document.getElementById("person-add-kind").value;
      const color = document.getElementById("person-add-color").value;
      change(() => send("/admin/api/people", { method: "POST", body: { name, kind, color } }), name + " added");
    });

    modalRoot.querySelectorAll(".people-row").forEach((row) => {
      const id = parseInt(row.dataset.id, 10);
      const p = (state.people || []).find((x) => x.id === id);
      if (!p) return;
      row.querySelector(".people-name")?.addEventListener("change", (e) => {
        const name = e.target.value.trim();
        if (!name || name === p.name) { e.target.value = p.name; return; }
        change(() => put(id, { name }), "Renamed");
      });
      row.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const act = btn.dataset.act;
          if (act === "color") change(() => put(id, { color: btn.dataset.color }));
          else if (act === "kind") change(() => put(id, { kind: btn.dataset.kind }));
          else if (act === "deactivate") change(() => put(id, { active: false }), p.name + " deactivated");
          else if (act === "activate") change(() => put(id, { active: true }), p.name + " activated");
          else if (act === "delete") {
            if (!window.confirm("Delete " + p.name + "? This cannot be undone.")) return;
            change(() => send(`/admin/api/people/${id}`, { method: "DELETE" }), p.name + " deleted");
          } else if (act === "up" || act === "down") {
            const list = (state.people || []).slice();
            const i = list.findIndex((x) => x.id === id);
            const j = act === "up" ? i - 1 : i + 1;
            if (j < 0 || j >= list.length) return;
            [list[i], list[j]] = [list[j], list[i]];
            change(async () => {
              for (let k = 0; k < list.length; k++) {
                if (list[k].sort_order !== k) await put(list[k].id, { sort_order: k });
              }
            });
          }
        });
      });
    });
  }

  async function openPeopleModal() {
    peopleChanged = false;
    await loadPeople();
    await renderPeopleModal();
  }

export { activePeople, loadPeople, openPeopleModal, personDotsHTML, personTogglesHTML, readToggles };
