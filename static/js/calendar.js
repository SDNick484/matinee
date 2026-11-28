// Matinee — the Calendar (spec 2026-11-19-calendar-settings-design §3). #/calendar[/YYYY-MM]: one month as a
// Sunday-first grid with a poster on each night the family watched a movie; a tap opens that day's list. Public when
// the site is public: the data is titles and dates only. The month shown is the one the server answered for (never
// the browser clock), and the date math is plain arithmetic, so the browser's time zone can't shift a day.
// Ported from the Christmas app's calendar.js.
import { api, esc, isAdmin, state } from "./core.js";
import { icon } from "./icons.js";
import { openModal } from "./modal.js";

  const CAL_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December"];
  const CAL_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function calMonthKey(year, month) {
    return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`;
  }

  // "2026-12" moved by `by` months, as "YYYY-MM".
  function calShiftMonth(year, month, by) {
    const total = year * 12 + (month - 1) + by;
    return calMonthKey(Math.floor(total / 12), (total % 12) + 1);
  }

  function calDaysIn(year, month) {
    const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
    return [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  }

  function calWeekday(year, month, day) {
    const d = new Date(0);
    d.setUTCFullYear(year, month - 1, day);   // not Date.UTC, which reads years 0 to 99 as 1900s
    return d.getUTCDay();
  }

  // ================================================================= posters
  // The grid's poster, or its placeholder (title over a dark card) when there is none or it fails to load.
  function calPlaceholderHtml(e) {
    return `<div class="poster-ph"><span>${esc(e.title)}</span></div>`;
  }

  function calPosterHtml(e) {
    return e.poster_url
      ? `<img class="poster-img" src="${esc(e.poster_url)}" loading="lazy" alt="" data-title="${esc(e.title)}">`
      : calPlaceholderHtml(e);
  }

  // An <img> error does not bubble, so listen in the capture phase on a container and swap in the placeholder.
  function watchCalPosters(box) {
    box.addEventListener("error", (ev) => {
      const img = ev.target;
      if (!(img instanceof HTMLImageElement) || !img.classList.contains("poster-img")) return;
      img.outerHTML = calPlaceholderHtml({ title: img.dataset.title || "" });
    }, true);
  }

  // ================================================================= the day sheet
  function calRowHtml(e) {
    const label = e.year ? `${e.title} (${e.year})` : e.title;
    return `<a class="cal-row" href="#/t/${Number(e.title_id)}">`
      + `<span class="cal-row-poster">${calPosterHtml(e)}</span>`
      + `<span class="cal-row-title">${esc(label)}</span></a>`;
  }

  // The same sheet as lists.js's: a dialog from modal.js, which also closes it on Escape and gives focus back to the day
  // that opened it. A row is a link, and handleRoute closes the sheet on the hash change.
  function openCalDaySheet(date, entries) {
    const [year, month, day] = date.split("-").map(Number);
    const title = `${CAL_WEEKDAYS[calWeekday(year, month, day)]}, ${CAL_MONTHS[month - 1]} ${day}, ${year}`;
    const modal = openModal({
      title,
      iconHTML: icon("calendar"),
      className: "cal-dialog",
      id: "calendar-sheet",
      titleId: "calendar-sheet-title",
      content: `<div class="cal-rows">${entries.map(calRowHtml).join("")}</div>`,
    });
    if (modal) watchCalPosters(modal.dialog);
  }

  // ================================================================= the month
  function calDayHtml(year, month, day, entries, today) {
    const date = `${calMonthKey(year, month)}-${String(day).padStart(2, "0")}`;
    const ring = date === today ? " cal-today" : "";
    const num = `<span class="cal-num">${day}</span>`;
    if (!entries) return `<div class="cal-day cal-empty${ring}">${num}</div>`;
    const label = `${CAL_MONTHS[month - 1]} ${day}: ${entries.map((e) => e.title).join(", ")}`;
    return `<button type="button" class="cal-day${ring}" data-date="${date}" aria-label="${esc(label)}">`
      + `<span class="cal-poster">${calPosterHtml(entries[0])}</span>${num}`
      + (entries.length > 1 ? `<span class="cal-more">+${entries.length - 1}</span>` : "")
      + `</button>`;
  }

  function calMonthHtml(year, month, days, today) {
    const heads = ["S", "M", "T", "W", "T", "F", "S"].map((d, i) => `<div class="cal-wd" aria-hidden="true" title="${CAL_WEEKDAYS[i]}">${d}</div>`);
    const blanks = Array.from({ length: calWeekday(year, month, 1) }, () => `<div class="cal-blank"></div>`);
    const cells = [];
    for (let day = 1; day <= calDaysIn(year, month); day++) {
      cells.push(calDayHtml(year, month, day, days[`${calMonthKey(year, month)}-${String(day).padStart(2, "0")}`], today));
    }
    return heads.concat(blanks, cells).join("");
  }

  // The route's month ("YYYY-MM"), or null for this month, which the server decides. The router has already
  // refused anything that is not a real month.
  async function renderCalendar(container, month) {
    const seq = state.routeSeq;
    container.innerHTML = `
      <div class="cal-view" id="calendar-view">
        <a href="#/" class="back-link" id="calendar-back">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
          Back to collection
        </a>
        <div class="cal-head">
          <a class="btn btn-ghost cal-arrow" id="calendar-prev" aria-label="Previous month">&lsaquo;</a>
          <h1 class="cal-title" id="calendar-title" aria-live="polite">&nbsp;</h1>
          <a class="btn btn-ghost cal-arrow" id="calendar-next" aria-label="Next month">&rsaquo;</a>
        </div>
        <div class="cal-grid" id="calendar-grid" role="group" aria-label="Days watched"></div>
        <p class="cal-status" id="calendar-status" role="status">Loading…</p>
      </div>`;
    const base = `${isAdmin ? "/admin/api" : "/api"}/calendar`;
    let data;
    try {
      data = await api(month ? `${base}?month=${encodeURIComponent(month)}` : base);
    } catch (err) {
      if (seq !== state.routeSeq) return;
      document.getElementById("calendar-status").textContent = err.message || "Couldn't load the calendar right now.";
      return;
    }
    if (seq !== state.routeSeq) return;   // the user left the Calendar (or changed month) while it loaded
    const [year, mon] = data.month.split("-").map(Number);
    document.getElementById("calendar-title").textContent = `${CAL_MONTHS[mon - 1]} ${year}`;
    document.getElementById("calendar-prev").href = `#/calendar/${calShiftMonth(year, mon, -1)}`;
    document.getElementById("calendar-next").href = `#/calendar/${calShiftMonth(year, mon, 1)}`;
    const box = document.getElementById("calendar-grid");
    watchCalPosters(box);
    box.innerHTML = calMonthHtml(year, mon, data.days || {}, data.today);
    document.getElementById("calendar-status").textContent = Object.keys(data.days || {}).length
      ? "" : `Nothing watched in ${CAL_MONTHS[mon - 1]} ${year}.`;
    box.querySelectorAll("button.cal-day").forEach((cell) => {
      cell.addEventListener("click", () => openCalDaySheet(cell.dataset.date, data.days[cell.dataset.date]));
    });
  }

export { renderCalendar };
