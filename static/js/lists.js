// Matinee — lists (spec 2026-11-12-lists-design §5): chips on the title page, the sheet a chip opens, the editor's
// membership sheet, and the Lists manager in the admin header. Lists never appear on the grid or the dashboard.
import { api, esc, isAdmin, state, toast } from "./core.js";
import { icon } from "./icons.js";
import { closeModal } from "./forms.js";

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MD_RE = /^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/;

  function listsApiBase() {
    return isAdmin ? "/admin/api" : "/api";
  }

  // "11-25" -> "Nov 25"
  function monthDay(md) {
    const m = MD_RE.exec(md || "");
    return m ? `${MONTHS[Number(m[1]) - 1]} ${Number(m[2])}` : "";
  }

  function windowText(l) {
    return l.season_start && l.season_end ? `${monthDay(l.season_start)} – ${monthDay(l.season_end)}` : "";
  }

  // ================================================================= Title page
  // One chip per list the title is on (anyone can tap one to see the list), plus the editor's Lists button.
  function listChipsHTML(title) {
    const lists = Array.isArray(title.lists) ? title.lists : [];
    if (!lists.length && !isAdmin) return "";
    const chips = lists
      .map((l) => `<button type="button" class="chip list-chip${l.in_season ? " is-season" : ""}" data-list-id="${Number(l.id)}" aria-haspopup="dialog" aria-label="${esc(l.name)} list${l.in_season ? ", in season" : ""}">${icon("list")}${esc(l.name)}</button>`)
      .join("");
    const edit = isAdmin
      ? `<button type="button" class="btn btn-sm btn-ghost list-edit-btn" id="btn-title-lists" aria-haspopup="dialog">${icon("list")} Lists</button>`
      : "";
    return `<div class="list-chips" id="list-chips" role="group" aria-label="Lists">${chips}${edit}</div>`;
  }

  // onChange runs after the membership sheet closes having saved something (the title page re-renders).
  function bindListChips(container, title, onChange) {
    container.querySelectorAll(".list-chip").forEach((b) => {
      b.addEventListener("click", () => openListSheet(Number(b.dataset.listId)));
    });
    container.querySelector("#btn-title-lists")?.addEventListener("click", () => openMembershipSheet(title, onChange));
  }

  function sheetHTML(cls, label, title, sub, body) {
    return `
      <div class="modal-overlay" id="modal-overlay">
        <div class="modal-dialog ${cls}" role="dialog" aria-modal="true" aria-label="${esc(label)}">
          <div class="modal-header">
            <div>
              <div class="modal-title">${icon("list")} ${esc(title)}</div>
              ${sub ? `<div class="list-sub">${sub}</div>` : ""}
            </div>
            <button type="button" class="btn-close" id="btn-modal-close" aria-label="Close">✕</button>
          </div>
          ${body}
        </div>
      </div>`;
  }

  function bindClose(onClose) {
    document.getElementById("btn-modal-close")?.addEventListener("click", onClose);
    document.getElementById("btn-modal-cancel")?.addEventListener("click", onClose);
    document.getElementById("modal-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-overlay") onClose();
    });
  }

  // ================================================================= One list (anyone)
  async function openListSheet(listId) {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;
    let data;
    try {
      data = await api(`${listsApiBase()}/lists/${listId}`);
    } catch (err) {
      toast(err.message || "Could not load the list");
      return;
    }
    const titles = data.titles || [];
    const sub = [`${titles.length} ${titles.length === 1 ? "movie" : "movies"}`, windowText(data),
      data.in_season ? "in season" : ""].filter(Boolean).map(esc).join(" · ");
    const rows = titles.length
      ? titles.map((t) => `
          <a class="list-title-row" href="#/t/${Number(t.id)}">
            ${t.poster_url ? `<img class="list-title-poster" src="${esc(t.poster_url)}" alt="" loading="lazy">` : `<span class="list-title-poster" aria-hidden="true"></span>`}
            <span class="list-title-text">
              <span class="list-title-name">${esc(t.title)}</span>
              <span class="list-title-meta">${[t.year ? esc(t.year) : "", t.owned ? "" : "Not in the collection"].filter(Boolean).join(" · ")}</span>
            </span>
          </a>`).join("")
      : `<div class="list-empty">Nothing on this list yet.</div>`;
    modalRoot.innerHTML = sheetHTML("list-dialog", data.name, data.name, sub, `<div class="list-titles">${rows}</div>`);
    bindClose(closeModal);
  }

  // ================================================================= Membership sheet (editors)
  let membershipChanged = false;
  let membershipDone = null;

  function membershipOpen() {
    return Boolean(document.querySelector("#modal-root .list-membership-dialog"));
  }

  function closeMembership() {
    const wasOpen = membershipOpen();
    closeModal();
    if (wasOpen && membershipChanged && membershipDone) {
      membershipChanged = false;
      membershipDone();
    }
  }

  async function openMembershipSheet(title, onChange) {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;
    let data;
    try {
      data = await api("/admin/api/lists");
    } catch (err) {
      toast(err.message || "Could not load lists");
      return;
    }
    membershipChanged = false;
    membershipDone = onChange || null;
    const on = new Set((title.lists || []).map((l) => l.id));
    const rows = (data.lists || []).map((l) => `
      <button type="button" class="list-toggle" data-list-id="${Number(l.id)}" aria-pressed="${on.has(l.id) ? "true" : "false"}">
        <span class="list-toggle-check" aria-hidden="true"></span>
        <span class="list-toggle-name">${esc(l.name)}</span>
        <span class="list-toggle-meta">${esc(windowText(l))}</span>
      </button>`).join("");
    const body = `
      <div class="list-toggles" role="group" aria-label="Lists for ${esc(title.title)}">${rows}</div>
      <div class="modal-actions"><button type="button" class="btn people-btn" id="btn-modal-cancel">Done</button></div>`;
    modalRoot.innerHTML = sheetHTML("list-membership-dialog", `Lists for ${title.title}`, "Lists", esc(title.title), body);
    bindClose(closeMembership);
    modalRoot.querySelectorAll(".list-toggle").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const listId = Number(btn.dataset.listId);
        const want = btn.getAttribute("aria-pressed") !== "true";
        btn.disabled = true;
        try {
          const res = await api(`/admin/api/lists/${listId}/titles/${Number(title.id)}`, { method: want ? "PUT" : "DELETE" });
          btn.setAttribute("aria-pressed", res.on ? "true" : "false");
          title.lists = res.lists;
          membershipChanged = true;
        } catch (err) {
          toast(err.message || "Could not save");
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  // ================================================================= Lists manager (admin header)
  let managerChanged = false;

  function managerOpen() {
    return Boolean(document.querySelector("#modal-root .lists-dialog"));
  }

  function closeManager() {
    const wasOpen = managerOpen();
    closeModal();
    if (wasOpen && managerChanged) {
      managerChanged = false;
      if (state.route?.path !== "/t/:id") return;   // only the title page shows lists
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }

  // Escape: forms.js closes any modal on window; this runs first (document) so a saved change still re-renders.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (membershipOpen()) closeMembership();
    else if (managerOpen()) closeManager();
  });

  function managerRowHTML(l, idx, total) {
    const soon = l.kind === "watch_soon";
    const mdInput = (field, value, label) => `<input type="text" class="form-input lists-md" data-field="${field}" value="${esc(value || "")}" placeholder="MM-DD" maxlength="5" inputmode="numeric" autocomplete="off" aria-label="${label} for ${esc(l.name)}">`;
    return `
      <div class="people-row lists-row" data-id="${Number(l.id)}">
        <div class="people-row-top">
          <input type="text" class="form-input people-name lists-name" value="${esc(l.name)}" maxlength="40" aria-label="Name for ${esc(l.name)}" autocomplete="off" enterkeyhint="done" ${soon ? "readonly" : ""}>
          <span class="people-meta">${l.count} ${l.count === 1 ? "movie" : "movies"}${soon ? " · built in" : ""}${l.in_season ? " · in season" : ""}</span>
        </div>
        ${soon ? "" : `<div class="people-row-controls lists-window" role="group" aria-label="Season for ${esc(l.name)}">${mdInput("season_start", l.season_start, "Season start")}<span aria-hidden="true">to</span>${mdInput("season_end", l.season_end, "Season end")}</div>`}
        <div class="people-row-actions">
          <button type="button" class="btn btn-ghost people-btn" data-act="up" aria-label="Move ${esc(l.name)} up" ${idx === 0 ? "disabled" : ""}>↑</button>
          <button type="button" class="btn btn-ghost people-btn" data-act="down" aria-label="Move ${esc(l.name)} down" ${idx === total - 1 ? "disabled" : ""}>↓</button>
          ${soon ? "" : `<button type="button" class="btn btn-ghost people-btn" data-act="delete" aria-label="Delete ${esc(l.name)}" style="color:var(--danger)">${icon("trash")} Delete</button>`}
        </div>
      </div>`;
  }

  function managerHTML(lists) {
    const body = `
      <div style="display:flex;flex-direction:column;gap:var(--s4)">
        <div class="people-list">${lists.map((l, i) => managerRowHTML(l, i, lists.length)).join("")}</div>
        <form id="add-list-form" class="people-add">
          <div style="font-weight:600;font-size:var(--fs-sm)">Add list</div>
          <div class="people-add-grid lists-add-grid">
            <input type="text" id="list-add-name" class="form-input" placeholder="Christmas" maxlength="40" required autocomplete="off" enterkeyhint="done" aria-label="New list's name">
            <input type="text" id="list-add-start" class="form-input lists-md" placeholder="Start MM-DD" maxlength="5" inputmode="numeric" autocomplete="off" aria-label="Season start (optional)">
            <input type="text" id="list-add-end" class="form-input lists-md" placeholder="End MM-DD" maxlength="5" inputmode="numeric" autocomplete="off" aria-label="Season end (optional)">
          </div>
          <button type="submit" class="btn people-btn">＋ Add list</button>
        </form>
        <div class="modal-actions"><button type="button" class="btn btn-ghost people-btn" id="btn-modal-cancel">Close</button></div>
      </div>`;
    return sheetHTML("lists-dialog", "Lists", "Lists", "Watch Soon, plus seasonal lists that come round each year (MM-DD, may wrap the year)", body);
  }

  async function renderManager() {
    const modalRoot = document.getElementById("modal-root");
    if (!modalRoot) return;
    let lists = [];
    try {
      lists = (await api("/admin/api/lists")).lists || [];
    } catch (err) {
      toast(err.message || "Could not load lists");
    }
    const prev = modalRoot.querySelector(".modal-dialog");
    const scroll = prev ? prev.scrollTop : 0;
    modalRoot.innerHTML = managerHTML(lists);
    const dialog = modalRoot.querySelector(".modal-dialog");
    if (dialog) dialog.scrollTop = scroll;
    bindClose(closeManager);

    const change = async (fn, okMsg) => {
      try {
        await fn();
        managerChanged = true;
        if (okMsg) toast(okMsg);
      } catch (err) {
        toast(err.message || "Could not save");
      }
      await renderManager();
    };
    const put = (id, body) => api(`/admin/api/lists/${id}`, { method: "PUT", body });
    const blankOrMd = (v) => v === "" || MD_RE.test(v);

    document.getElementById("add-list-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const name = document.getElementById("list-add-name").value.trim();
      const start = document.getElementById("list-add-start").value.trim();
      const end = document.getElementById("list-add-end").value.trim();
      if (!name) return;
      const body = { name };
      if (start || end) Object.assign(body, { season_start: start, season_end: end });
      change(() => api("/admin/api/lists", { method: "POST", body }), name + " added");
    });

    modalRoot.querySelectorAll(".lists-row").forEach((row) => {
      const id = Number(row.dataset.id);
      const l = lists.find((x) => x.id === id);
      if (!l) return;
      row.querySelector(".lists-name:not([readonly])")?.addEventListener("change", (e) => {
        const name = e.target.value.trim();
        if (!name || name === l.name) { e.target.value = l.name; return; }
        change(() => put(id, { name }), "Renamed");
      });
      // The window saves once both ends are a valid MM-DD (or both are empty, which clears it).
      row.querySelectorAll(".lists-md").forEach((input) => {
        input.addEventListener("change", () => {
          const start = row.querySelector('[data-field="season_start"]').value.trim();
          const end = row.querySelector('[data-field="season_end"]').value.trim();
          if (!blankOrMd(start) || !blankOrMd(end) || (start === "") !== (end === "")) return;
          if (start === (l.season_start || "") && end === (l.season_end || "")) return;
          change(() => put(id, { season_start: start, season_end: end }), "Season saved");
        });
      });
      row.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const act = btn.dataset.act;
          if (act === "delete") {
            if (!window.confirm(`Delete the ${l.name} list? The movies stay in the collection.`)) return;
            change(() => api(`/admin/api/lists/${id}`, { method: "DELETE" }), l.name + " deleted");
          } else {
            const order = lists.slice();
            const i = order.findIndex((x) => x.id === id);
            const j = act === "up" ? i - 1 : i + 1;
            if (j < 0 || j >= order.length) return;
            [order[i], order[j]] = [order[j], order[i]];
            change(async () => {
              for (let k = 0; k < order.length; k++) {
                if (order[k].sort_order !== k) await put(order[k].id, { sort_order: k });
              }
            });
          }
        });
      });
    });
  }

  async function openListsManagerModal() {
    managerChanged = false;
    await renderManager();
  }

export { bindListChips, listChipsHTML, openListsManagerModal };
