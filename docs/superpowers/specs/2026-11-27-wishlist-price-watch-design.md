# Wish list price watch (eBay): design

**Date:** 2026-11-27
**Repo:** `SDNick484/matinee`, `main` @ `3b5d02f` (schema v14)
**Size:** one feature in three PRs, each shippable on its own (§9)
**Status:** Draft, awaiting Nick's approval

---

## 1. Intent

Nick keeps a short wish list, almost all 4K UHD action movies. He is not chasing a sale he missed; when a good
price shows up, he wants to take it. He buys new or used and does not care who the seller is: a disc is the same
product everywhere, so the only question is the price.

He sets a target price on a wanted title. Matinee checks eBay once a day and tells him, through Home Assistant,
when a copy is listed at or under that price with shipping included. The Wanted list shows the cheapest matching
listing for each title whether or not it meets the target.

### Why eBay only

Researched on 2026-11-27. Of the stores Nick buys from, none can be read by a program for free:

| Source | Finding |
|---|---|
| Gruv | Every scripted request is answered with a bot challenge (HTTP 403), including `robots.txt` |
| Rasputin Music | The website has no online catalog or prices |
| Amazon | Keepa is the only solid source, about €49 a month. Amazon's own API needs 10 affiliate sales a month. camelcamelcamel has no API |
| Slickdeals | A search feed works, but their terms and `robots.txt` forbid automated access. Nick: skip it for now |
| eBay | Free with a developer key (Browse API). Covers used copies well |

Nick chose the free version. The consequence, which he accepted: this will not see a sale at Gruv, Amazon or
Rasputin's.

### Goals

- A target price, shipping included, on a want.
- A daily eBay check of every want that has a target price.
- The Wanted list shows, per title, the cheapest matching listing: its title, the total price, the item price and
  shipping, the condition, where it ships from, and a link.
- A Home Assistant notification when a listing is at or under the target, once per deal.

### Non-goals

- Any source other than eBay. The code should not pretend to be multi-source; a second source is a later design.
- Email. Nick agreed to start with Home Assistant only.
- Price history, charts, or "lowest ever".
- Auctions, best-offer haggling, or buying from inside Matinee.
- Picking one edition. Any release of the film in the wanted format counts (Nick).
- Matching by UPC. A UPC pins one edition, which Nick does not want.

### Anti-goals (fails even if it technically works)

- **Notifying every morning about the same listing.** One notification per deal (§5).
- **A price that leaves out shipping.** A $9.99 disc with $3.49 shipping is $13.48 everywhere it is shown or
  compared.
- A target price, a listing or a ZIP code showing on a public page or in the public `/api`.
- The eBay secret, the webhook URL or the ZIP code in a log line or an error message.
- A new runtime dependency (CLAUDE.md rule 1). Standard library `urllib` only, like `clients.py`.
- The nightly job failing loudly or blocking the other refreshes when eBay is down or the key is missing.

## 2. Decisions

| # | Topic | Decision | Whose |
|---|---|---|---|
| 1 | Source | eBay only; skip Slickdeals for now | Nick |
| 2 | Cost | Free only; no Keepa | Nick |
| 3 | Edition | Any release of the film in the wanted format | Nick |
| 4 | Wrong matches | An occasional wrong alert is fine as long as the notification shows the listing title, price and a link | Nick |
| 5 | Price compared | Item price plus shipping | Nick (my guess, he confirmed) |
| 6 | Listing type | Buy It Now (fixed price) only | Nick (my guess, he confirmed) |
| 7 | Frequency | Once a day, with the existing nightly refresh | Nick (my guess, he confirmed) |
| 8 | Repeat alerts | Once per deal, not every night | Nick (my guess, he confirmed) |
| 9 | Wanted list | Mockup 1: title, price with shipping, ships-from, per wanted title | Nick |
| 10 | Notification | Home Assistant only | Nick |
| 11 | eBay key | Nick registers a free eBay developer account and puts the key in the settings | Nick |
| 12 | Show a listing above target | Yes: the cheapest match is shown with the target beside it | my call |
| 13 | Show the condition | Yes ("Used, very good", "New") | my call |
| 14 | Which wants are watched | Only a want with a **physical** wanted format (`FORMATS["physical"]`) and a target price. "Any" and digital formats cannot have a target | my call |
| 15 | Where listings ship from | US item location only | my call |
| 16 | Webhook | A new setting `HA_PRICE_WEBHOOK_URL`, separate from `HA_WEBHOOK_URL`, as Play did with `HA_PLAY_WEBHOOK_URL`. An existing watch-logged automation must not fire on a price | my call |
| 17 | Check on save | Saving a want with a new or changed target checks that one title right away, through the same code as the nightly run (so it can notify) | my call |
| 18 | Listings with unknown shipping | Left out: a total cannot be worked out | my call |
| 19 | Money | Stored as integer cents, US dollars only | my call |
| 20 | Remembering what was notified | Every listing notified for a want is remembered (eBay item id and total), not only the last one, so two sellers trading the cheapest spot do not alert twice | my call |
| 21 | Failed notification | A POST that fails is not recorded as notified; the next check tries again | my call |
| 22 | `target_price` left out of a save | Kept as it was. Only an explicit null clears it, so an assistant changing a note cannot silently stop a watch | my call |
| 23 | Format change | Physical to physical: the target stays, the stored listing is discarded and looked up again. To Any or digital: the target is cleared | my call |
| 24 | Schema | Each PR adds only its own table or column (v15, v16, v17), so nothing is shaped by a guess about eBay's response | my call |
| 25 | Recording eBay fixtures | The PR 2 session records them live, once, with Nick's production key in its environment. Error fixtures (429, 500, bad token) are written by hand from the real error shape | my call |

## 3. What the editor sees

Editor-only throughout. All of it is under `/admin/api/`; the public `/api` carries none of it (§7).

### 3.1 Want form

One new field under Format: **Target price, with shipping**, a dollar amount. Hint: "Leave blank to keep it on
the list without watching the price." The field is shown only while the selected format is a physical one;
choosing Any or a digital format hides it and clears it.

### 3.2 Wanted list (`#/wanted`)

Under the page title: "Prices from eBay, checked <when>." (omitted when eBay is not configured). Each row gains a
price box:

- **At or under target** (highlighted):
  `$13.48 with shipping · at or under your $15`
  `"Heat 4K Ultra HD + Blu-ray Directors Definitive Edition" · Used, very good`
  `$9.99 + $3.49 shipping · ships from Fresno, CA · View on eBay ›`
- **Above target:**
  `$17.20 with shipping · target $12`
  `"Mad Max Fury Road (4K UHD + Blu-ray, 2015) NEW Sealed" · New`
  `$17.20, free shipping · ships from Edison, NJ · View on eBay ›`
- **Target set, nothing found:** `No listings found · target $12`
- **Target set, not checked yet:** `Target $12 · not checked yet`
- **No target (physical format):** `No target price set · Set one ›` (opens the want form)
- **Any or digital format:** no price box.

The row is a link to the title page today; "View on eBay" must be its own link that opens the listing in a new
tab without also following the row link. Phone-first: no horizontal scroll at 360 or 412 px, tap targets 44 px.

### 3.3 Title page

The want block shows the same price box as the Wanted row (my call).

### 3.4 Notification

Home Assistant receives an event (§6) and its automation builds the phone notification. The example automation in
`docs/home-assistant.md` produces:

> **Matinee · Heat (1995) 4K UHD is $13.48**
> Your target is $15. Used, very good. $9.99 + $3.49 shipping, ships from Fresno, CA.
> "Heat 4K Ultra HD + Blu-ray Directors Definitive Edition" (tap opens the listing)

## 4. Finding the listing

A new route-free module `prices.py` (added to `_SPLIT_MODULES` and `installer/build.sh`).

**Search.** One eBay Browse `item_summary/search` call per watched want:

- `q` = the film's title plus a format keyword: 4K UHD → `4K`; Blu-ray → `Blu-ray`; 3D Blu-ray → `3D Blu-ray`;
  DVD → `DVD`; VHS → `VHS`. The year is **not** in `q`: many listings leave it out.
- Fixed price only, US item location, US marketplace, the movies category, cheapest first, up to 100 results.
- The buyer ZIP (`EBAY_SHIP_ZIP`, §7) is sent when set, so eBay can quote shipping.

**Filter, in code.** A result is a match when all of these hold:

- Every word of the film's title appears in the listing title (folded with `common.fold`, punctuation ignored).
- The listing title names the format. For 4K UHD: one of `4K`, `UHD`, `Ultra HD`.
- The listing title has none of a short blocklist of non-disc phrases: `digital code`, `code only`, `slipcover
  only`, `slip cover only`, `case only`, `no disc`, `no 4k`, `artwork only`, `poster`. The list is a constant
  the builder may extend from the recorded fixture.
- The condition is not "For parts or not working".
- The price is in USD and a shipping cost is known (decision 18).

**Pick.** The match with the lowest total (item + shipping); ties go to the first returned.

A different film with the same name can still match (decision 4 accepts this).

### Unverified: the eBay response

eBay's documentation pages refused automated reading, so these are **from memory and must be confirmed against a
real response before code depends on them**: the token endpoint and client-credentials flow, the `EBAY_US`
marketplace header, category `617` for movies, whether `sort=price` exists, the names of the price, shipping
and location fields, and whether a search result carries a city and state or only a postal code prefix and
country. Read on the filters page and so reliable: `buyingOptions:{FIXED_PRICE}`, `conditions:{NEW|USED}`,
`itemLocationCountry:US`, `price:[..N],priceCurrency:USD`, and the `X-EBAY-C-ENDUSERCTX:
contextualLocation=country=US,zip=…` header.

So PR 2 begins with Nick's key. Nick registers an eBay developer account and a **production** keyset (a sandbox
key returns fake listings) and puts `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` and `EBAY_SHIP_ZIP` in the environment
of the session that builds PR 2. That session records one real token response and real search responses as
fixtures (token, secrets, ZIP and seller names removed) and corrects this section from what comes back
(decision 25). If a search result has no city, "ships from" shows what is there (state, or the ZIP prefix)
rather than making a second call per listing.

**Stop and report, do not improvise,** if the real response changes the design rather than a field name: the
key cannot call the Browse API; most fixed-price listings come back with no shipping cost even with a ZIP
(decision 18 would then hide most matches); there is no way to get results cheapest first; or there is no usable
movies category. Each of those is Nick's decision.

## 5. When a notification is sent

After a check, a want notifies when **all** hold:

- the best match's total is at or under the target, and
- this listing (by eBay item id) has never been notified for this want at this total or lower.

So: a new listing notifies; the same listing dropping further notifies; the same listing at the same price the
next morning does not, and neither does a listing that was notified, lost the cheapest spot, and is the cheapest
again (decision 20). Raising the target above a listing never yet notified does notify on the next check.
Changing the target or the format does not clear what was notified. The record is per want and goes when the
want goes (a copy in the wanted format is added, or the want is removed).

Nothing is sent when `HA_PRICE_WEBHOOK_URL` is empty; the listing still shows in the app, and is not recorded as
notified. A POST that fails is not recorded either, so the next check tries again (decision 21).

## 6. Data and interfaces

### Schema (`migrate()`, idempotent from v1; one version per PR, decision 24)

- **v15 (PR 1):** `wants.target_cents INTEGER` (NULL = not watched), added by `ALTER TABLE` when missing.
- **v16 (PR 2):** table `want_offers`, one row per watched want, keyed by `title_id` and deleted with the want:
  the listing's eBay item id, listing title, URL, item cents, shipping cents, total cents, condition text,
  ships-from text and `checked_at`. A check that finds nothing keeps the row with empty listing fields, so
  `checked_at` survives. Its columns are settled from the recorded response (§4).
- **v17 (PR 3):** table `want_notified`, one row per want and eBay item id with the lowest total notified,
  deleted with the want.

Clearing the target, or changing the format, deletes the `want_offers` row (decision 23). Nothing new is written to `$DATA_DIR` outside the database,
so `ops/matinee-backup` already covers it.

### Routes (all `@editor_required`, all under `/admin/api/`)

- `POST /admin/api/wants` accepts `target_price` (a number or numeric string in dollars, or null). Validation:
  greater than 0, at most 999.99, at most two decimals, and only with a physical format; otherwise 400 with a
  sentence, in the style of `want_format_arg`. A body with no `target_price` key leaves the target as it was;
  only an explicit null clears it (decision 22). The route otherwise stays a full replace of format and note.
  A save that changes the format to Any or a digital one clears the target (decision 23). After the save, when the target is new or changed and eBay is
  configured, that title is checked before the response (decision 17); a failed check does not fail the save.
- `GET /admin/api/wants` and the title payload's `want` gain `target_price` and `offer` (null, or the fields in
  §3.2 plus `checked_at` and `meets_target`). `GET /admin/api/wants` also gains a top-level `prices` object:
  whether eBay is configured and the newest `checked_at`.

### Nightly job

`ops/check_prices.py`, in the pattern of `ops/refresh_ratings.py`, run from `ops/matinee-refresh` when
`EBAY_CLIENT_ID` is set, with `|| true` like its neighbours. One token per run, a short pause between searches,
one log line per run to `/var/log/ops.log` (counts only). An eBay error on one title is logged and the run moves
on; a token failure ends the run with one line.

### Home Assistant event

POSTed to `HA_PRICE_WEBHOOK_URL` with the same care as `post_ha_event` (host only in logs, never the URL):

```json
{"event": "matinee_price_drop", "title": "Heat", "year": 1995, "title_id": 412, "wanted_format": "4K UHD",
 "target_price": 15.00, "total_price": 13.48, "item_price": 9.99, "shipping_price": 3.49,
 "condition": "Used, very good", "ships_from": "Fresno, CA",
 "listing_title": "Heat 4K Ultra HD + Blu-ray Directors Definitive Edition",
 "url": "https://www.ebay.com/itm/…"}
```

From the nightly script the POST is made directly and waited for (a daemon thread would die with the script).

### MCP (rule 6)

- `get_wishlist` returns `target_price` and `offer` for each want.
- `want_movie` takes an optional `target_price`.
- Tests in `tests/test_mcp_server.py`; `docs/mcp-setup.md` updated.

## 7. Settings, privacy, hygiene

New settings, each in `.env.example` with a comment line, the installer's `add_setting` list, and the app's
init-script `export` line (rule 7). They are not exported to `matinee-mcp`.

| Setting | Default | Notes |
|---|---|---|
| `EBAY_CLIENT_ID` | empty | eBay developer App ID. Empty = price watching off everywhere |
| `EBAY_CLIENT_SECRET` | empty | A **secret**. Never logged |
| `EBAY_SHIP_ZIP` | empty | Buyer ZIP for shipping quotes. Personal: never logged, never in an API response. Docs use `95008` |
| `HA_PRICE_WEBHOOK_URL` | empty | A **secret** (the webhook id). Empty = no notifications |

`ops/matinee-refresh` reads `/etc/conf.d/matinee` itself, so the nightly job gets them from there.

**Privacy (rule 5):** target prices and listings are editor-only, like the want they belong to. No new `/api/`
route; `tests/test_privacy.py` must still pass, and a test asserts the public title payload carries neither
`target_price` nor `offer`.

**Docs:** `docs/home-assistant.md` gains the price event and an example automation; a short section (README or a
new `docs/price-watch.md`, builder's choice) covers registering the eBay developer account and the four
settings, with OpenRC commands (`rc-service matinee restart`).

## 8. Tests (rule 3: recorded fixtures, no live network)

Fixtures in `tests/fixtures/ebay/`, recorded once with Nick's key and scrubbed:

- a search with matches, including at least one junk listing the filter must reject and one with no shipping cost;
- a search with no results (the lookup miss);
- a 429 quota answer and a 500;
- a token success and a token failure (bad credentials).

Covered: target validation; migration from v1 and from v14; the filter and the pick; the notify-once rule across
repeated checks, a price drop, a new listing and a raised target; nothing sent with no webhook; the save still
succeeds when the check fails; secrets and ZIP absent from logs; the public payload; the MCP tools; the installer
static test for the new settings and module; a UI test in `tests/test_wishlist_ui.py` for the target field and
the price box at 412 px.

## 9. PRs

Each starts from `main` after the one before it has merged.

1. **Target price.** Schema v15, the want form field, validation, `target_price` in the API and the MCP tools,
   and the Wanted and title-page boxes in their two states without a listing ("No target price set" and
   "Target $12"). Useful alone: the target is recorded and shown.
2. **eBay check.** Schema v16, `prices.py`, the three eBay settings, `ops/check_prices.py`, the check on save,
   `offer` in the API and MCP, and listings in the Wanted list and title page. Starts by recording the fixtures
   (§4), so it waits for Nick's key.
3. **Notification.** Schema v17, `HA_PRICE_WEBHOOK_URL`, the notify-once rule, the event, and the Home Assistant
   docs.

The three are a chain: each edits the code the one before it wrote, so they cannot run side by side.

## 10. Left to the builder

- Names inside `prices.py`, the exact column names, and the CSS.
- The pause between searches and the request timeout.
- Wording of validation messages and of the "checked <when>" line.
- Additions to the blocklist that the recorded fixture justifies.
