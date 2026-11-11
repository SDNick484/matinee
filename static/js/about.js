// Matinee — about and not-found views.
import { state } from "./core.js";

  // ================================================================= 9.1 About View
  function renderAbout(container) {
    container.innerHTML = `
      <div class="about-view">
        <div>
          <a href="#/" class="back-link">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            Back to collection
          </a>
          <h1 style="font-size:var(--fs-xl);font-weight:800;margin-top:var(--s2)">About Matinee</h1>
          <p style="color:var(--ink-2);margin-top:var(--s2);line-height:1.6">
            Matinee is a self-hosted family movie tracker that brings together our physical discs (4K UHD, Blu-ray, DVD, VHS) and digital libraries (Movies Anywhere, Apple TV, Amazon, and more) into a single unified catalog.
          </p>
        </div>

        <div class="tmdb-attribution">
          <img src="/static/tmdb.svg" alt="TMDB Logo" class="tmdb-logo">
          <p style="font-size:var(--fs-sm);color:var(--ink-2);line-height:1.5">
            This product uses the TMDB API but is not endorsed or certified by TMDB.
          </p>
          <a href="https://www.themoviedb.org/" target="_blank" rel="noopener noreferrer" style="font-size:var(--fs-sm)">
            themoviedb.org
          </a>
        </div>
      </div>
    `;
  }

  // 404 View
  function renderNotFound(container) {
    container.innerHTML = `
      <div class="empty-state">
        <h2>Page not found</h2>
        <p>The page you requested could not be found.</p>
        <a href="#/" class="btn">Back to collection</a>
      </div>
    `;
  }

export { renderAbout, renderNotFound };
