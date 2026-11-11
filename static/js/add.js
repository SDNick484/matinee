// Matinee — add flow: TMDB search and barcode scanner (8.1 / 8.2).
import { api, esc, isAdmin, state, TMDB_IMG_THUMB, toast } from "./core.js";
import { icon } from "./icons.js";
import { navigate } from "./router.js";
import { openCopyModal } from "./forms.js";

  // ================================================================= 8.1 / 8.2 Add Flow & Barcode Scanner
  let scannerStream = null;
  let scannerAnimId = null;
  let isScannerPaused = false;

  function pauseBarcodeScanner() {
    isScannerPaused = true;
  }

  function resumeBarcodeScanner() {
    isScannerPaused = false;
  }

  function playBeep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      gain.gain.setValueAtTime(0.2, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.15);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.15);
    } catch (e) {}
  }

  function getBatchQueue() {
    try {
      return JSON.parse(localStorage.getItem("matinee_scan_queue") || "[]");
    } catch (e) {
      return [];
    }
  }

  function setBatchQueue(q) {
    try {
      localStorage.setItem("matinee_scan_queue", JSON.stringify(q));
    } catch (e) {}
  }

  function stopBarcodeScanner() {
    if (scannerAnimId) {
      cancelAnimationFrame(scannerAnimId);
      scannerAnimId = null;
    }
    if (scannerStream) {
      scannerStream.getTracks().forEach((track) => track.stop());
      scannerStream = null;
    }
    isScannerPaused = false;
  }

  async function startBarcodeScanner(videoEl, reticleEl, onBarcodeDetected) {
    if (!("BarcodeDetector" in window)) {
      return { supported: false };
    }
    try {
      const detector = new window.BarcodeDetector({ formats: ["upc_a", "upc_e", "ean_13"] });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      scannerStream = stream;
      videoEl.srcObject = stream;
      await videoEl.play();

      let lastDetected = null;
      let lastTime = 0;

      async function scanLoop() {
        if (!isScannerPaused && videoEl.readyState >= 2) {
          try {
            const barcodes = await detector.detect(videoEl);
            if (!isScannerPaused && barcodes.length > 0) {
              const raw = barcodes[0].rawValue;
              const now = Date.now();
              if (raw !== lastDetected || now - lastTime > 2500) {
                lastDetected = raw;
                lastTime = now;
                reticleEl.classList.add("success");
                playBeep();
                setTimeout(() => reticleEl.classList.remove("success"), 400);
                onBarcodeDetected(raw);
              }
            }
          } catch (e) {}
        }
        scannerAnimId = requestAnimationFrame(scanLoop);
      }
      scanLoop();
      return { supported: true };
    } catch (err) {
      return { supported: true, error: err.message };
    }
  }

  async function renderAdd(container) {
    // Stop any active scanner before re-rendering
    stopBarcodeScanner();

    if (!isAdmin) {
      container.innerHTML = `
        <div class="empty-state">
          <h2>Editor Sign-in Required</h2>
          <p>Adding movies requires signing in with an editor account.</p>
          <div style="display:flex;gap:var(--s2);justify-content:center;margin-top:var(--s3)">
            <a href="#/" class="btn btn-ghost">Back to collection</a>
            <a href="/admin#/add" class="btn">Sign In as Editor</a>
          </div>
        </div>
      `;
      return;
    }

    const initialQ = state.route.query.get("q") || state.add.q || "";

    container.innerHTML = `
      <div class="add-view">
        <div style="margin-bottom:var(--s4)">
          <a href="#/" class="back-link">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            Back to collection
          </a>
          <h1 style="font-size:var(--fs-xl);font-weight:800;margin-top:var(--s2)">Add Movies</h1>
        </div>

        <div class="scanner-tabs">
          <button type="button" class="scanner-tab-btn active" id="tab-search">${icon("search")} Search Title</button>
          <button type="button" class="scanner-tab-btn" id="tab-scan">${icon("camera")} Scan Barcode</button>
        </div>

        <!-- Panel 1: Search -->
        <div id="search-panel">
          <p style="color:var(--ink-2);font-size:var(--fs-sm);margin-bottom:var(--s3)">
            Search TMDB by title (e.g. "Dune" or "Dune 1984") to find and add copies to your collection.
          </p>
          <div class="search-wrap">
            <span class="search-icon">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            </span>
            <input type="text" id="tmdb-search-input" class="search-input" placeholder="Search TMDB (at least 2 characters)…" value="${esc(initialQ)}" autocomplete="off">
            ${initialQ ? `<button type="button" id="tmdb-clear-search" class="clear-btn" aria-label="Clear">✕</button>` : ""}
          </div>

          <div id="tmdb-results-container">
            <!-- Results injected here -->
          </div>
        </div>

        <!-- Panel 2: Barcode Scanner -->
        <div id="scanner-panel" style="display:none">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--s3);flex-wrap:wrap;gap:var(--s2)">
            <div class="toggle-group" id="scan-mode-toggle">
              <button type="button" class="toggle-btn active" data-mode="single">Single Scan</button>
              <button type="button" class="toggle-btn" data-mode="batch">Batch Mode (Shelf)</button>
            </div>
            <div id="scanner-status" style="font-size:var(--fs-xs);color:var(--ink-2)">Camera ready</div>
          </div>

          <div class="scanner-viewport" id="scanner-viewport">
            <video id="scanner-video" class="scanner-video" playsinline muted autoplay></video>
            <div class="scanner-reticle" id="scanner-reticle">
              <div class="scanner-laser"></div>
            </div>
          </div>

          <div style="max-width:500px;margin:0 auto var(--s4)">
            <div style="display:flex;gap:var(--s2)">
              <input type="text" id="manual-upc-input" class="search-input" inputmode="numeric" placeholder="Enter UPC barcode number…" style="flex:1">
              <button type="button" id="manual-upc-btn" class="btn">Lookup</button>
            </div>
          </div>

          <!-- Batch Queue Area -->
          <div id="batch-area" class="batch-panel" style="display:none">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--s3)">
              <div class="batch-queue-counter">
                <span>${icon("box")}</span>
                <span id="batch-count-label">0 Discs in Queue</span>
              </div>
              <div style="display:flex;gap:var(--s2)">
                <button type="button" id="btn-batch-clear" class="btn btn-ghost" style="font-size:var(--fs-xs)">Clear Queue</button>
                <button type="button" id="btn-batch-review" class="btn" style="font-size:var(--fs-xs)">Review &amp; Add All</button>
              </div>
            </div>
            <div id="batch-queue-preview" style="font-size:var(--fs-xs);color:var(--ink-2)"></div>
          </div>

          <div id="upc-result-container" style="margin-top:var(--s4)"></div>
        </div>
      </div>
    `;

    // Tab switcher
    const tabSearch = document.getElementById("tab-search");
    const tabScan = document.getElementById("tab-scan");
    const searchPanel = document.getElementById("search-panel");
    const scannerPanel = document.getElementById("scanner-panel");

    let currentMode = "single";

    tabSearch?.addEventListener("click", () => {
      stopBarcodeScanner();
      tabSearch.classList.add("active");
      tabScan.classList.remove("active");
      searchPanel.style.display = "block";
      scannerPanel.style.display = "none";
    });

    tabScan?.addEventListener("click", async () => {
      tabScan.classList.add("active");
      tabSearch.classList.remove("active");
      searchPanel.style.display = "none";
      scannerPanel.style.display = "block";
      initScanner();
    });

    // Scanner Mode Switcher
    const modeBtns = document.querySelectorAll("#scan-mode-toggle .toggle-btn");
    modeBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        modeBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentMode = btn.dataset.mode;
        resumeBarcodeScanner();
        updateBatchUI();
      });
    });

    // Manual UPC lookup
    const manualInput = document.getElementById("manual-upc-input");
    const manualBtn = document.getElementById("manual-upc-btn");
    manualBtn?.addEventListener("click", () => {
      const code = manualInput.value.trim();
      if (code) handleBarcodeScanned(code);
    });
    manualInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const code = manualInput.value.trim();
        if (code) handleBarcodeScanned(code);
      }
    });

    // Batch Actions
    const btnBatchClear = document.getElementById("btn-batch-clear");
    const btnBatchReview = document.getElementById("btn-batch-review");
    btnBatchClear?.addEventListener("click", () => {
      if (confirm("Clear all scanned barcodes from the queue?")) {
        setBatchQueue([]);
        updateBatchUI();
      }
    });
    btnBatchReview?.addEventListener("click", () => {
      reviewBatchQueue();
    });

    function updateBatchUI() {
      const batchArea = document.getElementById("batch-area");
      const countLabel = document.getElementById("batch-count-label");
      const preview = document.getElementById("batch-queue-preview");
      const queue = getBatchQueue();

      if (currentMode === "batch" || queue.length > 0) {
        if (batchArea) batchArea.style.display = "block";
        if (countLabel) countLabel.textContent = `${queue.length} Disc${queue.length === 1 ? "" : "s"} in Queue`;
        if (preview) {
          preview.textContent = queue.length > 0
            ? `Barcodes ready: ${queue.slice(-5).join(", ")}${queue.length > 5 ? ` and ${queue.length - 5} more` : ""}`
            : "Scan barcodes continuously to stack them into your queue.";
        }
      } else if (batchArea) {
        batchArea.style.display = "none";
      }
    }

    async function handleBarcodeScanned(code) {
      if (currentMode === "batch") {
        const queue = getBatchQueue();
        if (!queue.includes(code)) {
          queue.push(code);
          setBatchQueue(queue);
          toast(`Scanned UPC ${code} (${queue.length} in queue)`);
        } else {
          toast(`UPC ${code} already in queue`);
        }
        updateBatchUI();
        if (manualInput) manualInput.value = "";
      } else {
        // Single Scan Mode: pause camera scanner so loop doesn't continuously trigger
        pauseBarcodeScanner();
        await lookupSingleUpc(code);
      }
    }

    async function lookupSingleUpc(code) {
      const resultContainer = document.getElementById("upc-result-container");
      if (!resultContainer) return;
      resultContainer.innerHTML = `<div style="text-align:center;padding:24px;color:var(--ink-2)">Looking up barcode ${esc(code)}…</div>`;

      try {
        const data = await api(`/admin/api/upc/${encodeURIComponent(code)}`);
        const results = data.results || [];
        if (results.length === 0) {
          resultContainer.innerHTML = `
            <div class="empty-state">
              <h2>Product Found, but no TMDB match</h2>
              <p>Product: <strong>${esc(data.raw_title)}</strong></p>
              <p>Try searching TMDB directly by title.</p>
              <button type="button" class="btn btn-ghost" id="btn-cancel-upc" style="margin-top:var(--s2)">Dismiss & Scan Again</button>
            </div>
          `;
          document.getElementById("btn-cancel-upc")?.addEventListener("click", () => {
            resultContainer.innerHTML = "";
            resumeBarcodeScanner();
          });
          return;
        }

        const isMulti = results.length > 1;
        resultContainer.innerHTML = `
          <div style="background:var(--card);border:1px solid var(--line);border-radius:var(--r-card);padding:var(--s4);max-width:540px;margin:0 auto">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--s1)">
              <span style="font-size:var(--fs-xs);color:var(--ink-3)">BARCODE: ${esc(data.upc)}</span>
              <span class="chip k4">${esc(data.format_hint)}</span>
            </div>
            <div style="font-size:var(--fs-sm);color:var(--ink-2);margin-bottom:var(--s2)">Found: <strong>${esc(data.raw_title)}</strong></div>

            ${
              data.already_owned
                ? `<div class="duplicate-banner" style="margin-bottom:var(--s3)">${icon("warn")} In collection: ${esc(data.owned_summary)}. Adding this will attach your physical disc.</div>`
                : ""
            }

            ${
              isMulti
                ? `<div style="font-size:var(--fs-xs);color:var(--ink-3);margin-bottom:var(--s2);text-transform:uppercase;letter-spacing:0.5px">Select matching title (${results.length} found):</div>`
                : ""
            }

            <div style="display:flex;flex-direction:column;gap:var(--s2);margin-bottom:var(--s3)">
              ${results.slice(0, 5).map((m, idx) => `
                <div style="display:flex;gap:var(--s3);align-items:center;padding:var(--s2);border:1px solid var(--line);border-radius:var(--r-card);background:var(--bg)">
                  ${m.thumb ? `<img src="${esc(m.thumb)}" style="width:40px;height:60px;border-radius:4px;object-fit:cover;flex-shrink:0" alt="">` : '<div style="width:40px;height:60px;background:var(--card);border-radius:4px;flex-shrink:0"></div>'}
                  <div style="flex:1;min-width:0">
                    <div style="font-weight:700;font-size:var(--fs-sm);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(m.title)}</div>
                    <div style="font-size:var(--fs-xs);color:var(--ink-2)">${m.year || ""} ${m.already_owned ? `<span style="color:var(--amber);margin-left:4px">${icon("warn")} Owned: ${esc(m.owned_summary || "")}</span>` : ""}</div>
                  </div>
                  <button type="button" class="btn btn-sm btn-select-candidate" data-idx="${idx}">
                    Add Disc
                  </button>
                </div>
              `).join("")}
            </div>

            <div style="display:flex;justify-content:flex-end">
              <button type="button" class="btn btn-ghost" id="btn-cancel-upc">Cancel / Scan Again</button>
            </div>
          </div>
        `;

        resultContainer.querySelectorAll(".btn-select-candidate").forEach((btn) => {
          btn.addEventListener("click", () => {
            const idx = parseInt(btn.dataset.idx, 10);
            const selectedMovie = results[idx];
            if (selectedMovie) {
              openCopyModal(selectedMovie, {
                kind: "physical",
                format: data.format_hint,
                duplicateNotice: selectedMovie.already_owned ? (selectedMovie.owned_summary || data.owned_summary) : (data.already_owned ? data.owned_summary : null),
              });
            }
          });
        });

        document.getElementById("btn-cancel-upc")?.addEventListener("click", () => {
          resultContainer.innerHTML = "";
          resumeBarcodeScanner();
        });
      } catch (err) {
        resultContainer.innerHTML = `
          <div class="empty-state">
            <h2>Lookup Failed</h2>
            <p>${esc(err.message || "Product not found for this barcode.")}</p>
            <button type="button" class="btn btn-ghost" id="btn-cancel-upc" style="margin-top:var(--s2)">Dismiss & Scan Again</button>
          </div>
        `;
        document.getElementById("btn-cancel-upc")?.addEventListener("click", () => {
          resultContainer.innerHTML = "";
          resumeBarcodeScanner();
        });
      }
    }

    async function reviewBatchQueue() {
      const queue = getBatchQueue();
      if (queue.length === 0) {
        toast("Queue is empty");
        return;
      }

      const modalRoot = document.getElementById("modal-root");
      if (!modalRoot) return;

      modalRoot.innerHTML = `
        <div class="modal-overlay" id="modal-overlay">
          <div class="modal-dialog" style="max-width:600px;max-height:85vh;overflow-y:auto">
            <div class="modal-header">
              <div>
                <div class="modal-title">Review Scanned Discs (${queue.length})</div>
                <div style="font-size:var(--fs-xs);color:var(--ink-3)">Resolve and import your physical discs in bulk</div>
              </div>
              <button type="button" class="btn-close" id="btn-batch-modal-close">✕</button>
            </div>

            <div id="batch-items-container" style="padding:var(--s3) 0">
              <div style="text-align:center;padding:24px;color:var(--ink-2)">Resolving ${queue.length} barcodes…</div>
            </div>

            <div class="form-actions" style="margin-top:var(--s4)">
              <button type="button" class="btn btn-ghost" id="btn-batch-modal-cancel">Close</button>
              <button type="button" class="btn" id="btn-batch-import-all">Import All Selected</button>
            </div>
          </div>
        </div>
      `;

      document.getElementById("btn-batch-modal-close")?.addEventListener("click", () => modalRoot.innerHTML = "");
      document.getElementById("btn-batch-modal-cancel")?.addEventListener("click", () => modalRoot.innerHTML = "");

      // Resolve items sequentially
      const itemsContainer = document.getElementById("batch-items-container");
      const resolved = [];

      for (let i = 0; i < queue.length; i++) {
        const upc = queue[i];
        if (itemsContainer) {
          itemsContainer.innerHTML = `<div style="text-align:center;padding:24px;color:var(--ink-2)">Resolving barcode ${i + 1} of ${queue.length}…</div>`;
        }
        try {
          const res = await api(`/admin/api/upc/${encodeURIComponent(upc)}`);
          if (res.results && res.results.length > 0) {
            resolved.push({ upc, ok: true, data: res });
          } else {
            resolved.push({ upc, ok: false, error: "No TMDB match" });
          }
        } catch (e) {
          resolved.push({ upc, ok: false, error: e.message || "Lookup error" });
        }
      }

      itemsContainer.innerHTML = resolved
        .map((item, idx) => {
          if (!item.ok) {
            return `
              <div style="padding:var(--s2);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;opacity:0.6">
                <div>
                  <div style="font-weight:600;font-size:var(--fs-sm)">UPC: ${esc(item.upc)}</div>
                  <div style="font-size:var(--fs-xs);color:var(--accent-text)">${esc(item.error)}</div>
                </div>
              </div>
            `;
          }
          const d = item.data;
          const movie = d.results[0];
          return `
            <div style="padding:var(--s2) 0;border-bottom:1px solid var(--line);display:flex;gap:var(--s3);align-items:center">
              <input type="checkbox" class="batch-chk" data-idx="${idx}" checked style="width:18px;height:18px">
              ${movie.thumb ? `<img src="${esc(movie.thumb)}" style="width:36px;height:54px;border-radius:4px;object-fit:cover" alt="">` : ""}
              <div style="flex:1">
                <div style="font-weight:700;font-size:var(--fs-sm)">${esc(movie.title)} <span style="font-weight:400;color:var(--ink-2)">(${movie.year || ""})</span></div>
                <div style="font-size:var(--fs-xs);color:var(--ink-2)"><span class="chip k4" style="height:18px;font-size:10px">${esc(d.format_hint)}</span> ${d.already_owned ? `<span style="color:var(--amber);margin-left:6px">${icon("warn")} Owned: ${esc(d.owned_summary)}</span>` : ""}</div>
              </div>
            </div>
          `;
        })
        .join("");

      document.getElementById("btn-batch-import-all")?.addEventListener("click", async () => {
        const chks = itemsContainer.querySelectorAll(".batch-chk:checked");
        let addedCount = 0;
        const importedUpcs = new Set();
        const btn = document.getElementById("btn-batch-import-all");
        if (btn) btn.disabled = true;

        for (const chk of chks) {
          const idx = parseInt(chk.dataset.idx, 10);
          const item = resolved[idx];
          if (item?.ok && item.data.results?.[0]) {
            try {
              await api("/admin/api/copies", {
                method: "POST",
                body: JSON.stringify({
                  tmdb_id: item.data.results[0].tmdb_id,
                  kind: "physical",
                  format: item.data.format_hint,
                  service: null,
                }),
              });
              addedCount++;
              importedUpcs.add(item.upc);
            } catch (err) {}
          }
        }

        const remaining = queue.filter((code) => !importedUpcs.has(code));
        setBatchQueue(remaining);
        updateBatchUI();
        modalRoot.innerHTML = "";
        if (addedCount > 0) {
          toast(`Successfully added ${addedCount} physical disc copies!`);
        }
        if (remaining.length > 0) {
          toast(`${remaining.length} barcode(s) remaining in queue`);
        }
        navigate("#/");
      });
    }

    async function initScanner() {
      const video = document.getElementById("scanner-video");
      const reticle = document.getElementById("scanner-reticle");
      const status = document.getElementById("scanner-status");
      updateBatchUI();

      if (!video || !reticle) return;
      const res = await startBarcodeScanner(video, reticle, (barcode) => {
        handleBarcodeScanned(barcode);
      });

      if (!res.supported) {
        if (status) status.innerHTML = `<span style="color:var(--amber)">Native Barcode Detection not supported in this browser. Use manual UPC entry below.</span>`;
      } else if (res.error) {
        if (status) status.innerHTML = `<span style="color:var(--accent-text)">Camera access denied or unavailable (${esc(res.error)}). Use manual entry below.</span>`;
      } else {
        if (status) status.textContent = "Camera active: place barcode in box";
      }
    }

    // Bind search listeners
    const searchInput = document.getElementById("tmdb-search-input");
    const clearBtn = document.getElementById("tmdb-clear-search");

    let debounceTimer;
    searchInput?.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      const val = e.target.value.trim();
      state.add.q = val;
      debounceTimer = setTimeout(() => {
        executeTmdbSearch(val);
      }, 400);
    });

    clearBtn?.addEventListener("click", () => {
      if (searchInput) searchInput.value = "";
      state.add.q = "";
      executeTmdbSearch("");
    });

    if (initialQ.length >= 2) {
      executeTmdbSearch(initialQ);
    }
  }

  async function executeTmdbSearch(query) {
    const container = document.getElementById("tmdb-results-container");
    if (!container) return;

    if (!query || query.length < 2) {
      container.innerHTML = ``;
      return;
    }

    container.innerHTML = `<div style="text-align:center;padding:32px;color:var(--ink-2)">Searching TMDB…</div>`;

    try {
      const data = await api(`/admin/api/tmdb/search?q=${encodeURIComponent(query)}`);
      const results = data.results || [];
      state.add.results = results;

      if (results.length === 0) {
        container.innerHTML = `
          <div class="empty-state" style="margin-top:var(--s4)">
            <h2>No matches</h2>
            <p>No TMDB matches for &#8216;${esc(query)}&#8217;. Check spelling or try fewer words.</p>
          </div>
        `;
        return;
      }

      container.innerHTML = `
        <div class="results-list">
          ${results
            .map((m) => {
              const thumbUrl = m.poster_path ? `${TMDB_IMG_THUMB}${m.poster_path}` : "";
              const thumbHtml = thumbUrl
                ? `<img class="result-thumb" src="${esc(thumbUrl)}" loading="lazy" alt="${esc(m.title)}" onerror="this.style.display='none';">`
                : `<div class="result-thumb poster-ph" style="font-size:10px">${esc(m.title)}</div>`;

              return `
                <div class="result-card" data-tmdb-id="${m.tmdb_id}">
                  ${thumbHtml}
                  <div class="result-info">
                    <div class="result-title">${esc(m.title)}</div>
                    <div class="result-year">${m.year || "Unknown year"}</div>
                    ${m.overview ? `<div class="result-overview">${esc(m.overview)}</div>` : ""}
                    ${m.owned ? `<span class="badge-owned">In collection</span>` : ""}
                  </div>
                </div>
              `;
            })
            .join("")}
        </div>
      `;

      container.querySelectorAll(".result-card").forEach((card) => {
        card.addEventListener("click", () => {
          const tmdbId = parseInt(card.dataset.tmdbId, 10);
          const movie = state.add.results.find((r) => r.tmdb_id === tmdbId);
          if (movie) {
            openCopyModal(movie);
          }
        });
      });
    } catch (err) {
      let msg = "TMDB lookup failed.";
      if (err.reason === "not_configured" || err.message?.includes("not configured")) {
        msg = "TMDB is not configured.";
      } else if (err.reason === "rate_limited" || err.status === 429 || err.message?.includes("busy")) {
        msg = "TMDB is busy. Try again in a minute.";
      } else if (err.reason === "unavailable" || err.status === 502) {
        msg = "TMDB is unavailable right now.";
      } else if (err.message) {
        msg = err.message;
      }

      container.innerHTML = `
        <div class="empty-state" style="margin-top:var(--s4)">
          <h2>Search Error</h2>
          <p>${esc(msg)}</p>
        </div>
      `;
    }
  }

export { renderAdd, resumeBarcodeScanner, stopBarcodeScanner };
