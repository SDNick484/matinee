/**
 * Matinee — phone-first family movie collection browser & catalog manager.
 * Vanilla JS with hash routing: #/, #/t/<id>, #/add, #/about, #/ask (admin).
 */
// Entry point: static/index.html loads this with <script type="module">; render_index() versions
// every module through an import map. The rest of the app lives in the other static/js/ modules.
import { handleRoute } from "./router.js";

  // ================================================================= Initialization
  window.addEventListener("hashchange", handleRoute);
  window.addEventListener("DOMContentLoaded", handleRoute);
