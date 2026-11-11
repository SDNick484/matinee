// Matinee — Ask (AI search) view.
import { api, esc, getChipClass, state } from "./core.js";
import { getChipIcon, icon } from "./icons.js";

  // ================================================================= Ask view
  // #/ask (admin only): a short thread with Claude over the collection. The
  // server hydrates every row from the database; the answer is plain text and
  // always goes through esc(). State lives in JS memory only (state.ask).
  const ASK_MAX_QUESTIONS = 5; // mirrors ask.MAX_QUESTIONS

  function titleRowHTML(t) {
    const id = encodeURIComponent(t.id);
    const src = t.poster_url || (t.poster_file ? `/images/${encodeURIComponent(t.poster_file)}` : "");
    // Placeholder sits under the image; a failed image just removes itself (no text in the handler).
    const thumb = `
      <span class="title-row-thumb">
        <span class="poster-ph" aria-hidden="true">${esc(t.title)}</span>
        ${src ? `<img src="${esc(src)}" alt="" loading="lazy" onerror="this.remove()">` : ""}
      </span>`;
    const chips = (t.formats || [])
      .map((fmt) => `<span class="chip ${getChipClass(fmt)}">${getChipIcon(fmt)}${esc(fmt)}</span>`)
      .join("");
    const locs = (t.copies || [])
      .map((c) =>
        c.kind === "digital"
          ? `<span class="title-row-loc">${icon("cloud")}<span>${esc(c.service || "Digital")}</span></span>`
          : `<span class="title-row-loc">${icon("pin")}<span>${esc(c.location_path || "Unassigned shelf")}</span></span>`
      )
      .join("");
    const year = t.year ? ` · ${esc(t.year)}` : "";
    return `
      <a class="title-row" href="#/t/${id}">
        ${thumb}
        <span class="title-row-main">
          <span class="title-row-title">${esc(t.title)}${year}</span>
          ${chips ? `<span class="chips">${chips}</span>` : ""}
          ${locs}
        </span>
      </a>`;
  }

  function askThreadHTML() {
    const st = state.ask;
    let k = 0;
    const turns = st.thread
      .map((turn) => {
        if ("q" in turn) return `<div class="ask-q">${esc(turn.q)}</div>`;
        const rows = st.results[k++] || [];
        return `
          <div class="ask-turn">
            <p class="ask-a">${esc(turn.a)}</p>
            ${rows.map(titleRowHTML).join("")}
          </div>`;
      })
      .join("");
    const busy = st.busy ? `<div class="ask-status" role="status">Thinking…</div>` : "";
    const err = st.error
      ? `<div class="ask-error" role="alert">
           <span>${icon("warn")} ${esc(st.error)}</span>
           ${[400, 403, 429].includes(st.errorStatus) ? "" : `<button type="button" class="btn btn-sm btn-ghost" id="ask-retry">Retry</button>`}
         </div>`
      : "";
    const empty = !st.thread.length && !busy
      ? `<div class="ask-hint">Ask about the collection: “where's The Dark Knight 4K?”, “something under 100 minutes the girls haven't seen, on disc”.</div>`
      : "";
    return empty + turns + busy + err;
  }

  function renderAskThread() {
    const st = state.ask;
    const thread = document.getElementById("ask-thread");
    const input = document.getElementById("ask-q");
    const send = document.getElementById("ask-send");
    const meta = document.getElementById("ask-meta");
    if (!thread || !input || !send || !meta) return;
    thread.innerHTML = askThreadHTML();
    const left = st.turnsLeft == null ? ASK_MAX_QUESTIONS : st.turnsLeft;
    const done = left <= 0 && !st.error;
    input.disabled = st.busy || done;
    send.disabled = st.busy || done;
    input.placeholder = done ? "Start a new question to keep going" : "Ask about the collection…";
    meta.innerHTML = `<button type="button" class="ask-new" id="ask-new"${st.busy ? " disabled" : ""}>New question</button> · ${left} left`;
    document.getElementById("ask-retry")?.addEventListener("click", () => sendAsk(true));
    document.getElementById("ask-new")?.addEventListener("click", () => {
      state.ask = { thread: [], results: [], draft: "", busy: false, error: null, turnsLeft: null };
      input.value = "";
      renderAskThread();
      input.focus();
    });
  }

  async function sendAsk(retry = false) {
    const st = state.ask;
    if (st.busy) return;
    const last = st.thread[st.thread.length - 1];
    if (!retry) {
      const q = (st.draft || "").trim();
      if (!q) return;
      if (last && "q" in last) st.thread.pop(); // replace a failed, unanswered question
      st.thread.push({ q });
      st.draft = "";
      const input = document.getElementById("ask-q");
      if (input) input.value = "";
    } else if (!last || !("q" in last)) {
      return;
    }
    st.error = null;
    st.errorStatus = null;
    st.busy = true;
    renderAskThread();
    try {
      const res = await api("/admin/api/ask", { method: "POST", body: { thread: st.thread } });
      if (state.ask !== st) return; // "New question" pressed meanwhile
      st.thread.push({ a: String(res.answer || "").slice(0, 1000) || "No answer." });
      st.results.push(Array.isArray(res.items) ? res.items : []);
      if (typeof res.turns_left === "number") st.turnsLeft = res.turns_left;
    } catch (err) {
      if (state.ask !== st) return;
      st.error = err.message || "AI search is unavailable right now, try again";
      st.errorStatus = err.status || null;
    } finally {
      st.busy = false;
      if (state.ask === st && state.route.path === "/ask") {
        renderAskThread();
        const qs = document.querySelectorAll("#ask-thread .ask-q");
        qs[qs.length - 1]?.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    }
  }

  function renderAsk(container) {
    container.innerHTML = `
      <section class="ask-view">
        <h1 class="ask-title">${icon("chat")} Ask</h1>
        <div class="ask-thread" id="ask-thread" aria-live="polite"></div>
        <form class="ask-input" id="ask-form" autocomplete="off">
          <div class="ask-input-row">
            <input type="text" id="ask-q" class="search-input" enterkeyhint="search" maxlength="500" aria-label="Ask about the collection" autocorrect="on" spellcheck="true" value="${esc(state.ask.draft)}">
            <button type="submit" class="btn" id="ask-send" aria-label="Send">${icon("send")}</button>
          </div>
          <div class="ask-meta" id="ask-meta"></div>
        </form>
      </section>
    `;
    const input = document.getElementById("ask-q");
    input.addEventListener("input", () => {
      state.ask.draft = input.value;
    });
    document.getElementById("ask-form").addEventListener("submit", (e) => {
      e.preventDefault();
      state.ask.draft = input.value;
      sendAsk(false);
    });
    renderAskThread();
    if (!state.ask.thread.length) input.focus({ preventScroll: true });
  }

export { renderAsk };
