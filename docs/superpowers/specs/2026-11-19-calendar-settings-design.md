# Calendar, Settings, a findable Lists button and a rated "Who watched": design

**Date:** 2026-11-19
**Repo:** `SDNick484/matinee`, `main` @ `856870d` (schema v12)
**Size:** four independent changes, one PR each: Calendar, then Settings (both rewrite the header); the Lists button and Who watched alongside
**Status:** Approved by Nick on 2026-11-20

---

## 1. Intent

Four household-facing changes from one conversation:

1. **Calendar.** The family loved the Calendar in the Christmas app (`SDNick484/its-a-wonderful-app`,
   `docs/superpowers/specs/2026-11-19-calendar-design.md`). Matinee should have the same thing for
   the whole year: one month at a time, a movie poster on each night we watched something. It is for fun and for
   looking back, and it will be maintained year-round.
2. **Settings.** The top bar is crowded. People, Locations and Lists are set-up screens, not things used every
   night, so they move to one Settings page, like the Christmas app's.
3. **Lists button.** Adding a movie to a list (for example *The 40-Year-Old Virgin* to Watch Soon) already works
   from the movie page, but Nick couldn't find the button. Make it hard to miss.
4. **Who watched.** When logging a watch of an R, PG-13 or NC-17 movie, start with only the adults selected.

### Goals

- A **Calendar** link in the top bar beside Ask, Movie Night and Stats, opening the current month.
- A **Settings** page holding People, Locations and Lists as sections on one page.
- The movie page's Lists button sits in the rating row and looks like a real button.
- Who watched defaults to adults for R, PG-13 and NC-17, everywhere a watch is logged without naming people.

### Non-goals

- Logging, moving or deleting a watch from the Calendar. It is read-only.
- Per-person Calendar views or showing who watched on the Calendar.
- Redesigning People, Locations or Lists. Settings moves them; their behaviour and rules stay the same.
- New settings. Nothing else moves into Settings now.
- Changing how old watches read. The rating rule applies only to new logs.

### Anti-goals (fails even if it technically works)

- **A poster on a night that didn't happen.** A watch logged by time bucket ("1–2 Years") is stored with an
  *estimated* `watched_date` (`app.py`, `WATCH_BUCKET_OFFSETS`), and that date must never appear on the Calendar.
- Notes or who was there showing on a public page.
- Horizontal page scroll, or tap targets under 44 px, at 360 or 412 px.
- Any new runtime dependency or build step (CLAUDE.md rule 1).
- Old watches suddenly reading differently on Stats or in Movie Night's "unseen by" filters.

## 2. Decisions

| # | Topic | Decision | Whose |
|---|---|---|---|
| 1 | Calendar look | Same as the Christmas app: poster month grid, Sunday first, month arrows, "+N" badge, tap a day for a list | Nick |
| 2 | Calendar range | Every dated watch in Matinee, any year | Nick |
| 3 | Calendar visibility | **Public when the site is public** (`PUBLIC_VIEW`), because dates are already public (`browse.PRIVATE_WATCH`: "dates and counts stay public"). Only movies on it, never notes or people | my call, Nick may override |
| 4 | Undated watches | Left off the Calendar: any watch with a `date_bucket`, or with no `watched_date` | my call |
| 5 | Settings shape | One page with sections People, Locations, Lists | Nick |
| 6 | Settings entry | A gear icon button at the end of the editor's top bar, `aria-label="Settings"`, at `#/settings`. Editors only | my call |
| 7 | Top bar | People, Locations and Lists buttons leave it. Calendar is added | Nick |
| 8 | Lists button | Moves into the rating row on the movie page and becomes a normal (not ghost) button with the list icon and "Lists" | Nick |
| 9 | Rated default | R, PG-13 and NC-17 start as adults only. G, PG, NR, Unrated and no rating start as everyone | Nick |
| 10 | Which rating | The effective certification: the editor's override if set, else TMDB's US one (`browse` already computes this as `certification`) | my call |
| 11 | Where the rule applies | Everywhere a log omits `attendees`: the movie page (Watched today, Log date, buckets) and the MCP `log_watch` (both go through `people.default_attendees`). Movie Night only recommends and never logs, so it is unaffected | my call |
| 11a | PG-13 through Claude | MCP `log_watch` treats PG-13 as everyone; R and NC-17 still default to adults | Nick |
| 12 | History | Only new logs. `recommend.attendance_of` and `SEEN_CTE` (how watches *without* attendance rows read) do not change | my call |

## 3. Calendar

### Which watches count

A watch is placed on its `watched_date` when `watched_date` is set **and** `date_bucket` is NULL. Nothing else.
(Matinee stores a plain local date, not a timestamp, so there is no midnight or 4 am rule as in the Christmas app.)
A rewatch is a second watch row, so a movie can appear on several days. Two watches of the same movie on the
same day show once.

**Scenario.** *The 40-Year-Old Virgin* watched 2026-11-14; *Elf* watched 2026-11-14 and again 2026-11-28;
*Jaws* logged with bucket "1–2 Years" (stored `watched_date` 2025-11-19). For `month=2026-11`: Nov 14 shows
*The 40-Year-Old Virgin* with "+1", Nov 28 shows *Elf*. *Jaws* appears nowhere, including November 2025.

### API

`GET /api/calendar?month=YYYY-MM`. It is a public read, so it follows `PUBLIC_VIEW` like every `/api/` route and
must pass `tests/test_privacy.py`. Register it in the `("/api", False), ("/admin/api", True)` loop in `app.py` like the
other public reads, so editors read it through `/admin/api/calendar` when `PUBLIC_VIEW` is off. `month` defaults to
the current month by `lists.pacific_today()`; anything else that is not a real month is a 400 `{"error": "month must look like 2026-11"}`.
An empty month is `"days": {}`.

```json
{
  "month": "2026-11",
  "today": "2026-11-19",
  "days": {
    "2026-11-14": [
      {"title_id": 41, "title": "The 40-Year-Old Virgin", "year": 2005, "poster_url": "/images/..."},
      {"title_id": 7, "title": "Elf", "year": 2003, "poster_url": "/images/..."}
    ]
  }
}
```

Within a day, order by the watch's `created_at`, then `id` (the first logged shows on the cell). `poster_url`
is built the way the grid builds it (custom poster first). No notes, people or watch ids in the response.

Code goes in a new route-free module, **not** `calendar.py` (it would shadow the standard library): use
`watched_days.py`, added to `_SPLIT_MODULES` and to `installer/build.sh`. The route stays in `app.py`.

**MCP (rule 6):** an assistant may want "what did we watch in November?". Add a read tool `watch_calendar(month)`
returning the same days, compactly (title and year per day), with a test and a line in `docs/mcp-setup.md`.

### UI (412 px phone first)

- Route `#/calendar/YYYY-MM`; `#/calendar` opens the current month. Back steps between months.
- Header link "Calendar" with `icon("calendar")`, for editors and public visitors, placed after Movie Night (and
  after Wanted for editors), before Stats. It gets `aria-current="page"` like Stats. Also add "Calendar" to the
  footer links beside Stats.
- Month title with 44 px previous/next arrows, both always enabled.
- Seven columns, Sunday first. A day with movies is a button showing the day number and the first movie's poster;
  "+N" when there are more; empty days are not buttons. Today has a ring. Each button has an `aria-label` such as
  "November 14: The 40-Year-Old Virgin, Elf".
- Tapping a day opens a sheet titled with the date, one row per movie (poster, title, year) linking to
  `#/t/<id>`. Same behaviour for one movie or several.
- An empty month says "Nothing watched in November 2026."
- Posters that fail to load fall back as the grid's do. Day numbers and badges stay readable over posters.
- New `static/js/calendar.js`; follow CLAUDE.md rule 1 for imports and exports.

Port from the Christmas app's `static/js/calendar.js` and `watched_days.py` where they fit, adapting to Matinee's
modules, tokens and route names; don't copy its theme or Christmas styling.

## 4. Settings

- Route `#/settings`, editors only. A public visitor gets the same "Editor Sign-in Required" panel `#/wanted`
  shows (`wishlist.js`).
- Header: a gear icon button at the end of the editor bar, after Add Movie, `aria-label="Settings"`, 44 px. There
  is no gear in `static/js/icons.js` yet; add a `gear` icon there.
  Remove the Locations, People and Lists buttons and their listeners from `router.js`.
- The page: "Settings" heading, then three sections in this order, each with its own heading: **People**,
  **Locations**, **Lists**. Each shows what its pop-up shows today and does the same things (add, rename,
  reorder, colours, adult/kid, active, list windows, delete with the same confirmations and errors). The way to
  get there is to render the existing managers' content into a page section instead of a modal; keep one copy of
  each manager's code, not a modal copy and a page copy.
- The three managers are opened only from the header (`router.js`) and the locations manager re-opening itself
  after a write, so no other callers move. The locations manager's self-re-render must re-render its section.
- Text that sends people to the old buttons changes to Settings: the `NO_PEOPLE` message in `mcp_server.py` and
  `recommend.py` ("Add them under People in the admin view") and any README wording.
- The movie page's Lists sheet (membership of one movie) is unchanged and stays a sheet.

## 5. Lists button on the movie page

- Move the editor's `#btn-title-lists` out of the list-chip row into the rating row on the movie page, after the
  rating controls. It becomes a regular `btn btn-sm` (not `btn-ghost`) with `icon("list")` and "Lists", 44 px tall
  on phones. It opens the same membership sheet.
- The list chips stay where they are and still open each list. If the movie is on no lists, the chip row
  disappears for editors (it already does for the public).
- The rating row must not overflow at 360 px; if it can't fit, the button wraps to its own line below the slider.

## 6. Who watched: rated default

- Adults-only certifications: `R`, `PG-13`, `NC-17`. Put them in one constant in `people.py` (e.g.
  `ADULT_CERTS`). Everything else (`G`, `PG`, `NR`, none) means everyone.
- `people.default_attendees(con, watched_date, date_bucket)` gains the title (or its effective certification): it
  returns active adults when the watch is old (current rule) **or** the effective certification is adults-only.
  `app.py`'s log route passes it.
- The movie page's `defaultIds(when)` in `static/js/detail.js` applies the same rule, so the toggles start with
  kids off for these movies and `attendeesBody` still sends nothing when the toggles match the default. When the
  editor changes the certification override on the page, the toggles reset to the new default (unless the editor
  already changed them by hand).
- **No active adults** with an adults-only movie (my call): the rating rule doesn't apply and the default is every
  active person, written as rows. Writing no rows would read as `assumed_everyone` anyway (`recommend.attendance_of`),
  so this just makes it explicit. The old-watch rule keeps its current behaviour.
- **Through Claude (MCP `log_watch`), PG-13 counts as everyone** (Nick, 2026-11-20): Claude's "kids' night" picks
  go up to PG-13, and Nick wants those logged for the kids too. So a `log_watch` without `attendees` defaults to
  adults only for R and NC-17, and to everyone for PG-13. The movie page keeps PG-13 as adults only. How the MCP
  call tells the server (for example a request field the log route reads, so the rule stays in
  `people.default_attendees`) is the builder's call; explicit `attendees` always win.
- Update the `log_watch` docstring and `SERVER_INSTRUCTIONS` in `mcp_server.py` to say an R or NC-17 watch records
  adults unless `attendees` names the kids, and PG-13 records everyone.
- README: say what the default is.

## 7. Rules from CLAUDE.md this touches

- **No schema change**, no new setting, nothing new in `$DATA_DIR`.
- **Auth/privacy:** `/api/calendar` is public and must carry no notes or people (test it in
  `tests/test_privacy.py`). Settings changes no routes; the managers keep their `/admin/api/` writes.
- **MCP in step:** `watch_calendar` tool, `log_watch` docstring.
- **JS:** `node --check` on the new module; `tests/test_js_modules.py` passes.
- **Hygiene:** example titles and `movies.example.com` only.

## 8. Testing

- `tests/test_watched_days.py`: the scenario in §3; a bucket watch is never placed; a watch with no date is never
  placed; two watches of one movie on one day show once; a Dec 1 watch is not in November; `month=2026-13`,
  `month=Nov`, `month=` are 400; no `month` is the current month; empty month is `{}`; public response has no
  notes, attendees or watch ids.
- `tests/test_people.py` (or wherever `default_attendees` is tested): R, PG-13, NC-17 log adults only; PG, G, NR
  and no certification log everyone; an override of PG-13 on a TMDB PG movie logs adults; an override of PG on a
  TMDB R movie logs everyone; explicit `attendees` always win; an old PG watch still logs adults only.
- `tests/test_mcp_server.py`: `watch_calendar`.
- Playwright (`tests/test_calendar_ui.py` new, `tests/test_settings_ui.py` new, extend `tests/test_people_ui.py`
  and `tests/test_lists_ui.py` to reach their managers through Settings): no horizontal scroll at 360 and 412 px;
  44 px targets; arrows change month and hash; tapping a two-movie day lists both with working links; empty month
  message; Settings shows three sections and adding a person, a location and a list works from it; the header no
  longer has People, Locations or Lists; the movie page's Lists button sits in the rating row; Who watched starts
  with kids off on an R movie and everyone on a PG movie. Add new UI test files to the UI smoke job and to the
  CLAUDE.md list of Playwright files.

## 9. Left to the builder

- Cell sizing, badge style, and how the Settings sections are styled (match the Stats page).
- Whether Settings sections are collapsible. Default: not.
- The exact name and shape of the extra `default_attendees` argument.
- Whether the footer gets a Settings link for editors (fine either way).
