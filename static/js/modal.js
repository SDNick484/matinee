// Matinee — the one implementation of every modal and sheet: a dialog role labelled by its heading, focus moved in and
// trapped, Escape and the close button and a tap on the backdrop all close it, and focus goes back to whatever opened it.
import { esc } from "./core.js";

  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), '
    + 'textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

  const modalStack = [];         // the open modals, oldest first: Tab, Escape and focus belong to the last one
  const closeHooks = [];   // run after any modal closes (forms.js resumes the barcode scanner)
  let serial = 0;

  function tabbables(dialog) {
    return [...dialog.querySelectorAll(FOCUSABLE)]
      .filter((el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden");
  }

  function focusFirst(entry, wanted) {
    const target = typeof wanted === "string" ? entry.dialog.querySelector(wanted) : wanted;
    const el = target || entry.dialog.querySelector(".btn-close") || tabbables(entry.dialog)[0] || entry.dialog;
    el.focus({ preventScroll: true });
  }

  // A hook for work that follows any close: it runs once per modal that is closed, never for one that a newer modal
  // replaced.
  function onModalClose(fn) {
    closeHooks.push(fn);
  }

  // openModal({ title, content, onClose, ... }) puts a dialog on the page and returns { overlay, dialog, close }.
  //   title       the heading's text (escaped here); subtitle a second line under it; iconHTML an icon's markup before the title.
  //   content     HTML (or a node) that follows the heading. An element in it with data-modal-close closes the modal.
  //   onClose     runs once, after the modal is closed by any means (a replaced modal does not run it).
  //   className   classes for the dialog; overlayClass for the backdrop; id for the dialog; titleId for the heading.
  //   bare        no header row and no close button: the heading alone, for a sheet whose buttons all close it.
  //   stacked     lay it over what is open instead of replacing it (the play sheet over Movie Night). Escape closes the
  //               top one only. Without it the modal takes the place of the one in #modal-root, and keeps its opener.
  //   initialFocus  a selector or element to focus; default is the close button.
  function openModal({
    title, subtitle = "", subtitleClass = "modal-sub", iconHTML = "", content = "", onClose = null, className = "",
    overlayClass = "", id = "", titleId = "", bare = false, stacked = false, initialFocus = null, closeLabel = "Close",
  } = {}) {
    const host = stacked ? document.body : document.getElementById("modal-root");
    if (!host) return null;

    let opener = document.activeElement;
    if (!stacked) {
      // One modal in #modal-root at a time, as ever: a new one replaces the old, silently, and inherits its opener.
      for (const old of modalStack.filter((e) => !e.stacked)) {
        if (!opener || opener === document.body || old.overlay.contains(opener)) opener = old.opener;
        old.discard();
      }
    }
    if (opener === document.body || !opener || opener.nodeType !== 1) opener = null;

    const headingId = titleId || `modal-title-${++serial}`;
    const overlay = document.createElement("div");
    overlay.className = `modal-overlay${overlayClass ? ` ${overlayClass}` : ""}`;
    const dialog = document.createElement("div");
    dialog.className = `modal-dialog${className ? ` ${className}` : ""}`;
    if (id) dialog.id = id;
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", headingId);
    dialog.tabIndex = -1;
    const heading = `<h2 class="modal-title" id="${esc(headingId)}">${iconHTML}${iconHTML ? " " : ""}${esc(title)}</h2>`;
    dialog.innerHTML = bare
      ? heading
      : `<div class="modal-header"><div>${heading}${subtitle ? `<div class="${esc(subtitleClass)}">${esc(subtitle)}</div>` : ""}</div>`
        + `<button type="button" class="btn-close" id="btn-modal-close" data-modal-close aria-label="${esc(closeLabel)}">✕</button></div>`;
    if (typeof content === "string") dialog.insertAdjacentHTML("beforeend", content);
    else if (content) dialog.appendChild(content);
    overlay.appendChild(dialog);

    let finished = false;
    const entry = { overlay, dialog, opener, stacked };
    const leave = () => {
      finished = true;
      overlay.remove();
      const at = modalStack.indexOf(entry);
      if (at >= 0) modalStack.splice(at, 1);
    };
    entry.discard = () => { if (!finished) leave(); };
    const close = () => {
      if (finished) return;
      const hadFocus = overlay.contains(document.activeElement);
      leave();
      const now = document.activeElement;
      if (opener && opener.isConnected && (hadFocus || !now || now === document.body)) opener.focus({ preventScroll: true });
      if (onClose) onClose();
      closeHooks.forEach((fn) => fn());
    };
    entry.close = close;

    // A tap on the backdrop closes, but a press that began inside the dialog and ended outside (selecting text) does not.
    let pressedOnBackdrop = false;
    overlay.addEventListener("pointerdown", (e) => { pressedOnBackdrop = e.target === overlay; });
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay ? pressedOnBackdrop : e.target.closest("[data-modal-close]")) close();
    });
    overlay.addEventListener("keydown", (e) => {
      if (e.key !== "Tab") return;
      const items = tabbables(dialog);
      if (!items.length) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const here = document.activeElement;
      if (e.shiftKey && (here === first || here === dialog || !dialog.contains(here))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (here === last || !dialog.contains(here))) {
        e.preventDefault();
        first.focus();
      }
    });

    host.appendChild(overlay);
    modalStack.push(entry);
    focusFirst(entry, initialFocus);
    return { overlay, dialog, close };
  }

  // Close every modal that sits in #modal-root (a route change, a saved form). Stacked sheets stay with their own opener.
  function closeModals() {
    for (const entry of modalStack.filter((e) => !e.stacked)) entry.close();
  }

  // Escape closes the top modal and nothing under it. This is on document, in the bubble phase: after forms.js has
  // judged (on window, in the capture phase) whether the key may leave a title page, before its own handler on window.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !modalStack.length || e.defaultPrevented) return;
    e.stopPropagation();
    e.preventDefault();
    modalStack[modalStack.length - 1].close();
  });

  // Focus that lands behind the top modal (a script, a click through) comes back inside it.
  document.addEventListener("focusin", (e) => {
    const top = modalStack[modalStack.length - 1];
    if (!top || top.overlay.contains(e.target)) return;
    focusFirst(top);
  });

export { closeModals, onModalClose, openModal };
