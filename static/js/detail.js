// Matinee — title view: copies, ratings, watches, neighbours (7.3).
import { api, certSlotHtml, esc, formatRuntime, getChipClass, isAdmin, state, toast } from "./core.js";
import {
  BUCKET_LABELS, formatCopyLabel, formatLastWatched, formatReadableDate, formatRelativeDate, getChipIcon,
  icon
} from "./icons.js";
import { navigate } from "./router.js";
import {
  downscaleImage, MAX_COVER_BYTES, openCopyModal, openCoverPreviewModal, openEditCopyModal
} from "./forms.js";

  // ================================================================= 7.3 Detail Page
  async function renderDetail(container, titleId) {
    window.scrollTo({ top: 0, behavior: "instant" });
    container.innerHTML = `
      <div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading movie details…</div>
    `;

    const apiPrefix = isAdmin ? "/admin/api" : "/api";
    let title;
    try {
      title = await api(`${apiPrefix}/titles/${titleId}`);
    } catch (err) {
      if (err.status === 404) {
        container.innerHTML = `
          <div class="empty-state">
            <h2>Movie not found</h2>
            <p>This movie does not exist in the collection.</p>
            <a href="#/" class="btn">Back to collection</a>
          </div>
        `;
      } else {
        container.innerHTML = `
          <div class="empty-state">
            <h2>Unable to load movie</h2>
            <p>${esc(err.message)}</p>
            <a href="#/" class="btn btn-ghost">Back to collection</a>
          </div>
        `;
      }
      return;
    }

    const runtimeStr = formatRuntime(title.runtime);
    const metaParts = [];
    if (title.year) metaParts.push(esc(title.year));
    if (runtimeStr) metaParts.push(esc(runtimeStr));
    if (title.certification || isAdmin) {
      metaParts.push(`<span id="cert-slot">${certSlotHtml(title)}</span>`);
    }
    if (title.tmdb_vote && title.tmdb_vote > 0) {
      metaParts.push(`<span class="score">★ ${title.tmdb_vote.toFixed(1)}</span>`);
    }

    const ratingsHtml = [];
    if (title.omdb) {
      if (title.omdb.imdb_rating !== null && title.omdb.imdb_rating !== undefined) {
        const votesStr = title.omdb.imdb_votes ? ` (${Number(title.omdb.imdb_votes).toLocaleString()} votes)` : "";
        const imdbId = title.omdb.imdb_id || title.imdb_id;
        const imdbUrl = imdbId ? `https://www.imdb.com/title/${encodeURIComponent(imdbId)}/` : null;
        const tag = imdbUrl ? "a" : "span";
        const href = imdbUrl ? `href="${imdbUrl}" target="_blank" rel="noopener noreferrer"` : "";
        ratingsHtml.push(
          `<${tag} ${href} class="rating-badge rating-imdb" title="IMDb Rating${votesStr}">` +
          `<span class="rating-logo">IMDb</span> ★ ${Number(title.omdb.imdb_rating).toFixed(1)}` +
          `</${tag}>`
        );
      }
      if (title.omdb.rotten_tomatoes_score !== null && title.omdb.rotten_tomatoes_score !== undefined) {
        const rtScore = title.omdb.rotten_tomatoes_score;
        const rtIcon = icon("dot", rtScore >= 60 ? "ico-rt-fresh" : "ico-rt-rotten");
        ratingsHtml.push(
          `<span class="rating-badge rating-rt" title="Rotten Tomatoes Tomatometer">` +
          `${rtIcon} ${rtScore}%` +
          `</span>`
        );
      }
      if (title.omdb.metacritic_score !== null && title.omdb.metacritic_score !== undefined) {
        const metaScore = title.omdb.metacritic_score;
        const tierClass = metaScore >= 61 ? "rating-meta-good" : (metaScore >= 40 ? "rating-meta-mixed" : "rating-meta-bad");
        ratingsHtml.push(
          `<span class="rating-badge ${tierClass}" title="Metacritic Metascore">` +
          `<span class="rating-logo">METASCORE</span> ${metaScore}` +
          `</span>`
        );
      }
    }

    const heroBgSrc = title.backdrop_url || (title.backdrop_file ? `/images/${esc(title.backdrop_file)}` : null);
    const heroBgStyle = heroBgSrc
      ? `style="background-image: url('${heroBgSrc}');"`
      : "";

    const posterDetailSrc = title.poster_large_url || title.poster_url || (title.poster_file ? `/images/${esc(title.poster_file)}` : null);
    const posterHtml = posterDetailSrc
      ? `<img src="${posterDetailSrc}" alt="${esc(title.title)}" onerror="this.onerror=null;this.parentElement.innerHTML='<div class=\\'poster-ph\\'>${esc(title.title)}</div>';">`
      : `<div class="poster-ph" style="height:100%">${esc(title.title)}</div>`;

    const copies = title.copies || [];

    // Ensure we have active titles list for adjacent navigation
    if (!state.grid.titles || !state.grid.titles.length) {
      try {
        const titlesData = await api(`${apiPrefix}/titles?sort=title`);
        state.grid.titles = titlesData.titles || [];
      } catch (e) {
        state.grid.titles = [];
      }
    }

    const titlesList = state.grid.titles || [];
    const currentId = Number(titleId);
    const currentIndex = titlesList.findIndex((t) => t.id === currentId);
    const prevMovie = currentIndex > 0 ? titlesList[currentIndex - 1] : null;
    const nextMovie = currentIndex >= 0 && currentIndex < titlesList.length - 1 ? titlesList[currentIndex + 1] : null;

    container.innerHTML = `
      <div class="detail-view">
        <div class="detail-nav-bar">
          <a href="${state.grid.lastHash || "#/"}" class="back-link" id="btn-back">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            Back to collection
          </a>
          <div class="detail-adjacent-nav">
            <a href="${prevMovie ? `#/t/${prevMovie.id}` : "#"}"
               class="nav-arrow-btn ${!prevMovie ? "disabled" : ""}"
               id="btn-prev-movie"
               title="${prevMovie ? `Previous: ${esc(prevMovie.title)} (←)` : "No previous movie"}"
               aria-label="Previous movie"
               ${!prevMovie ? 'aria-disabled="true" tabindex="-1"' : ""}>
              ‹ Prev
            </a>
            <a href="${nextMovie ? `#/t/${nextMovie.id}` : "#"}"
               class="nav-arrow-btn ${!nextMovie ? "disabled" : ""}"
               id="btn-next-movie"
               title="${nextMovie ? `Next: ${esc(nextMovie.title)} (→)` : "No next movie"}"
               aria-label="Next movie"
               ${!nextMovie ? 'aria-disabled="true" tabindex="-1"' : ""}>
              Next ›
            </a>
          </div>
        </div>

        <section class="hero">
          <div class="hero-bg ${title.backdrop_file ? "" : "fallback"}" ${heroBgStyle}></div>
          <div class="hero-body">
            <div class="hero-poster">
              ${posterHtml}
            </div>
            <div class="hero-info">
              <h1 class="hero-title">${esc(title.title)}</h1>
              <div class="meta-line">
                ${metaParts.join(' <span style="opacity:0.4">·</span> ')}
              </div>
              ${
                ratingsHtml.length > 0
                  ? `<div class="ratings-row">${ratingsHtml.join("")}</div>`
                  : ""
              }
              ${
                title.collection_name
                  ? `<div style="font-size:var(--fs-sm);color:var(--amber);font-weight:600">${esc(title.collection_name)}</div>`
                  : ""
              }
              ${
                title.genres && title.genres.length > 0
                  ? `<div class="chips" style="margin-top:var(--s1)">
                      ${title.genres.map((g) => `<span class="chip" style="background:var(--raised);color:var(--ink-2)">${esc(g)}</span>`).join("")}
                    </div>`
                  : ""
              }
              ${
                isAdmin
                  ? `
                <div class="rating-widget" id="rating-widget">
                  <div class="rating-header">
                    <span class="rating-label">Personal Rating</span>
                    <span class="rating-score-display" id="rating-display">
                      ${title.user_rating !== null && title.user_rating !== undefined ? `★ ${Number(title.user_rating).toFixed(1)} <small style="font-size:var(--fs-xs);font-weight:400;color:var(--ink-2)">/ 10</small>` : "Unrated"}
                    </span>
                  </div>
                  <div class="rating-controls">
                    <button type="button" class="btn btn-sm btn-ghost rating-step-btn" id="btn-rating-minus" aria-label="Decrease rating by 0.5">−</button>
                    <input type="range" class="rating-slider" id="rating-slider" min="0" max="10" step="0.5" value="${title.user_rating !== null && title.user_rating !== undefined ? title.user_rating : 7.0}">
                    <button type="button" class="btn btn-sm btn-ghost rating-step-btn" id="btn-rating-plus" aria-label="Increase rating by 0.5">+</button>
                    <button type="button" class="btn btn-sm btn-ghost" id="btn-rating-clear" ${title.user_rating === null || title.user_rating === undefined ? "disabled" : ""}>Clear</button>
                  </div>
                </div>
              `
                  : title.user_rating !== null && title.user_rating !== undefined
                  ? `
                <div class="rating-widget-public">
                  <span class="rating-label">Personal Rating:</span>
                  <span class="badge-rating-detail">★ ${Number(title.user_rating).toFixed(1)} / 10</span>
                </div>
              `
                  : ""
              }
            </div>
          </div>
        </section>

        ${
          title.overview
            ? `
          <section class="detail-section">
            <h3>Overview</h3>
            <p class="overview-text">${esc(title.overview)}</p>
          </section>
        `
            : ""
        }

        <section class="detail-section watch-section">
          <div class="watch-section-header">
            <div>
              <h3 style="margin-bottom:2px">Watch History</h3>
              <div class="last-watched-tag" id="last-watched-tag">
                ${formatLastWatched(title.last_watched, title.watch_count)}
              </div>
            </div>
            ${
              isAdmin
                ? `
              <div class="watch-header-actions">
                <button type="button" class="btn btn-sm" id="btn-watched-today">+ Watched Today</button>
              </div>
            `
                : ""
            }
          </div>

          ${
            isAdmin
              ? `
            <div class="watch-log-form">
              <div class="watch-log-inputs">
                <input type="date" id="input-watch-date" class="watch-date-input" max="${new Date().toISOString().slice(0, 10)}">
                <button type="button" class="btn btn-sm btn-ghost" id="btn-log-watch">Log Date</button>
              </div>
              <div class="watch-bucket-buttons">
                <button type="button" class="btn btn-watch-bucket" data-bucket="past_year">Past Year</button>
                <button type="button" class="btn btn-watch-bucket" data-bucket="1_2_years">1–2 Years</button>
                <button type="button" class="btn btn-watch-bucket" data-bucket="2_5_years">2–5 Years</button>
                <button type="button" class="btn btn-watch-bucket" data-bucket="5_10_years">5–10 Years</button>
                <button type="button" class="btn btn-watch-bucket" data-bucket="over_10_years">10+ Years</button>
                <button type="button" class="btn btn-watch-bucket" data-bucket="unknown">Date Unknown</button>
              </div>
            </div>
          `
              : ""
          }

          ${
            !title.watches || title.watches.length === 0
              ? `<div style="color:var(--ink-2);padding:var(--s2) 0">No recorded watches yet.</div>`
              : `
            <div class="watch-log-list" id="watch-log-list">
              ${title.watches
                .map(
                  (w, idx) => `
                <div class="watch-row ${idx >= 3 ? "watch-row-extra hidden" : ""}" data-watch-id="${w.id}">
                  <div class="watch-row-main" style="display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap">
                    ${
                      w.date_bucket && BUCKET_LABELS[w.date_bucket]
                        ? (w.date_bucket === "unknown"
                            ? `<span class="watch-row-date">${icon("calendar")} Watched (Date unknown)</span><span class="badge badge-subtle">Historical</span>`
                            : `<span class="watch-row-date">${icon("calendar")} Watched ${esc(BUCKET_LABELS[w.date_bucket])}</span><span class="badge badge-subtle">Estimated</span>`
                          )
                        : (w.watched_date
                            ? `<span class="watch-row-date">${icon("calendar")} ${formatReadableDate(w.watched_date)}</span><span class="watch-row-rel">(${formatRelativeDate(w.watched_date)})</span>`
                            : `<span class="watch-row-date">${icon("calendar")} Watched (Date unknown)</span><span class="badge badge-subtle">Historical</span>`
                          )
                    }
                  </div>
                  ${w.notes ? `<div class="watch-row-notes" style="width:100%;font-size:var(--fs-xs);color:var(--ink-2);margin-top:2px;padding-left:4px">${esc(w.notes)}</div>` : ""}
                  ${
                    isAdmin
                      ? `<button type="button" class="btn btn-sm btn-danger btn-delete-watch" data-id="${w.id}" title="Delete watch entry" style="margin-left:auto;padding:2px 8px;font-size:var(--fs-xs)">✕</button>`
                      : ""
                  }
                </div>
              `
                )
                .join("")}
              ${
                title.watches.length > 3
                  ? `<button type="button" class="btn btn-sm btn-ghost watch-toggle-btn" id="btn-toggle-watches">Show all ${title.watches.length} watches</button>`
                  : ""
              }
            </div>
          `
          }
        </section>

        <section class="detail-section">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--s3)">
            <h3 style="margin-bottom:0">Owned Copies (${copies.length})</h3>
            ${
              isAdmin
                ? `<button class="btn btn-sm" id="btn-add-copy">+ Add copy</button>`
                : ""
            }
          </div>

          ${
            copies.length === 0
              ? `<div style="color:var(--ink-2);padding:var(--s2) 0">Not in the collection right now.</div>`
              : `
            <div class="copies-list">
              ${copies
                .map((cp) => {
                  const label = formatCopyLabel(cp);
                  const chipCls = getChipClass(cp.format);
                  const locBadge =
                    cp.kind === "physical" && cp.location_path
                      ? `<span class="copy-location">${icon("pin")}${esc(cp.location_path)}</span>`
                      : "";
                  const addedInfo = cp.added_by
                    ? `<span class="copy-meta">Added by ${esc(cp.added_by)}</span>`
                    : "";

                  return `
                    <div class="copy-row" data-copy-id="${cp.id}">
                      <div class="copy-info">
                        <span class="chip ${chipCls}">${getChipIcon(cp.format)} ${esc(label)}</span>
                        ${locBadge}
                        ${addedInfo}
                      </div>
                      ${
                        isAdmin
                          ? `
                        <div class="copy-actions">
                          <button class="btn btn-sm btn-ghost btn-edit-copy" data-copy="${esc(JSON.stringify(cp))}">Edit</button>
                          <button class="btn btn-sm btn-danger btn-delete-copy" data-id="${cp.id}">Delete</button>
                        </div>
                      `
                          : ""
                      }
                    </div>
                  `;
                })
                .join("")}
            </div>
          `
          }

          ${
            isAdmin
              ? `
            <div class="admin-bar">
              <button class="btn btn-sm btn-ghost" id="btn-refresh-tmdb">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                Refresh from TMDB
              </button>
              <button type="button" class="btn btn-sm btn-ghost" id="btn-change-cover">${icon("image")} Change cover</button>
              <input type="file" id="input-cover" accept="image/jpeg,image/png,image/webp,image/*" hidden>
              ${title.custom_poster
                ? `<button type="button" class="btn btn-sm btn-ghost" id="btn-revert-cover">${icon("undo")} Use TMDB poster</button>`
                : ""}
              <button class="btn btn-sm btn-danger" id="btn-delete-title" style="margin-left:auto">
                Delete title
              </button>
            </div>
          `
              : ""
          }
        </section>
      </div>
    `;

    // Hook up Back button to maintain scroll and active filters
    document.getElementById("btn-back")?.addEventListener("click", (e) => {
      e.preventDefault();
      location.hash = state.grid.lastHash || "#/";
    });

    // Editor controls (7.4)
    if (isAdmin) {
      const bindCertOverride = () => {
        document.getElementById("cert-override")?.addEventListener("change", async (e) => {
          const sel = e.target;
          try {
            const res = await api(`/admin/api/titles/${title.id}/certification`, {
              method: "PUT",
              body: { certification: sel.value === "" ? null : sel.value },
            });
            title.certification = res.certification;
            title.certification_tmdb = res.certification_tmdb;
            title.certification_override = res.certification_override;
            const slot = document.getElementById("cert-slot");
            if (slot) slot.innerHTML = certSlotHtml(title);
            bindCertOverride();
            toast("Certification updated");
          } catch (err) {
            sel.value = title.certification_override || "";
            toast(err.message || "Failed to update certification");
          }
        });
      };
      bindCertOverride();

      document.getElementById("btn-add-copy")?.addEventListener("click", () => {
        openCopyModal({ tmdb_id: title.tmdb_id, title_id: title.id, title: title.title });
      });

      container.querySelectorAll(".btn-edit-copy").forEach((btn) => {
        btn.addEventListener("click", () => {
          const cp = JSON.parse(btn.dataset.copy);
          openEditCopyModal(cp, title);
        });
      });

      container.querySelectorAll(".btn-delete-copy").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const copyId = btn.dataset.id;
          if (!confirm("Delete this copy?")) return;
          try {
            await api(`/admin/api/copies/${copyId}`, { method: "DELETE" });
            toast("Copy deleted");
            await renderDetail(container, titleId);
          } catch (err) {
            toast(err.message || "Failed to delete copy");
          }
        });
      });

      document.getElementById("btn-refresh-tmdb")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-refresh-tmdb");
        if (btn) btn.disabled = true;
        try {
          await api(`/admin/api/titles/${titleId}/refresh`, { method: "POST" });
          toast("Refreshed from TMDB");
          await renderDetail(container, titleId);
        } catch (err) {
          toast(err.message || "Failed to refresh");
        } finally {
          if (btn) btn.disabled = false;
        }
      });

      const coverInput = document.getElementById("input-cover");
      document.getElementById("btn-change-cover")?.addEventListener("click", () => coverInput?.click());
      coverInput?.addEventListener("change", async () => {
        const file = coverInput.files && coverInput.files[0];
        coverInput.value = "";
        if (!file) return;
        const blob = await downscaleImage(file);
        if (blob.size > MAX_COVER_BYTES) {
          toast("That image is over 8 MB. Try a smaller photo.");
          return;
        }
        openCoverPreviewModal(title, blob, async () => {
          const form = new FormData();
          form.append("poster", blob, blob.type === "image/jpeg" ? "cover.jpg" : file.name);
          await api(`/admin/api/titles/${titleId}/poster`, { method: "POST", body: form });
          toast("Cover updated");
          await renderDetail(container, titleId);
        });
      });

      document.getElementById("btn-revert-cover")?.addEventListener("click", async () => {
        if (!confirm(`Remove your cover for "${title.title}" and use the TMDB poster again?`)) return;
        try {
          await api(`/admin/api/titles/${titleId}/poster`, { method: "DELETE" });
          toast("Using the TMDB poster");
          await renderDetail(container, titleId);
        } catch (err) {
          toast(err.message || "Failed to remove cover");
        }
      });

      document.getElementById("btn-delete-title")?.addEventListener("click", async () => {
        if (!confirm(`Delete "${title.title}" and all its copies from the collection?`)) return;
        try {
          await api(`/admin/api/titles/${titleId}`, { method: "DELETE" });
          toast("Title deleted");
          navigate("#/");
        } catch (err) {
          toast(err.message || "Failed to delete title");
        }
      });

      // Rating control listeners
      const slider = document.getElementById("rating-slider");
      const display = document.getElementById("rating-display");
      const clearBtn = document.getElementById("btn-rating-clear");
      const minusBtn = document.getElementById("btn-rating-minus");
      const plusBtn = document.getElementById("btn-rating-plus");

      let currentRating = title.user_rating;
      let saveTimer = null;

      const triggerSave = (val) => {
        currentRating = val;
        if (display) {
          display.innerHTML =
            val !== null && val !== undefined
              ? `★ ${Number(val).toFixed(1)} <small style="font-size:var(--fs-xs);font-weight:400;color:var(--ink-2)">/ 10</small>`
              : "Unrated";
        }
        if (clearBtn) clearBtn.disabled = val === null || val === undefined;
        if (slider && val !== null && val !== undefined) slider.value = val;

        clearTimeout(saveTimer);
        saveTimer = setTimeout(async () => {
          try {
            await api(`${apiPrefix}/titles/${titleId}/rating`, {
              method: "PUT",
              body: { rating: val },
            });
            title.user_rating = val;
            if (state.grid.titles) {
              const gt = state.grid.titles.find((t) => t.id === Number(titleId));
              if (gt) gt.user_rating = val;
            }
            toast(val !== null && val !== undefined ? `Rating saved (★ ${Number(val).toFixed(1)})` : "Rating cleared");
          } catch (e) {
            toast(e.message || "Failed to save rating");
          }
        }, 300);
      };

      slider?.addEventListener("input", (e) => {
        triggerSave(Number(e.target.value));
      });

      minusBtn?.addEventListener("click", () => {
        const base = currentRating !== null && currentRating !== undefined ? currentRating : 7.0;
        const val = Math.max(0.0, Math.round((base - 0.5) * 2) / 2);
        triggerSave(val);
      });

      plusBtn?.addEventListener("click", () => {
        const base = currentRating !== null && currentRating !== undefined ? currentRating : 7.0;
        const val = Math.min(10.0, Math.round((base + 0.5) * 2) / 2);
        triggerSave(val);
      });

      clearBtn?.addEventListener("click", () => {
        triggerSave(null);
      });

      // Watch history listeners
      const inputWatchDate = document.getElementById("input-watch-date");
      const btnLogWatch = document.getElementById("btn-log-watch");

      document.getElementById("btn-watched-today")?.addEventListener("click", async () => {
        try {
          const todayIso = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
          await api(`${apiPrefix}/titles/${titleId}/watches`, {
            method: "POST",
            body: { watched_date: todayIso },
          });
          toast("Marked as watched today!");
          await renderDetail(container, titleId);
        } catch (e) {
          toast(e.message || "Failed to mark as watched");
        }
      });

      btnLogWatch?.addEventListener("click", async () => {
        const val = inputWatchDate?.value?.trim();
        if (!val) {
          toast("Please choose a date");
          return;
        }
        try {
          await api(`${apiPrefix}/titles/${titleId}/watches`, {
            method: "POST",
            body: { watched_date: val },
          });
          toast(`Logged watch date: ${val}`);
          await renderDetail(container, titleId);
        } catch (e) {
          toast(e.message || "Failed to log watch date");
        }
      });

      container.querySelectorAll(".btn-watch-bucket").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const bucket = btn.dataset.bucket;
          const label = BUCKET_LABELS[bucket] || bucket;
          try {
            await api(`${apiPrefix}/titles/${titleId}/watches`, {
              method: "POST",
              body: { date_bucket: bucket },
            });
            toast(`Logged watch: ${label}`);
            await renderDetail(container, titleId);
          } catch (e) {
            toast(e.message || "Failed to log watch");
          }
        });
      });

      container.querySelectorAll(".btn-delete-watch").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const watchId = btn.dataset.id;
          if (!confirm("Remove this watch date?")) return;
          try {
            await api(`${apiPrefix}/titles/${titleId}/watches/${watchId}`, { method: "DELETE" });
            toast("Watch entry removed");
            await renderDetail(container, titleId);
          } catch (e) {
            toast(e.message || "Failed to delete watch entry");
          }
        });
      });
    }

    // Toggle extra watches
    const toggleWatchesBtn = document.getElementById("btn-toggle-watches");
    toggleWatchesBtn?.addEventListener("click", () => {
      const extras = container.querySelectorAll(".watch-row-extra");
      const isHidden = extras[0]?.classList.contains("hidden");
      extras.forEach((el) => el.classList.toggle("hidden", !isHidden));
      toggleWatchesBtn.textContent = isHidden ? "Show fewer" : `Show all ${title.watches.length} watches`;
    });
  }

export { renderDetail };
