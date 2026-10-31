# Spec Delta

## Purpose

Keeps the family's movie collection as Titles (one per movie, keyed by TMDB id) and the Copies we own of each, so that one movie owned on Blu-ray and in Movies Anywhere is a single movie with two copies.

## ADDED Requirements

### Requirement: One Title per TMDB movie
The system SHALL store at most one Title per TMDB movie id. Every Copy SHALL belong to exactly one Title.

#### Scenario: Second copy of the same movie joins the existing Title
- **WHEN** the catalog already holds *The Dark Knight* (TMDB 155) with a Blu-ray copy and an editor adds a Digital HD copy of TMDB 155
- **THEN** the catalog still holds one Title for TMDB 155, now with two copies

#### Scenario: Remakes stay separate
- **WHEN** an editor adds *Dune* (1984, TMDB 841) and *Dune* (2021, TMDB 438631)
- **THEN** the catalog holds two separate Titles, each with its own copies

### Requirement: Copy kind, format and service
Each Copy SHALL have a kind of `physical` or `digital` and a format that matches the kind. Physical formats are 4K UHD, Blu-ray, 3D Blu-ray, DVD and VHS. Digital formats are Digital 4K, Digital HD and Digital SD. A digital Copy SHALL have a service: Movies Anywhere, Apple TV, Fandango at Home, Amazon, Google Play, Plex/Jellyfin or Other. A physical Copy SHALL NOT have a service.

#### Scenario: Valid physical copy
- **WHEN** an editor saves a copy of *Paddington 2* with kind `physical` and format `Blu-ray`
- **THEN** the copy is saved with no service

#### Scenario: Valid digital copy
- **WHEN** an editor saves a copy of *Frozen* with kind `digital`, format `Digital HD` and service `Movies Anywhere`
- **THEN** the copy is saved with those values

#### Scenario: Format does not match kind
- **WHEN** an editor saves a copy with kind `digital` and format `DVD`
- **THEN** the request is rejected with a message that DVD is a physical format, and nothing is saved

#### Scenario: Digital copy without a service
- **WHEN** an editor saves a copy of *Coco* with kind `digital`, format `Digital 4K` and no service
- **THEN** the request is rejected with a message asking which service it is on

#### Scenario: Physical copy with a service
- **WHEN** an editor saves a physical 4K UHD copy of *Top Gun: Maverick* and also sends service `Apple TV`
- **THEN** the copy is saved as physical 4K UHD and the service is dropped

### Requirement: Add a copy of a movie not yet in the catalog
When an editor adds a copy of a TMDB movie that has no Title yet, the system SHALL fetch that movie's details from TMDB. It SHALL then create the Title and the Copy together. If the TMDB fetch fails, the system SHALL create neither.

#### Scenario: First copy creates the Title
- **WHEN** an editor adds a Blu-ray copy of *Spirited Away* (TMDB 129) and the catalog has no Title for 129
- **THEN** a Title for *Spirited Away* (2001) is created from TMDB with its runtime, US certification and genres, and it has one Blu-ray copy

#### Scenario: TMDB has no movie with that id
- **WHEN** an editor adds a copy of TMDB id 99999999 and TMDB answers "not found"
- **THEN** the request fails with "TMDB has no movie with id 99999999", and no Title or Copy is created

#### Scenario: TMDB is rate-limiting
- **WHEN** an editor adds a DVD copy of *Home Alone* (TMDB 771) and TMDB answers 429 Too Many Requests
- **THEN** the request fails with a message that TMDB is busy and to try again in a minute, no Title or Copy is created, and the editor's choices stay on the add form so they can retry

#### Scenario: TMDB is not configured
- **WHEN** an editor adds a copy of a new movie and no TMDB token is configured
- **THEN** the request fails with a message that TMDB is not set up, and nothing is created

### Requirement: Add a copy of a movie already in the catalog
When an editor adds a copy of a movie that already has a Title, the system SHALL add the Copy to that Title without calling TMDB.

#### Scenario: Adding to an owned title makes no TMDB call
- **WHEN** the catalog holds *Elf* (TMDB 10719) and an editor adds a Digital HD copy on Apple TV, while TMDB is unreachable
- **THEN** the copy is added to the existing *Elf* Title, and the add succeeds

### Requirement: Tell the editor what is already owned
Before the editor confirms an add, the system SHALL show the copies already owned of the chosen movie.

#### Scenario: Already own it
- **WHEN** an editor picks *The Princess Bride* (1987) from search results and the catalog already has a DVD copy and a Digital HD copy on Movies Anywhere
- **THEN** the add form says "Already in the collection: DVD · Digital HD (Movies Anywhere)" before the editor saves

#### Scenario: Exact duplicate is allowed after a warning
- **WHEN** the catalog has a Blu-ray copy of *Jurassic Park* and an editor adds another Blu-ray copy (e.g. a second disc in the kids' room)
- **THEN** the add form warns that a Blu-ray copy is already owned, and saving creates a second Blu-ray copy

### Requirement: Edit and remove copies
Editors SHALL be able to change a Copy's kind, format and service under the same rules as adding one, and to delete a Copy. Deleting a Title's last Copy SHALL keep the Title, which then no longer counts as owned.

#### Scenario: Change format
- **WHEN** an editor changes the *Inception* copy from `Blu-ray` to `4K UHD`
- **THEN** the copy shows 4K UHD

#### Scenario: Delete the last copy
- **WHEN** an editor deletes the only copy of *Cars 2*
- **THEN** the copy is gone, the *Cars 2* Title still exists with no copies, and it no longer appears in the poster grid

### Requirement: Remove a title
Editors SHALL be able to delete a Title. Deleting a Title SHALL delete all of its copies and its locally cached images.

#### Scenario: Delete a title with copies
- **WHEN** an editor deletes *Cats* (2019), which has one DVD copy
- **THEN** the Title, its DVD copy and its cached poster and backdrop are removed

### Requirement: Date added
Each Copy SHALL record when it was added and which editor added it. A Title's date added SHALL be the date of its earliest current Copy.

#### Scenario: Date added comes from the earliest copy
- **WHEN** *Moana* got a DVD copy on 2026-10-01 and a Digital HD copy on 2026-10-15
- **THEN** *Moana*'s date added is 2026-10-01
