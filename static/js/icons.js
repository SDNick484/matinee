// Matinee — inline SVG icons, chip icons, watch-date and copy labels.

  // ================================================================= Icons
  // Inline SVG icons (24×24, stroke = currentColor). Color emoji (U+1F4BF disc, U+1F3B2 dice, ...)
  // depend on the viewer's OS having a color-emoji font; without one (e.g. a
  // Linux desktop without Noto Color Emoji) they render as empty boxes.
  const ICONS = {
    disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/>',
    cloud: '<path d="M7 18.5h10.5a4 4 0 0 0 .4-7.98A6 6 0 0 0 6.3 9.3 4.6 4.6 0 0 0 7 18.5z"/>',
    dice: '<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/><circle cx="8.5" cy="8.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="15.5" cy="8.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="8.5" cy="15.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="15.5" cy="15.5" r="1.3" fill="currentColor" stroke="none"/>',
    pin: '<path d="M12 21.5s-6.5-6-6.5-11.5a6.5 6.5 0 0 1 13 0c0 5.5-6.5 11.5-6.5 11.5z"/><circle cx="12" cy="10" r="2.3"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    camera: '<path d="M4 7.5h3.2l1.8-2.5h6l1.8 2.5H20a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8.5a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>',
    box: '<path d="M3.5 7.5 12 3.5l8.5 4v9L12 20.5l-8.5-4z"/><path d="M3.5 7.5 12 11.5l8.5-4M12 11.5v9"/>',
    film: '<rect x="3.5" y="3.5" width="17" height="17" rx="2"/><path d="M7.5 3.5v17M16.5 3.5v17M3.5 8h4M3.5 12h4M3.5 16h4M16.5 8h4M16.5 12h4M16.5 16h4"/>',
    popcorn: '<path d="M6 10.5 7.6 21h8.8L18 10.5z"/><path d="M10 10.5 10.6 21M14 10.5 13.4 21"/><path d="M6.5 10.5a2.5 2.5 0 0 1 1.7-4.6 3 3 0 0 1 5.6-1 2.6 2.6 0 0 1 3.9 2.6 2 2 0 0 1-.2 3"/>',
    home: '<path d="M3.5 11 12 4l8.5 7"/><path d="M5.5 9.5V20h13V9.5"/><path d="M10 20v-5h4v5"/>',
    folder: '<path d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4.2l2 2H19a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    edit: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    trash: '<path d="M4 7h16M10 7V4.5h4V7M6 7l1 13h10l1-13M10 11v5.5M14 11v5.5"/>',
    warn: '<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.3" r="0.6" fill="currentColor"/>',
    image: '<rect x="3.5" y="4" width="17" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.7"/><path d="m20.5 15.5-5-5-10 9.5"/>',
    undo: '<path d="M4 9.5h10.5a5 5 0 0 1 0 10H8"/><path d="m7.5 6-3.5 3.5L7.5 13"/>',
    people: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 19.5c.4-3.4 3-5.5 6-5.5s5.6 2.1 6 5.5"/><circle cx="17" cy="9.5" r="2.4"/><path d="M16.5 14.2c2.6.1 4.3 1.8 4.7 4.6"/>',
    dot: '<circle cx="12" cy="12" r="6.5" fill="currentColor" stroke="none"/>',
    chat: '<path d="M5 4.5h14A1.5 1.5 0 0 1 20.5 6v9a1.5 1.5 0 0 1-1.5 1.5h-8.5L6 20v-3.5H5A1.5 1.5 0 0 1 3.5 15V6A1.5 1.5 0 0 1 5 4.5z"/>',
    send: '<path d="M4 12 20.5 4 16 20.5l-4-6.5z"/><path d="m12 14 8.5-10"/>',
    download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/><path d="M4.5 19.5h15"/>',
    list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.5" cy="6.5" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="17.5" r="1.2" fill="currentColor" stroke="none"/>',
  };

  function icon(name, cls = "") {
    const body = ICONS[name];
    if (!body) return "";
    return `<svg class="ico${cls ? " " + cls : ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
  }

  function getChipIcon(format) {
    if (format === "4K UHD" || format === "Blu-ray" || format === "3D Blu-ray" || format === "DVD" || format === "VHS") {
      return icon("disc");
    }
    if (format === "Digital 4K" || format === "Digital HD" || format === "Digital SD") {
      return icon("cloud");
    }
    return "";
  }

  function formatCopyLabel(copy) {
    if (copy.kind === "digital") {
      return `${copy.format}${copy.service ? " · " + copy.service : ""}`;
    }
    return copy.format;
  }

  const BUCKET_LABELS = {
    past_year: "Past Year",
    "1_2_years": "1–2 Years",
    "2_5_years": "2–5 Years",
    "5_10_years": "5–10 Years",
    over_10_years: "10+ Years",
    unknown: "Date Unknown",
  };

  function formatRelativeDate(isoDateStr) {
    if (!isoDateStr) return "Never";
    const parts = isoDateStr.split("-").map(Number);
    const target = new Date(parts[0], parts[1] - 1, parts[2]);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffMs = today - target;
    const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays <= 0) return "today";
    if (diffDays === 1) return "yesterday";
    if (diffDays < 7) return `${diffDays} days ago`;
    const diffWeeks = Math.floor(diffDays / 7);
    if (diffWeeks < 5) return diffWeeks === 1 ? "1 week ago" : `${diffWeeks} weeks ago`;
    const diffMonths = Math.floor(diffDays / 30.4375);
    if (diffMonths < 12) return diffMonths === 1 ? "1 month ago" : `${diffMonths} months ago`;
    const diffYears = Math.floor(diffDays / 365.25);
    return diffYears === 1 ? "1 year ago" : `${diffYears} years ago`;
  }

  function formatReadableDate(isoDateStr) {
    if (!isoDateStr) return "";
    const parts = isoDateStr.split("-").map(Number);
    const date = new Date(parts[0], parts[1] - 1, parts[2]);
    return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }

  function formatLastWatched(lastWatchedDate, watchCount = 0) {
    if (lastWatchedDate) {
      const relative = formatRelativeDate(lastWatchedDate);
      const readable = formatReadableDate(lastWatchedDate);
      return `Watched ${relative} (${readable})`;
    }
    if (watchCount > 0) {
      return "Watched (Date unknown)";
    }
    return "Never watched";
  }

export {
  BUCKET_LABELS, formatCopyLabel, formatLastWatched, formatReadableDate, formatRelativeDate, getChipIcon,
  icon
};
