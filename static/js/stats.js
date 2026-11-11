// Matinee — stats view and CSV export link.
import { api, esc, getChipClass, isAdmin, state } from "./core.js";
import { getChipIcon, icon } from "./icons.js";

  // ================================================================= 8.1 Stats View
  async function renderStats(container) {
    const seq = state.routeSeq;
    container.innerHTML = `
      <div class="stats-view">
        <div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading statistics…</div>
      </div>
    `;

    const apiPrefix = isAdmin ? "/admin/api" : "/api";
    let data;
    try {
      data = await api(`${apiPrefix}/stats`);
    } catch (err) {
      if (seq !== state.routeSeq) return;
      container.innerHTML = `
        <div class="stats-view">
          <div class="empty-state">
            <h2>Unable to load stats</h2>
            <p>${esc(err.message || "Failed to connect to Matinee server")}</p>
            <button class="btn" onclick="location.reload()">Retry</button>
          </div>
        </div>
      `;
      return;
    }

    if (seq !== state.routeSeq) return;   // the user left Stats while it loaded
    const ov = data.overview || {};
    const qualities = data.qualities || [];
    const formats = data.formats || [];
    const genres = data.genres || [];
    const services = data.services || [];
    const ws = data.watched_stats || {};
    const rs = data.rating_stats || {};

    const maxGenreCount = Math.max(1, ...genres.map((g) => g.count));

    container.innerHTML = `
      <div class="stats-view">
        <div class="stats-header">
          <div class="stats-header-top">
            <a href="#/" class="back-link">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
              Back to collection
            </a>
            ${isAdmin ? `<a href="/admin/api/export.csv" class="btn btn-sm btn-ghost" id="btn-export-csv" download>${icon("download")} Export CSV</a>` : ""}
          </div>
          <h1 style="margin-top:var(--s2)">Collection Stats</h1>
          <p>Breakdown of movies, formats, qualities, and services in your family library.</p>
        </div>

        <!-- Overview Cards -->
        <section>
          <div class="stats-grid-overview">
            <div class="stat-card">
              <div class="stat-icon">${icon("film")}</div>
              <div class="stat-val">${ov.titles_count ?? 0}</div>
              <div class="stat-lbl">Unique Movies</div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">${icon("disc")}</div>
              <div class="stat-val">${ov.physical_count ?? 0}</div>
              <div class="stat-lbl">Physical Discs</div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">${icon("cloud")}</div>
              <div class="stat-val">${ov.digital_count ?? 0}</div>
              <div class="stat-lbl">Digital Copies</div>
            </div>
            <div class="stat-card">
              <div class="stat-icon">${icon("disc")}${icon("cloud")}</div>
              <div class="stat-val">${ov.dual_count ?? 0}</div>
              <div class="stat-lbl">Dual-Owned</div>
            </div>
          </div>
        </section>

        <!-- Quality Breakdown -->
        <section>
          <h2 class="stats-section-title">Breakdown by Quality</h2>
          <div class="quality-grid">
            ${qualities
              .map((q) => {
                const fillClass = q.quality === "4K" ? "k4" : q.quality === "HD" ? "hd" : "sd";
                return `
                <div class="quality-card">
                  <div class="quality-card-head">
                    <span class="quality-card-title">${esc(q.quality)}</span>
                    <span class="quality-card-count">${q.count} movies (${q.percent}%)</span>
                  </div>
                  <div class="stat-progress-bar">
                    <div class="stat-progress-fill ${fillClass}" style="width: ${Math.min(100, q.percent)}%"></div>
                  </div>
                </div>
              `;
              })
              .join("")}
          </div>
        </section>

        <!-- Formats Breakdown -->
        <section>
          <h2 class="stats-section-title">Breakdown by Format</h2>
          <div class="formats-grid">
            ${formats
              .map((f) => {
                const chipClass = getChipClass(f.format);
                const chipIcon = getChipIcon(f.format);
                return `
                <div class="format-stat-item">
                  <span class="chip ${chipClass}">${chipIcon}${esc(f.format)}</span>
                  <span class="format-count-badge">${f.count}</span>
                </div>
              `;
              })
              .join("")}
          </div>
        </section>

        <!-- Genre Breakdown -->
        <section>
          <h2 class="stats-section-title">Breakdown by Genre</h2>
          ${
            genres.length === 0
              ? `<div style="color:var(--ink-3);font-size:var(--fs-sm)">No genre data recorded.</div>`
              : `
            <div class="genres-list">
              ${genres
                .map((g) => {
                  const pct = Math.round((g.count / maxGenreCount) * 100);
                  return `
                  <div class="genre-row">
                    <div class="genre-row-meta">
                      <span>${esc(g.genre)}</span>
                      <span style="color:var(--ink-2)">${g.count}</span>
                    </div>
                    <div class="genre-bar-track">
                      <div class="genre-bar-fill" style="width: ${pct}%"></div>
                    </div>
                  </div>
                `;
                })
                .join("")}
            </div>
          `
          }
        </section>

        <!-- Digital Services Breakdown -->
        <section>
          <h2 class="stats-section-title">Digital Services</h2>
          ${
            services.length === 0
              ? `<div style="color:var(--ink-3);font-size:var(--fs-sm)">No digital service copies recorded.</div>`
              : `
            <div class="services-grid">
              ${services
                .map(
                  (s) => `
                <div class="service-card">
                  <span class="service-card-name">${esc(s.service)}</span>
                  <span class="format-count-badge">${s.count}</span>
                </div>
              `
                )
                .join("")}
            </div>
          `
          }
        </section>

        <!-- Activity & Ratings Breakdown -->
        <section>
          <h2 class="stats-section-title">Activity &amp; Ratings</h2>
          <div class="stats-activity-grid">
            <div class="stat-activity-card">
              <div class="stat-activity-head">
                <span class="stat-activity-title">Watched Movies</span>
                <span class="stat-activity-metric">${ws.total_watched_titles ?? 0} / ${ov.titles_count ?? 0} (${ws.watched_percent ?? 0}%)</span>
              </div>
              <div class="stat-progress-bar">
                <div class="stat-progress-fill" style="width: ${Math.min(100, ws.watched_percent ?? 0)}%; background: #22c55e;"></div>
              </div>
              <div class="stat-activity-meta">
                <span>${ws.watched_this_year ?? 0} watched this year</span>
                <span>·</span>
                <span>${ws.watched_this_month ?? 0} this month</span>
                <span>·</span>
                <span>${ws.total_watch_events ?? 0} total plays</span>
              </div>
            </div>

            <div class="stat-activity-card">
              <div class="stat-activity-head">
                <span class="stat-activity-title">Family Ratings</span>
                <span class="stat-activity-metric">${rs.average_rating ? `★ ${Number(rs.average_rating).toFixed(1)} / 10` : "No ratings yet"}</span>
              </div>
              <div class="stat-progress-bar">
                <div class="stat-progress-fill" style="width: ${Math.min(100, rs.rated_percent ?? 0)}%; background: var(--amber);"></div>
              </div>
              <div class="stat-activity-meta">
                <span>${rs.rated_titles_count ?? 0} rated (${rs.rated_percent ?? 0}%)</span>
                <span>·</span>
                <span>${rs.high_rated_count ?? 0} rated 8.0+ ★</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    `;
  }

export { renderStats };
