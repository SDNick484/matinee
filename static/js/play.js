// Matinee — play a title on the Family Room TV (spec 2026-11-26-play-on-tv-design §6.5): the grid's ▶, the buttons
// on the details page and under the Movie Night pick, the chooser for a title with two ways, and the one request.
// Fandango at Home is the primary way (spec 2026-11-27-fandango-play-design, decision 5): a one-tap place (the grid's
// ▶, Movie Night) starts it at once when the title has a Fandango link, whatever else it has; the details page shows
// every way, Fandango first, and is where the YouTube fallback is.
// Editors only: a title carries `play` only for an editor while HA_PLAY_WEBHOOK_URL is set, and every function here
// draws nothing for a title without a way.
import { api, esc, toast } from "./core.js";
import { icon } from "./icons.js";

  const FAILED = "Couldn't send that. Check the connection and try again.";
  const CONTROLS = "[data-play-title], [data-play-mode], .play-sheet button";

  let requestOut = false;                    // one request at a time across the page
  const wired = new WeakMap();         // root -> its click listener, so a re-render's wirePlay replaces the old one

  const WAY_LABELS = { fandango: "Play on Fandango at Home", youtube: "Play on YouTube", disc: "Start the disc" };

  // The ways a title can play, in the order the details page lists them: "fandango", "youtube", then "disc".
  function playWays(title) {
    const p = title && title.play;
    if (!p) return [];
    const ways = [];
    if (p.fandango) ways.push("fandango");
    if (p.youtube) ways.push("youtube");
    if (Array.isArray(p.disc) && p.disc.length) ways.push("disc");
    return ways;
  }

  // The ways a one-tap place offers: Fandango alone when the title has it, otherwise every way (YouTube and a disc
  // open the chooser in the grid and show both buttons under the Movie Night pick, as before Fandango).
  function oneTapWays(title) {
    const ways = playWays(title);
    return ways.includes("fandango") ? ["fandango"] : ways;
  }

  function discLines(title) {
    const disc = title && title.play && Array.isArray(title.play.disc) ? title.play.disc : [];
    return disc.map((d) => d.line).filter(Boolean);
  }

  function linesHTML(lines) {
    return lines.length ? `<ul class="play-lines">${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "";
  }

  // The ▶ on a poster. "" when the title can't play.
  function playButtonHtml(title) {
    if (!playWays(title).length) return "";
    return `<button type="button" class="play-btn" data-play-title="${Number(title.id)}" aria-label="Play ${esc(title.title)} on the Family Room TV">${icon("play")}</button>`;
  }

  // The buttons for the details page (every way) and Movie Night (`oneTap`: Fandango alone when there is a link):
  // one per way, each starting at once, and the disc lines when the disc is offered.
  function playBarHtml(title, { oneTap = false } = {}) {
    const ways = oneTap ? oneTapWays(title) : playWays(title);
    if (!ways.length) return "";
    const id = Number(title.id);
    const btn = (mode) =>
      `<button type="button" class="btn play-bar-btn" data-play-title="${id}" data-play-mode="${mode}">${icon("play")}<span>${WAY_LABELS[mode]}</span></button>`;
    return `<div class="play-bar" role="group" aria-label="Play on the Family Room TV">
      <div class="play-bar-buttons">${ways.map(btn).join("")}</div>
      ${ways.includes("disc") ? linesHTML(discLines(title)) : ""}
    </div>`;
  }

  function setBusy(on) {
    document.querySelectorAll(CONTROLS).forEach((el) => {
      el.disabled = on;
      if (on) el.setAttribute("aria-busy", "true");
      else el.removeAttribute("aria-busy");
    });
  }

  function closeNote() {
    document.querySelectorAll(".play-note").forEach((n) => n.remove());
  }

  // A message that stays until it is dismissed (a disc: the lines say where to find it).
  function showNote(message, lines) {
    closeNote();
    const note = document.createElement("div");
    note.className = "play-note";
    note.setAttribute("role", "status");
    note.innerHTML = `<div class="play-note-text">${esc(message)}</div>${linesHTML(lines)}
      <button type="button" class="btn btn-sm btn-ghost play-note-dismiss">Dismiss</button>`;
    note.querySelector(".play-note-dismiss").addEventListener("click", closeNote);
    document.body.appendChild(note);
  }

  // Send one play. Resolves with the route's answer, or null when nothing was started (a request is already out,
  // or it failed: the message says why). `attendees` is a list of person ids, a function returning one (read at the
  // moment of the tap), or nothing: with none the server logs by the rating.
  async function startPlay(title, mode, attendees) {
    if (requestOut) return null;
    requestOut = true;
    setBusy(true);
    try {
      const list = typeof attendees === "function" ? attendees() : attendees;
      const body = { mode };
      if (Array.isArray(list) && list.length) body.attendees = list;
      let res;
      try {
        res = await api(`/admin/api/titles/${Number(title.id)}/play`, { method: "POST", body });
      } catch (err) {
        closeNote();
        if (err.status === 401) return null;   // api() already said the sign-in expired
        toast(err.status ? err.message : FAILED);
        return null;
      }
      if (res.mode === "disc") {
        showNote(res.message, (res.disc || []).map((d) => d.line).filter(Boolean));
      } else {
        closeNote();
        toast(res.message);
      }
      return res;
    } finally {
      requestOut = false;
      setBusy(false);
    }
  }

  function closeSheet() {
    document.getElementById("play-sheet-overlay")?.remove();
    document.removeEventListener("keydown", onSheetKey, true);
  }

  function onSheetKey(e) {
    if (e.key !== "Escape") return;
    e.stopPropagation();   // forms.js closes any modal (Movie Night) on window; only the sheet closes here
    closeSheet();
  }

  // The chooser for a title with YouTube and a disc and no Fandango link. Choosing one closes it and sends.
  function openPlaySheet(title, attendees, onDone) {
    if (requestOut || oneTapWays(title).length < 2) return;
    closeSheet();
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay play-sheet-overlay";
    overlay.id = "play-sheet-overlay";
    overlay.innerHTML = `
      <div class="modal-dialog play-sheet" role="dialog" aria-modal="true" aria-label="Play ${esc(title.title)}">
        <h2 class="modal-title">${esc(title.title)}</h2>
        <button type="button" class="btn play-sheet-btn" data-way="youtube">${icon("play")}<span>Play on YouTube</span></button>
        <button type="button" class="btn play-sheet-btn" data-way="disc">${icon("disc")}<span>Start the disc</span></button>
        ${linesHTML(discLines(title))}
        <button type="button" class="btn btn-ghost play-sheet-cancel">Cancel</button>
      </div>`;
    overlay.addEventListener("click", async (e) => {
      if (e.target === overlay || e.target.closest(".play-sheet-cancel")) {
        closeSheet();
        return;
      }
      const way = e.target.closest("[data-way]");
      if (!way || way.disabled) return;
      closeSheet();
      const res = await startPlay(title, way.dataset.way, attendees);
      if (res && onDone) onDone(res, title);
    });
    document.body.appendChild(overlay);
    document.addEventListener("keydown", onSheetKey, true);
    overlay.querySelector('[data-way="youtube"]').focus();
  }

  // Delegated clicks for every [data-play-title] inside `root`. lookup(id) returns the title object. A button with a
  // data-play-mode (the details page and Movie Night) sends that way; the grid's ▶ sends Fandango when the title has
  // it, else its only way, or opens the chooser for YouTube and a disc. onDone(answer, title) runs after a start. The
  // poster's own link must not also open the details page, so the click is always cancelled. Calling it again on one root replaces the earlier listener.
  function wirePlay(root, lookup, attendees, onDone) {
    if (!root) return;
    if (wired.has(root)) root.removeEventListener("click", wired.get(root));
    const onClick = async (e) => {
      const el = e.target.closest("[data-play-title]");
      if (!el || !root.contains(el)) return;
      e.preventDefault();
      e.stopPropagation();
      if (requestOut || el.disabled) return;
      const title = lookup(Number(el.dataset.playTitle));
      if (!title) return;
      let mode = el.dataset.playMode;
      if (!mode) {
        const ways = oneTapWays(title);
        if (ways.length > 1) {
          openPlaySheet(title, attendees, onDone);
          return;
        }
        if (!ways.length) return;
        mode = ways[0];
      }
      const res = await startPlay(title, mode, attendees);
      if (res && onDone) onDone(res, title);
    };
    wired.set(root, onClick);
    root.addEventListener("click", onClick);
  }

export { playBarHtml, playButtonHtml, wirePlay };
