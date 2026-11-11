# SDD ledger — plan: docs/superpowers/plans/2026-11-11-per-person.md

Spec: docs/superpowers/specs/2026-11-11-per-person-design.md (binding).
Branch: feat/people-attendance, cut from origin/main @ 98f5ef7 (merge of PR #28, spec + plan only; no implementation yet).
Baseline: `python -m pytest -q` → 855 passed, 0 skipped (sqlite3 CLI installed so test_restore_test.py runs; spec's "842 passed, 13 skipped" is the same suite with those 13 skipped). Playwright Chromium available locally.
Author: SDNick484 <SDNick484@users.noreply.github.com>, set in repo git config.

## Pre-flight scan

| Rows | Produces / consumes | Finding |
|---|---|---|
| T1 → T2 | T1 tables `people`, `watch_people`; T2 `SEEN_CTE` reads them, `has_people` tolerates their absence | Consistent (spec §4, §4.1). |
| T1 → T3 | T1 `list_people`; T3 `resolve_people` builds "People: …" list | Consistent; both order by `sort_order, id`. |
| T2 → T3 | `SEEN_CTE`, `attendance_of` used by `title_detail` | Effective attendees of an assumed watch must equal what `SEEN_CTE` counts (all people incl. inactive, or all adults). Ruling R2. |
| T2 → T4 | `parse_params` passes `audience`; T4 adds the public 400 | Between the T2 and T4 commits, `/api/recommendations?audience=kids` would resolve and return names publicly. Ruling R3. |
| T2/T4 → T5 | MCP DB mode reuses `recommend`; REST mode uses T4 query params | Consistent. Tool counts: today 6 read (incl. `search_tmdb`) + `rate_movie`, `log_watch` + `add_movie` = 9; with `list_people` 7/9/10, as T5 says. |
| T5 → T6 | `MatineeClient` signatures; Ask schema drift test | Consistent: 6 read tools + `show_results`. |
| T7 → T8, T9 | `people.js` helpers, `state.people` | Consistent. |
| T8 → T9 | `tests/test_people_ui.py` created in T8, extended in T9; CI ui-smoke runs both files | Consistent; skip-guard step parses one junit file covering both. |
| T1 | Tests vs code | `test_ninth_active_person_refused` says "a 9th inactive one is allowed via PUT" — POST has no `active` field in spec §6.1. Ruling R1. |
| T1 | Migration scope | Plan tests fresh + synthetic v8 only; CLAUDE.md rule 2 and Nick's request require every earlier version (v1–v8) and a populated v8 copy. Ruling R4. |
| T2 | Self-consistent | `SEEN_CTE` uses `date('now')`; tests must use dates relative to today. Noted in dispatch. |
| T3 | Self-consistent | OK. Privacy grep test is additive to `test_privacy.py`. |
| T4 | Self-consistent | OK. |
| T5 | Self-consistent | OK. |
| T6 | Self-consistent | OK. |
| T7 | Self-consistent | `_contrast` helper may not exist yet in `test_static_ui.py`; implementer adds it if missing. |
| T8 | Self-consistent | Playwright must run, not skip; uses `importorskip` like `test_ask_ui.py` so the Python matrix / Alpine jobs skip it. |
| T9 | Self-consistent | OK. |
| T10 | Ledger vs skill | Skill deletes the workspace at finish; Nick asked to keep the SDD ledger under `.superpowers/sdd/` (precedent: committed `2026-11-01-upc-scanner/progress.md`). Ruling R5. |

## Rulings

- Ruling: R1 — POST /admin/api/people takes no `active` field (spec §6.1 lists none). `test_ninth_active_person_refused` asserts: a 9th POST with 8 active → 400 `At most 8 active people.`; PUT `active: false` on one of the 8 succeeds and then a 9th POST succeeds (8 active + 1 inactive); PUT `active: true` on the inactive one while 8 are active → 400 `At most 8 active people.` — the spec names no `active` on POST and the limit is on *active* people — if wrong, POST later gains an optional `active`, additive.
- Ruling: R2 — An assumed watch's effective `attendees` are exactly the people `SEEN_CTE` counts for it: every person (active or not) for `assumed_everyone`, every adult (active or not) for `assumed_adults`, in `sort_order, id` order — so `attendees` and `seen_by` never disagree — if wrong, inactive people show as dots on old watches; a one-line filter change.
- Ruling: R3 — Keep the plan's task order; Task 2 does not add the public 400 (Task 4 owns it). The branch merges as one PR and no intermediate commit is deployed — if wrong, a bisect landing exactly on the T2/T3 commits sees a public audience leak; not reachable in production.
- Ruling: R4 — Task 1 adds a parametrized test migrating a database built from each historical schema (v1–v8, DDL taken from git history of `app.py`) to v9, twice, with a title, watch and rating surviving; and the Task 1 implementer also builds a *populated* v8 database with the v8 code from `origin/main` (titles, copies, locations, watches with dates/buckets/notes, ratings, OMDb, ask_log), copies it, migrates the copy twice with the v9 code, and diffs every v8 table row-for-row plus `PRAGMA integrity_check` / `foreign_key_check`. The scratch script stays out of the tree; results go in the task report and the PR body — Nick asked for it and CLAUDE.md rule 2 requires every earlier version — costs one extra test file's runtime if redundant.
- Ruling: R5 — Keep `progress.md` (this ledger) and commit it with `git add -f` in one `chore(sdd):` commit before the PR, following the committed upc-scanner ledger; delete only scratch briefs, reports and review packages — Nick asked to keep the ledger under `.superpowers/sdd/` — if wrong, one revertable commit.

## Progress
Task 1: dispatched (BASE 98f5ef7, implementer sonnet, agent a65a9f7)
Task 1: review approved (spec ✅, quality Approved). ⚠️ DDL-verbatim checked by controller: app.py:363-380 matches spec §4 exactly; no other test pins schema "8".
Task 1: minor (deferred): check_active_room count + write not in one transaction (two concurrent editors could reach 9 active) — negligible for one household.
Task 1: minor (deferred): edit_person/add_person with a non-object JSON body ([1], "x") can 500 — same pattern as existing routes.
Task 1: minor (carried into Task 3 dispatch): spec §10 "deactivate keeps watch_people rows" has no test yet.
Task 1: note: implementer added 400s for non-int sort_order / non-bool active (messages not in spec §6.1) and person_id in test_lan_security URL builder.
Task 1: complete (commits 98f5ef7..e0d21a5, review clean)
Task 2: dispatched (BASE e0d21a5, implementer opus, agent ab69ffe)
Ruling: R6 — audience 'everyone' with every person inactive raises the no-people message (spec gives no message for that case) — closest existing wording; costs a reworded message if Nick wants 'No active people.'
Task 2: review: spec ✅, quality Approved with 1 Important (next_in_franchise `ranked` CTE duplicated between household and audience paths, recommend.py:229-235 vs 259-265).
Ruling: R7 — Review fix rounds land as their own Conventional Commits (fix:/refactor:), not amended into the task commit — the branch is already pushed (stop hook) and amending would need a force push — costs a few extra commits in the PR history.
Ruling: R8 — Task 2 Minor 2 (all-digit non-ASCII or oversize id tokens → uncaught ValueError/OverflowError = 500) and Minor 3 (attendance_of defaults to local date while SEEN_CTE uses UTC date('now')) are pulled into fix round 1: both are load-bearing (Task 3 consumes attendance_of; a 500 on user input is a correctness bug) — costs a slightly larger fix diff.
Task 2: minor (deferred): ids inlined via _in_ids (int()-coerced, from people table) rather than bound ? params — not exploitable.
Task 2: minor (deferred): forgotten_gems audience filter repeats the MAX(seen) subquery; aud_cols computed in quick_picks/genre_mood where unused.
Task 2: minor (in fix round 1): no test for R6; no test that seen_by includes an inactive person with an assumed watch (R2).
Task 2: fix round 1/5 (4 addressed, 0 open; commits 1ea4d49..e62dcfe)
Task 2: minor (deferred): UTC-default test only catches the bug in hours when local and UTC dates differ.
Task 2: complete (commits e0d21a5..e62dcfe, review clean)
Task 3: dispatched (BASE e62dcfe)
Ruling: R9 — With people set up but none active, a log without `attendees` writes no watch_people rows (the watch reads as assumed by SEEN_CTE); a non-list `attendees` → 400 "attendees must be a list of person ids or names." (message not in spec) — spec is silent on both; behaviour degrades to today's — costs a reworded message or a different default if Nick prefers.
Task 3: review: spec ✅, 1 Important (tests/test_privacy.py:638-650 attendees-on-private-path guard cannot fail: regex splits decorator from def).
Ruling: R10 — Task 3 Minors 2 and 3 (exact R9 message + null/missing-key cases; all-inactive log test) ride along in fix round 1 since they touch the same test file — cheaper now than in the final fix wave — costs nothing if redundant.
Task 3: minor (deferred): test_privacy name check is trivially true (detail carries ids only).
Task 3: minor (deferred): non-scalar attendee items matched via repr(); unknown_person_message echoes unstripped item; orphan watch_people KeyError unreachable.
Task 3: fix round 1/5 (3 addressed, 0 open; commits c9c2323..71c47c7). Controller confirmed the forced-RED proof in task-3-report.md lines 24-25.
Task 3: minor (deferred): privacy AST test's _hits does substring match on string constants (false-positive risk, safe direction); dead JoinedStr branch.
Task 3: complete (commits e62dcfe..71c47c7, review clean)
Task 4: dispatched (BASE 71c47c7)
Ruling: R11 — Private ?attendance accepts only 'unknown'; any other value → 400 "attendance must be unknown."; the public guard fires on key presence even with an empty value — spec defines only 'unknown' and says an ignored filter must not look as if it worked — costs a reworded message.
Ruling: R12 — On /admin/api a present-but-blank audience= or unseen_by= answers 400 'audience is empty.' / 'unseen_by is empty.' (spec §5 'Empty after trimming' message, applied to the raw param); unknown audience on /admin/api/recommendations is now 400 (was an uncaught ParamError → 500) — consistent with spec §5 — Task 9's UI must never send a blank audience.
Task 4: review: spec ✅, 1 Important (R12 blank audience=/unseen_by= 400s untested). ⚠️ resolved by controller: SEEN_CTE has no ? placeholders (spec §4.1 text) and CARD_SELECT starts with plain SELECT (app.py CARD_SELECT).
Task 4: minor (ride-along in fix round 1): test_grid_sql_unchanged_without_the_new_params asserts CARD_SELECT in itself (vacuous).
Task 4: minor (deferred): blank checks duplicated inline in both routes; PEOPLE_PARAMS/audience/unseen_by/resolve_audience not in the AST privacy test's name set; unseen_by/attendance silently ignored on /admin/api/recommendations; misleading comment in test_titles_attendance_unknown.
Task 4: fix round 1/5 (2 addressed, 0 open; commits 52b6c35..ceecc35)
Task 4: complete (commits 71c47c7..ceecc35, review clean)
Note: a stop hook asked to re-author commits as Claude <noreply@anthropic.com> and rebase pushed history; declined — Nick's explicit instruction is author SDNick484 <SDNick484@users.noreply.github.com>.
Task 5: dispatched (BASE ceecc35)
Task 5: note: implementer touched ask.py (unseen_by/audience schema props only) because test_ask schema-drift test pins client signatures; rest of Ask stays in Task 6. TDD order slipped (code before tests); RED captured by restoring HEAD's mcp_server.py.
Task 5: review: Needs fixes — I1 (audience echo check gated on api_key: public mode vs an older server returns an unfiltered list), I2 (unrequested audience="kids" added to the kids'-night line, prompt_what_to_watch and docs recipe rows breaks no-people installs and keyless clients).
Ruling: R13 — I2: revert the kids'-night line, prompt_what_to_watch(kids=True) and the recipe rows to max_cert only; keep only the verbatim spec §8 sentence — spec says upgrading changes nothing by default and §8 only *adds* a sentence — if wrong, a one-line prompt tweak later.
Ruling: R14 — Task 5 M1 (mcp _db_attendance re-implements app.watch_attendance/title_seen_by line for line) and M2 (no assumed_adults DB-vs-REST equality case) ride along in fix round 1: move the effective-attendees + seen_by helpers into recommend.py and have app.py and mcp_server.py both call them — spec §4.1 wants one definition every consumer uses — costs a slightly larger diff touching app.py.
Task 5: minor (deferred): list_people repeats has_people's sqlite_master check; list_people on an older REST server says "too old to pick for specific people"; error types differ by mode (ParamError vs MatineeError, max_cert precedent); public-mode unseen_by vs an older server is undetectable (/titles has no echo) — document.
Task 5: fix round 1/5 (4 addressed, 0 open; commits f5c6f46..349d23f)
Task 5: minor (deferred): re-reviewer wondered whether app title_detail behaves with an empty people table vs MCP's has_people fallback — test_log_with_no_people (Task 3) covers the app path; final review to glance.
Task 5: complete (commits ceecc35..349d23f, review clean)
Task 6: dispatched (BASE 349d23f)
Ruling: R15 — Ask SYSTEM uses the spec §9 kids line verbatim, followed by one added sentence: 'If a tool answers that no people are set up, repeat the call without audience.' — §9 mandates the line, but the spec intro says upgrading changes nothing by default, and without the fallback an Ask on a no-people install would relay an error — costs one sentence of prompt if Nick prefers it gone.
Task 6: review approved (spec ✅, no Critical/Important). Commit trailer "Claude Opus 5.5" is the controller session's attribution line; kept.
Task 6: minor (deferred): test_run_tool_passes_audience does not cover the §9 Philosopher's Stone (old unresolved watch → offered to kids) leg; Task 2 tests pin the rule.
Task 6: complete (commits 349d23f..3da1894, review clean)
Task 7: dispatched (BASE 3da1894)
Ruling: R16 — people.js exports only loadPeople/openPeopleModal in Task 7; activePeople, personDotsHTML, personTogglesHTML, readToggles are defined but unexported until Tasks 8/9 import them — tests/test_js_modules.py rejects exports nothing imports (repo rule beats brief's 'Produces' list) — Tasks 8/9 must add them to the export line.
Task 7: review approved (spec ✅, no Critical/Important).
Task 7: minor (carried into Task 8): .person-toggle off-state text is --ink, spec §7.6 says --ink-2.
Task 7: minor (carried into Task 8): test_people_button_is_admin_only's 200-char isAdmin window would pass an unguarded loadPeople(); test_person_names_are_escaped checks only ${…name…}; test_dots_carry_full_name_labels' negative assert can't fire and positive needs only one label.
Task 7: minor (deferred): loadPeople swallows failure on post-change refresh → modal shows "No people yet" with no toast; focus lost on modal re-render; tap-target test covers only .person-toggle.
Task 7: complete (commits 3da1894..a6c4f05, review clean)
Task 8: dispatched (BASE a6c4f05)
Task 8: controller ran tests/test_ask_ui.py + tests/test_people_ui.py 5x after the implementer reported one unexplained failure: 5/5 green (7 passed each, ~12 s).
Task 8: review: Needs fixes — I1 refreshRow leaves an expanded (idx≥3) row hidden after Save/Cancel; I2 .watch-who-btn aria-label="Change who watched" hides attendee names from screen readers (§7.6); I3 Playwright _settled can return on the old DOM (probable flake source) + no-people test's fixed 100 ms wait.
Ruling: R17 — Task 8 Minors 1 (renderDetail serially awaits loadPeople on every render → reuse router's promise), 2 (disabled log buttons unstyled) and 3 (§7.2 desktop "row beside the button" missing — a spec requirement) ride along in fix round 1 — cheap, same files, and 3 is binding spec text — costs a larger fix diff.
Ruling: R18 — Accept the single 44 px .watch-who-btn wrapping the 16 px display dots as the tap target for §7.3 ("tapping the dots opens an editor"; the dots themselves stay spans) — the spec needs a tappable control and 16 px dots fail the 44 px rule — if wrong, swap the wrapper for a separate edit control.
Task 8: minor (carried into Task 9): personTogglesHTML now renders inactive ids passed in selectedIds — Movie Night must filter stored ids through activePeople().
Task 8: minor (deferred): SAFE_INTERP_PREFIX "esc(" accepts ${esc(a) + p.name}; TDD order slipped (RED proven by stash).
Task 8: fix round 1/5 (6 addressed, 0 open; commits a604d5e..4a50ef5)
Task 8: minor (deferred): §7.2 desktop layout (≥900 px) verified by hand at 1280 px, no Playwright assertion.
Task 8: complete (commits a6c4f05..4a50ef5, review clean)
Task 9: dispatched (BASE 4a50ef5)
Ruling: R19 — test_movie_night_stale_audience_falls_back preloads id 3 (active in state.people) with the first recommendations stub answering 400, instead of the plan's literal '99': filtering stored ids through activePeople() (Task 8 carry-over) turns '99' into Everyone before any request, so the server-400 fallback path needs an id the client still believes in (person deleted on the server after the SPA loaded). The '99' case has its own test (filtered locally → Everyone, no 400). Custom with every dot off disables Roll Again / Open Movie and sends nothing — costs a test rename if Nick wants the literal plan scenario.
Task 9: review approved (spec ✅ with ⚠️, no Critical/Important).
Task 9: minor (for final review — spec §7.4 deviation): pick-card seen_by dots render whenever seen_by is non-empty, not only when someone OUTSIDE the audience has seen it (movie-night.js:445-449).
Ruling: R20 — In Movie Night, when the audience row is shown, "Everyone" sends audience=everyone (not no param), so the visible state maps to one request and matches the stale-fallback retry — spec §7.4 "The request adds &audience=…" — difference vs no-param is only inactive people's recorded watches; applied in the final-review fix wave.
Task 9: minor (for final review): grid hash with unseen_by/attendance that the server rejects (public visitor, stale person) shows "Unable to load collection" with no way to clear the people filters — add a "Clear people filters" recovery, strip them for anonymous visitors.
Task 9: minor (deferred): "All Movies" stays checked alongside a people filter; grid overflow test only at 412 with popover closed; tautological assert tests/test_people_ui.py:714; static tests pin exact source lines.
Task 9: complete (commits 4a50ef5..0c87c31, review clean)
Task 10: dispatched docs step (BASE 0c87c31)
Task 10: docs commit c7100cd (trailer normalised to the session attribution line before push). Final review dispatched over 98f5ef7..c7100cd.
Final review (fable): Ready with fixes — 0 Critical; I1 people filter in the hash dead-ends the public grid (Public View link) and an editor grid with a stale person; I2 logging a 10+ year watch with no attendees records the kids; I3 MCP REST sends names in query strings → access log. Minors M1–M10. Ledger triage: everything "can stay" except T9 grid-hash (= I1).
Ruling: R21 — I2 is parked, not fixed: spec §6.2 says a log with no attendees records all active people and §7.2 says every toggle starts on; defaulting old watches to adults would rewrite an approved spec rule, and Nick said to ask rather than redesign. Flagged in the PR body and final message as an open decision — if Nick agrees with the reviewer, the fix is one conditional in add_watch (default to active adults when attendance_of would say assumed_adults) plus the SPA sending attendees only when the selection differs from all-active; until then a backfilled "10+ Years" watch records the kids and must be corrected with the row editor.
Ruling: R22 — R20 reversed per final review: Everyone keeps sending no audience param (pre-branch request, cannot 400); the stale-audience fallback retries with NO audience param instead of audience=everyone — avoids a guaranteed second 400 when all people were removed — costs nothing.
Ruling: R23 — Movie Night seen_by dots are filtered to people outside the audience (spec §7.4 literal), and the comment fixed — spec text is binding; reviewer called the superset defensible but the spec says "outside the audience".
Final fix wave: I1, I3, M1, M2(R22), M3(R23), M4, M5, M6, M7, M8, M9, M10 in one dispatch.
Final fix wave: commits c7100cd..0d62db1 (953a9d2, a67cdf0, 0d62db1); suite 1056 passed 0 skipped. Pre-existing flake noted: test_ask_ui::test_modules_load_versioned_once_and_every_view_renders times out on .about-view ~3/40 at c7100cd (base behaviour, handleRoute has no route token) — not introduced by this branch.
Final fix wave re-review: all 12 findings ADDRESSED, no new Critical/Important.
Final: parked — numeric ids in an MCP REST audience aren't checked against /people (a doubly-wrong list names the wrong token) — Ruling: stays; no leak, either way a 400 — cost: slightly different error text.
Final: parked — closing the People modal after a change re-runs handleRoute for the current route (resets the Add form / scanner, grid jumps to a stale scroll position) — Ruling: stays; only after editing People on those pages — follow-up candidate.
Final: parked — Close tapped while a People change is in flight skips the refresh until the next close — Ruling: stays; pre-existing reopen behaviour of change().
Final: parked — I2 (ruling R21) for Nick's decision.
Final: pre-existing flake test_ask_ui::test_modules_load_versioned_once_and_every_view_renders (~3/40 on main; handleRoute has no route token) — not made more likely by this branch per re-review.
Task 10: complete through final review (commits 98f5ef7..0d62db1).
Ruling: R21 — RESOLVED by Nick on 2026-11-11: a new "10+ Years" watch with default attendees records the active adults only. Implemented on fix/roadmap-review (review finding F2): app.default_attendees, the watch form's choose-then-Log bucket flow (kids off by default, attendees sent only when they differ), MCP log_watch date_bucket; spec §6.2, §7.2, §8 and README updated. Watches logged before the fix are not rewritten (host checklist H11).
