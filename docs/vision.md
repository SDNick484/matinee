# Matinee: Product Vision

## Purpose

The family's movies are spread across shelves, bins and five or more digital storefronts, and nobody can answer "do we own *Paddington 2*, and where is it?" without hunting. Matinee is the one place that knows: a self-hosted, phone-first catalog of the family's physical discs and digital libraries.

- **Know what we own**, in which format (4K UHD, Blu-ray, DVD, Digital HD, ...), and where it is: a shelf path such as Family Room › Media Cabinet › Shelf 2, or the digital service holding it.
- **Add things fast**: search TMDB by title, or scan a barcode with the phone camera.
- **Decide what to watch**: ratings, watch history and rule-based picks for movie night.
- **Be reachable by assistants**: an MCP server and headless API key let an AI assistant answer "where is it?" or log a watch.

Android Chrome is the primary device (camera scanning, at least 3 poster columns at 412 px); desktop comes second. The family browses without signing in; editors sign in through Cloudflare Access (or the LAN) to change anything. It runs as one OpenRC service on an Alpine LXC on the Proxmox homelab.

## Core model: Title vs Copy

- **Title**: one movie, keyed by TMDB id, with cached metadata (year, runtime, certification, genres, franchise, poster, OMDb scores). Ratings and watches attach to the Title, because "have we seen it?" does not depend on which disc is on the shelf.
- **Copy**: one owned instance of a Title. It is physical or digital, has a format, and has either a service (digital) or an optional shelf location (physical). *The Dark Knight* on a 4K UHD disc and in Digital HD on Movies Anywhere is one Title with two Copies.

A Title with no Copies stays in the database but is hidden from the public grid.

**Packages were considered and dropped.** The original brief and the phase-1 OpenSpec config described a third entity, a Package: one UPC product holding many Titles, such as a trilogy box set. The family handles discs individually, so a package record would add bookkeeping nobody uses. A box set is added as a separate Copy of each Title, and scanning a multi-title UPC returns several TMDB candidates for the editor to choose from.

## People

Editors can list the people in the household (Nick, Lauren, Midge, Alice, Paige), each an adult or a kid with a color. Every watch then records who was in the room, so "have *we* seen it?" becomes "has *Alice* seen it?". Movie Night, the MCP tools and Ask can pick for an audience (`everyone`, `adults`, `kids`, or names), and the grid can show what a group has not seen. Watches logged before people existed are treated as seen by everyone, or by adults only when they are over ten years old, until an editor fixes them. The feature stays dormant until the first person is added.

## Principles

1. **Small footprint.** One Flask file, one SQLite file, a vanilla JS single-page app. No framework, no bundler, no npm build step.
2. **Alpine packages only.** A new runtime dependency must exist as an Alpine `py3-*` package, because the installer uses `apk` and CI runs a musl/Alpine job. Outbound HTTP uses the standard library.
3. **Upgrades never need a manual step.** The installer is idempotent and keeps data and config. Schema changes go through an idempotent `migrate()`.
4. **Rule-based recommendations.** Picks come from plain SQLite rules (forgotten gems, an unwatched backlog, quick weeknight picks, genre and mood matching, an optional runtime cap), so they are explainable and instant.
5. **No scraping.** Metadata comes from documented APIs only: TMDB (with attribution, refreshing cached data older than about 6 months), OMDb (1,000 requests a day), and the UPCitemdb trial endpoint (100 lookups a day, cached).
6. **Explicit access boundaries.** Reads under `/api/` are public when `PUBLIC_VIEW=1`. Writes live under `/admin/api/` and need an editor. Personal data is public or editor-only by deliberate choice. Who watched what is editor-only: people, attendance and audience picks answer only to editors, and the public `/api` refuses them.
7. **Phone-first, home-theater look.** A dark palette, WCAG AA contrast on every text and chip pair, and no page-level horizontal scroll at 412 px.
8. **Consistent with the sibling apps.** Matinee follows the conventions of Roasted and Cookbook Collection, and notes why when it diverges.

## Non-goals

- Video playback. Matinee tracks the collection; it does not play it.
- Social features. It is a family tool, not a network.
- ML-based recommendations.

## How it evolves

New work goes through the Superpowers flow: a design spec in `docs/superpowers/specs/`, an implementation plan in `docs/superpowers/plans/`, then implementation with tests. Each change should be independently shippable. The retired OpenSpec material is kept under `openspec/` for history only.
