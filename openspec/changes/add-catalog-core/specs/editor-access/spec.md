# Spec Delta

## Purpose

Decides who may change the collection. The family can browse freely, but only signed-in editors can add, change or remove anything.

## ADDED Requirements

### Requirement: Public browsing
The public site (`/`) and its read-only JSON (`/api/...`) SHALL be usable without signing in. They SHALL offer the poster grid, detail pages, the About page and cached images, and SHALL NOT accept any change to the collection.

#### Scenario: Family member browses without signing in
- **WHEN** a family member opens movies.example.com on their phone, without a Cloudflare Access session
- **THEN** they can browse the grid and open *Encanto*'s detail page

#### Scenario: Public responses do not reveal editors
- **WHEN** anyone fetches *Encanto* from the public `/api/...`
- **THEN** the response has its copies, but not which editor's email added each one

#### Scenario: Public API refuses writes
- **WHEN** a request without an editor identity sends a POST, PUT or DELETE to any `/api/...` path
- **THEN** it is refused, and the collection is unchanged

### Requirement: Editor area
All editing pages and actions SHALL live under `/admin`. These include TMDB search and every add, edit, delete and refresh. Every `/admin/api/...` request SHALL require an editor identity, and SHALL be refused with 401 and the message "Sign in to edit" without one.

#### Scenario: Editor adds a movie
- **WHEN** an editor, signed in through Cloudflare Access, adds a Blu-ray copy of *Wall-E* under /admin
- **THEN** the copy is saved and records the editor's email as the person who added it

#### Scenario: Request through Cloudflare without a valid Access token
- **WHEN** a request arrives through the Cloudflare tunnel to `/admin/api/titles` without a valid Access JWT
- **THEN** it is refused with 401 "Sign in to edit"

### Requirement: Editor identity from Cloudflare Access
The system SHALL accept a request as coming from an editor only if its Cloudflare Access JWT is signed by the configured team domain and issued for the configured application audience. When an editor email allow-list is configured, the token's email SHALL also be on it.

#### Scenario: Token for a different Access application
- **WHEN** a request carries a valid Access JWT that was issued for another app's audience
- **THEN** it is refused as not an editor

#### Scenario: Email not on the allow-list
- **WHEN** an editor allow-list contains only specific authorized emails, and a valid token for another email arrives
- **THEN** the request is refused as not an editor

### Requirement: LAN editing
When LAN editing is enabled, a request that does not come through Cloudflare SHALL be treated as an editor. When it is disabled, LAN requests SHALL be refused like any other request without an identity.

#### Scenario: Editing from home Wi-Fi by IP
- **WHEN** LAN editing is enabled and an editor opens /admin on the server's LAN address at port 8769 from the home network
- **THEN** they can add and edit copies, which are recorded as added from the LAN

#### Scenario: LAN editing turned off
- **WHEN** LAN editing is disabled and the same LAN request is made
- **THEN** `/admin/api/...` writes are refused with 401

### Requirement: Editor controls only for editors
The UI SHALL show editing controls only when it is served under `/admin`. If an editor's session expires during an edit, the UI SHALL tell them to reload to sign in again, and SHALL keep what they had entered on the page.

#### Scenario: Session expires mid-edit
- **WHEN** an editor's Access session has expired and they save a new DVD copy of *Home Alone*
- **THEN** the save is refused, a message says "Your sign-in expired. Reload the page to sign in again.", and the form keeps their choices
