// Matinee — the page side of the service worker (static/sw.js): registering it, the "Offline, showing the collection
// as of …" line when the worker answered from this phone's copy, and the editor's controls disabled while offline.
// Writes are never queued: the editor's pages need /admin/api, which the worker never keeps, so offline every
// button and field there is disabled until the connection is back.
import { isAdmin } from "./core.js";

  const OFFLINE_CONTROLS = "button, input, select, textarea";
  const OFFLINE_ROOTS = ["view", "modal-root"];
  let observer = null;

  function offlineLine(text) {
    const el = document.getElementById("offline-line");
    if (!el) return;
    el.textContent = text || "";
    el.hidden = !text;
  }

  function asOfText(asOf) {
    const when = new Date(asOf);
    if (!asOf || Number.isNaN(when.getTime())) return "Offline, showing the collection saved on this device";
    return `Offline, showing the collection as of ${when.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
  }

  function disableIn(root) {
    root.querySelectorAll(OFFLINE_CONTROLS).forEach((el) => {
      if (el.disabled) return;
      el.disabled = true;
      el.dataset.offlineDisabled = "1";
    });
  }

  // Editors only: offline, disable every control on the page (and any drawn later) until the connection is back.
  function setEditorOffline(offline) {
    const roots = OFFLINE_ROOTS.map((id) => document.getElementById(id)).filter(Boolean);
    if (offline) {
      roots.forEach(disableIn);
      if (!observer) {
        observer = new MutationObserver((records) => {
          records.forEach((r) => r.addedNodes.forEach((n) => {
            if (n instanceof Element) {
              if (n.matches(OFFLINE_CONTROLS) && !n.disabled) {
                n.disabled = true;
                n.dataset.offlineDisabled = "1";
              }
              disableIn(n);
            }
          }));
        });
        roots.forEach((root) => observer.observe(root, { childList: true, subtree: true }));
      }
      offlineLine("Offline. Nothing can be changed until the connection is back.");
    } else {
      if (observer) observer.disconnect();
      observer = null;
      document.querySelectorAll("[data-offline-disabled]").forEach((el) => {
        el.disabled = false;
        delete el.dataset.offlineDisabled;
      });
      offlineLine("");
    }
  }

  function startOffline() {
    if (isAdmin) {
      window.addEventListener("offline", () => setEditorOffline(true));
      window.addEventListener("online", () => setEditorOffline(false));
      if (!navigator.onLine) setEditorOffline(true);
    }
    if (!("serviceWorker" in navigator)) return;   // plain http by IP, or an old browser: the app works as before
    navigator.serviceWorker.addEventListener("message", (ev) => {
      if (!ev.data || ev.data.type !== "matinee-data" || isAdmin) return;
      offlineLine(ev.data.asOf === null ? "" : asOfText(ev.data.asOf));
    });
    navigator.serviceWorker.startMessages();
    const register = () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });
  }

export { startOffline };
