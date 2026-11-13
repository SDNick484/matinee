// Matinee — title view: copies, ratings, watches, neighbours (7.3).
import { api, centsToDollars, certSlotHtml, esc, formatRuntime, getChipClass, isAdmin, state, toast } from "./core.js";
import {
  BUCKET_LABELS, formatCopyLabel, formatLastWatched, formatReadableDate, formatRelativeDate, getChipIcon,
  icon
} from "./icons.js";
import { navigate } from "./router.js";
import { activePeople, personDotsHTML, personTogglesHTML, readToggles } from "./people.js";
import { bindListChips, listChipsHTML } from "./lists.js";
import {
  downscaleImage, MAX_COVER_BYTES, openCopyModal, openCoverPreviewModal, openEditCopyModal
} from "./forms.js";
import { bindWantSection, wantSectionHTML } from "./wishlist.js";

  // ================================================================= 7.3 Detail Page
  // Editor-only line under a copy: the digital code (discs only, and only while it says something), the purchase date
  // and the price. The server never sends these three to a visitor, and only the admin title view calls this.
  const CODE_LABELS = { unredeemed: "Unredeemed code", redeemed: "Redeemed code", expired: "Expired code" };

  function copyExtraHTML(cp) {
    const parts = [];
    if (cp.kind === "physical" && CODE_LABELS[cp.code_status]) parts.push(CODE_LABELS[cp.code_status]);
    if (cp.purchased_on) parts.push(`Bought ${formatReadableDate(cp.purchased_on)}`);
    if (typeof cp.price_cents === "number") parts.push(centsToDollars(cp.price_cents));
    return parts.length ? `<span class="copy-meta copy-extra">${esc(parts.join(" · "))}</span>` : "";
  }

  async function renderDetail(container, titleId) {
    const seq = state.routeSeq;   // see core.js: stop after an await once the user has navigated away
    window.scrollTo({ top: 0, behavior: "instant" });
    container.innerHTML = `
      <div style="text-align:center;padding:48px 16px;color:var(--ink-2)">Loading movie details…</div>
    `;

    const apiPrefix = isAdmin ? "/admin/api" : "/api";
    let title;
    try {
      title = await api(`${apiPrefix}/titles/${titleId}`);
    } catch (err) {
      if (seq !== state.routeSeq) return;
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

    // "Who watched" (editors only, and only once a person exists): otherwise this view is exactly as before.
    if (isAdmin && state.peopleReady) await state.peopleReady;   // started once by router.js
    if (seq !== state.routeSeq) return;
    const people = isAdmin ? activePeople() : [];
    const showWho = people.length > 0;
    const whoLabel = "Who watched";
    const whoRowHTML = () =>
      `<div class="who-watched" id="who-watched-row"><span class="who-watched-label">Who watched</span>${personTogglesHTML(people.map((p) => p.id), whoLabel)}</div>`;
    const watchDatesHTML = (w) =>
      w.date_bucket && BUCKET_LABELS[w.date_bucket]
        ? (w.date_bucket === "unknown"
            ? `<span class="watch-row-date">${icon("calendar")} Watched (Date unknown)</span><span class="badge badge-subtle">Historical</span>`
            : `<span class="watch-row-date">${icon("calendar")} Watched ${esc(BUCKET_LABELS[w.date_bucket])}</span><span class="badge badge-subtle">Estimated</span>`
          )
        : (w.watched_date
            ? `<span class="watch-row-date">${icon("calendar")} ${formatReadableDate(w.watched_date)}</span><span class="watch-row-rel">(${formatRelativeDate(w.watched_date)})</span>`
            : `<span class="watch-row-date">${icon("calendar")} Watched (Date unknown)</span><span class="badge badge-subtle">Historical</span>`
          );
    const watchWhoHTML = (w) => {
      if (!showWho) return "";
      const assumed = w.attendance === "assumed_adults" ? "adults" : w.attendance === "assumed_everyone" ? "everyone" : null;
      return `<button type="button" class="watch-who-btn" aria-expanded="false">${personDotsHTML(w.attendees || [], { assumed })}<span class="sr-only">, change who watched</span></button>`;
    };
    const watchRowHTML = (w, idx) => `
                <div class="watch-row ${idx >= 3 ? "watch-row-extra hidden" : ""}" data-watch-id="${w.id}">
                  <div class="watch-row-main" style="display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap">
                    ${watchDatesHTML(w)}
                    ${watchWhoHTML(w)}
                  </div>
                  ${w.notes ? `<div class="watch-row-notes" style="width:100%;font-size:var(--fs-xs);color:var(--ink-2);margin-top:2px;padding-left:4px">${esc(w.notes)}</div>` : ""}
                  ${
                    isAdmin
                      ? `<button type="button" class="btn btn-sm btn-danger btn-delete-watch" data-id="${w.id}" title="Delete watch entry" style="margin-left:auto;padding:2px 8px;font-size:var(--fs-xs)">✕</button>`
                      : ""
                  }
                </div>
              `;

    // Ensure we have active titles list for adjacent navigation
    if (!state.grid.titles || !state.grid.titles.length) {
      try {
        const titlesData = await api(`${apiPrefix}/titles?sort=title`);
        state.grid.titles = titlesData.titles || [];
      } catch (e) {
        state.grid.titles = [];
      }
    }
    if (seq !== state.routeSeq) return;

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
              ${listChipsHTML(title)}
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
            ${showWho ? whoRowHTML() : ""}
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
                ${showWho ? `<button type="button" class="btn btn-sm" id="btn-log-bucket" hidden>Log</button>` : ""}
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
              ${title.watches.map((w, idx) => watchRowHTML(w, idx))
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
                  const edition = cp.edition ? `<span class="copy-edition">${esc(cp.edition)}</span>` : "";
                  const addedInfo = cp.added_by
                    ? `<span class="copy-meta">Added by ${esc(cp.added_by)}</span>`
                    : "";

                  return `
                    <div class="copy-row" data-copy-id="${cp.id}">
                      <div class="copy-info">
                        <span class="chip ${chipCls}">${getChipIcon(cp.format)} ${esc(label)}</span>
                        ${edition}
                        ${locBadge}
                        ${isAdmin ? copyExtraHTML(cp) : ""}
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

          ${isAdmin ? wantSectionHTML(title) : ""}

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

    // List chips (everyone) and the editor's Lists sheet, which re-renders this page after a change.
    bindListChips(container, title, () => renderDetail(container, titleId));

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

      bindWantSection(title, () => renderDetail(container, titleId));

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
      const whoRow = document.getElementById("who-watched-row");
      // With the Who watched row a bucket button only chooses a bucket (and resets the toggles), so it stays enabled.
      const logButtons = () => [
        document.getElementById("btn-watched-today"),
        btnLogWatch,
        document.getElementById("btn-log-bucket"),
        ...(whoRow ? [] : container.querySelectorAll(".btn-watch-bucket")),
      ].filter(Boolean);
      // With every "Who watched" toggle off there is nobody to log, so every log button is disabled.
      const syncLogButtons = () => {
        const none = !!whoRow && readToggles(whoRow).length === 0;
        logButtons().forEach((b) => { b.disabled = none; });
      };
      // Who a log records when the page sends no attendees (app.default_attendees): every active person, or only
      // the active adults for a watch over ten years old, the "10+ Years" bucket (ruling R21) or a date before
      // today minus ten years (decision D2). The page sends `attendees` only when the toggles differ from that
      // default, so an untouched row leaves the choice to the server; absent entirely when no people exist.
      // The cutoff uses the UTC date, as the server does.
      const tenYearsAgo = (() => {
        const [y, m, d] = new Date().toISOString().slice(0, 10).split("-");
        return m === "02" && d === "29" ? `${Number(y) - 10}-03-01` : `${Number(y) - 10}-${m}-${d}`;
      })();
      const isOld = ({ bucket, date } = {}) => bucket === "over_10_years" || Boolean(date && date < tenYearsAgo);
      const defaultIds = (when) => people.filter((p) => !isOld(when) || p.kind === "adult").map((p) => p.id);
      const attendeesBody = (when) => {
        if (!whoRow) return {};
        const picked = readToggles(whoRow);
        const def = defaultIds(when);
        const same = picked.length === def.length && def.every((id) => picked.includes(id));
        return same ? {} : { attendees: picked };
      };
      const setToggles = (ids) => {
        whoRow?.querySelectorAll(".person-toggle").forEach((t) => {
          t.setAttribute("aria-pressed", ids.includes(parseInt(t.dataset.personId, 10)) ? "true" : "false");
        });
        syncLogButtons();
      };
      whoRow?.addEventListener("click", (e) => {
        const t = e.target.closest(".person-toggle");
        if (!t) return;
        t.setAttribute("aria-pressed", t.getAttribute("aria-pressed") === "true" ? "false" : "true");
        syncLogButtons();
      });

      document.getElementById("btn-watched-today")?.addEventListener("click", async () => {
        try {
          const todayIso = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
          await api(`${apiPrefix}/titles/${titleId}/watches`, {
            method: "POST",
            // The live flag is what lets Home Assistant hear about it; Log Date and MCP never send it.
            body: { watched_date: todayIso, live: true, ...attendeesBody({ date: todayIso }) },
          });
          toast("Marked as watched today!");
          await renderDetail(container, titleId);
        } catch (e) {
          toast(e.message || "Failed to mark as watched");
        }
      });

      // Picking a date more than ten years back switches the kids off (decision D2); back inside ten years, everyone.
      // The toggles are reset only when that changes, so a correction made for one recent date survives another.
      let dateIsOld = false;
      inputWatchDate?.addEventListener("input", () => {
        const old = isOld({ date: inputWatchDate.value });
        if (old === dateIsOld) return;
        dateIsOld = old;
        setToggles(defaultIds({ date: inputWatchDate.value }));
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
            body: { watched_date: val, ...attendeesBody({ date: val }) },
          });
          toast(`Logged watch date: ${val}`);
          await renderDetail(container, titleId);
        } catch (e) {
          toast(e.message || "Failed to log watch date");
        }
      });

      const logBucket = async (bucket) => {
        const label = BUCKET_LABELS[bucket] || bucket;
        try {
          await api(`${apiPrefix}/titles/${titleId}/watches`, {
            method: "POST",
            body: { date_bucket: bucket, ...attendeesBody({ bucket }) },
          });
          toast(`Logged watch: ${label}`);
          await renderDetail(container, titleId);
        } catch (e) {
          toast(e.message || "Failed to log watch");
        }
      };
      // Without people a bucket logs on one tap, as before. With the Who watched row a bucket is chosen first: the
      // toggles switch to that bucket's default (kids off for "10+ Years"), the editor can change them, then Log.
      const btnLogBucket = document.getElementById("btn-log-bucket");
      let chosenBucket = null;
      container.querySelectorAll(".btn-watch-bucket").forEach((btn) => {
        btn.addEventListener("click", () => {
          const bucket = btn.dataset.bucket;
          if (!whoRow) {
            logBucket(bucket);
            return;
          }
          chosenBucket = chosenBucket === bucket ? null : bucket;
          container.querySelectorAll(".btn-watch-bucket").forEach((b) => {
            b.setAttribute("aria-pressed", b.dataset.bucket === chosenBucket ? "true" : "false");
          });
          setToggles(defaultIds({ bucket: chosenBucket }));
          if (btnLogBucket) {
            btnLogBucket.hidden = !chosenBucket;
            btnLogBucket.textContent = chosenBucket ? `Log ${BUCKET_LABELS[chosenBucket] || chosenBucket}` : "Log";
          }
        });
      });
      btnLogBucket?.addEventListener("click", () => {
        if (chosenBucket) logBucket(chosenBucket);
      });

      // Inline "who watched" editor on a saved watch: tap the dots, adjust, Save (PUT) or Cancel.
      const openWhoEditor = (row, w) => {
        if (row.querySelector(".watch-who-editor")) return;
        const editor = document.createElement("div");
        editor.className = "watch-who-editor";
        editor.innerHTML = `
          ${personTogglesHTML(w.attendees || [], "Who watched this time")}
          <div class="watch-who-actions">
            <button type="button" class="btn btn-sm watch-who-save">Save</button>
            <button type="button" class="btn btn-sm btn-ghost watch-who-cancel">Cancel</button>
          </div>
          <div class="watch-who-error" role="alert"></div>`;
        row.classList.add("is-editing");
        row.querySelector(".watch-who-btn")?.setAttribute("aria-expanded", "true");
        row.appendChild(editor);
        const save = editor.querySelector(".watch-who-save");
        const syncSave = () => { save.disabled = readToggles(editor).length === 0; };
        editor.querySelector(".person-toggles").addEventListener("click", (e) => {
          const t = e.target.closest(".person-toggle");
          if (!t) return;
          t.setAttribute("aria-pressed", t.getAttribute("aria-pressed") === "true" ? "false" : "true");
          syncSave();
        });
        editor.querySelector(".watch-who-cancel").addEventListener("click", () => refreshRow(row, w));
        save.addEventListener("click", async () => {
          save.disabled = true;
          try {
            const res = await api(`${apiPrefix}/titles/${titleId}/watches/${w.id}/attendees`, {
              method: "PUT",
              body: { attendees: readToggles(editor) },
            });
            w.attendance = res.watch.attendance;
            w.attendees = res.watch.attendees;
            refreshRow(row, w);
            toast("Updated who watched");
          } catch (err) {
            editor.querySelector(".watch-who-error").textContent = err.message || "Could not save";
            syncSave();
          }
        });
      };

      const bindWatchRow = (row) => {
        const w = (title.watches || []).find((x) => x.id === Number(row.dataset.watchId));
        row.querySelector(".watch-who-btn")?.addEventListener("click", () => {
          if (w) openWhoEditor(row, w);
        });
        row.querySelector(".btn-delete-watch")?.addEventListener("click", async (e) => {
          const watchId = e.currentTarget.dataset.id;
          if (!confirm("Remove this watch date?")) return;
          try {
            await api(`${apiPrefix}/titles/${titleId}/watches/${watchId}`, { method: "DELETE" });
            toast("Watch entry removed");
            await renderDetail(container, titleId);
          } catch (err) {
            toast(err.message || "Failed to delete watch entry");
          }
        });
      };
      // Re-render one saved watch in place (after Save or Cancel), keeping its place in the Show-all fold.
      function refreshRow(row, w) {
        const idx = title.watches.indexOf(w);
        const hidden = row.classList.contains("hidden");
        const tmp = document.createElement("div");
        tmp.innerHTML = watchRowHTML(w, idx).trim();
        const fresh = tmp.firstElementChild;
        fresh.classList.toggle("hidden", hidden);   // keep the row's current visibility (Show all)
        row.replaceWith(fresh);
        bindWatchRow(fresh);
      }
      container.querySelectorAll(".watch-row").forEach(bindWatchRow);
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
