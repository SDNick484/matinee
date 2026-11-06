# Spec Delta

## Purpose

Finds movies on TMDB and keeps a local cache of their metadata and images, so the collection loads fast, keeps working when TMDB is slow or down, and stays within TMDB's terms of use.

## ADDED Requirements

### Requirement: Search TMDB by title
Editors SHALL be able to search TMDB by movie title. The system SHALL return up to 20 matches. Each match SHALL include its title, release year, poster thumbnail and TMDB id, and SHALL be marked if it is already in the catalog. Adult titles SHALL be excluded.

#### Scenario: Ordinary search
- **WHEN** an editor searches for "paddington"
- **THEN** results include *Paddington* (2014) and *Paddington 2* (2017), each with a poster thumbnail and year

#### Scenario: Owned results are marked
- **WHEN** an editor searches for "toy story" and the catalog already holds *Toy Story* (1995)
- **THEN** the *Toy Story* (1995) result is marked as already in the collection, and *Toy Story 2* is not

#### Scenario: No matches
- **WHEN** an editor searches for "teh godfathr part iv"
- **THEN** the system shows "No TMDB matches for 'teh godfathr part iv'" with a hint to check the spelling or try fewer words, and nothing is saved

#### Scenario: TMDB is rate-limiting
- **WHEN** an editor searches for "jaws" and TMDB answers 429 Too Many Requests
- **THEN** the system shows that TMDB is busy and to try again in a minute, keeps the search text in the box, and makes no automatic retry loop

#### Scenario: TMDB unreachable or not configured
- **WHEN** an editor searches for "jaws" and TMDB times out, or no TMDB token is configured
- **THEN** the system shows a message that says which of the two happened, and the rest of the app keeps working

### Requirement: Year hint in search
When a search ends in a four-digit year from 1888 through next year, the system SHALL use that year to narrow the TMDB search. If the narrowed search finds nothing, the system SHALL search again for the title without the year.

#### Scenario: Year picks the right remake
- **WHEN** an editor searches for "Dune 1984"
- **THEN** *Dune* (1984) is the first result, ahead of *Dune* (2021)

#### Scenario: Wrong year falls back
- **WHEN** an editor searches for "Elf 2004" (the film is from 2003)
- **THEN** the narrowed search finds nothing, a search for "Elf" with no year runs, and *Elf* (2003) is among the results

#### Scenario: Number in the title is not a year
- **WHEN** an editor searches for "Blade Runner 2049"
- **THEN** 2049 is not treated as a year, and *Blade Runner 2049* (2017) is returned

### Requirement: Cached movie metadata
For each Title, the system SHALL store the following from TMDB: title, original release year, runtime in minutes, overview, genres, IMDb id, TMDB collection id and name, TMDB vote average and vote count, and the time it was fetched. Pages SHALL be served from this cache, without calling TMDB.

#### Scenario: Collection and IMDb id are kept
- **WHEN** *Harry Potter and the Philosopher's Stone* (TMDB 671) is added
- **THEN** its Title records IMDb id tt0241527, the collection "Harry Potter Collection", genres Adventure, Fantasy and Family, and the fetch time

#### Scenario: Browsing does not depend on TMDB
- **WHEN** TMDB is down and a family member opens the poster grid and the *Coco* detail page
- **THEN** both pages load fully from the local cache

### Requirement: US certification
The system SHALL record a Title's US certification, using TMDB's US release dates. It SHALL prefer the certification on a US theatrical release, and then any other US release that has one. A Title with no US certification SHALL be recorded as having none, not as an empty rating.

#### Scenario: Theatrical rating
- **WHEN** *Spirited Away* (TMDB 129) is added
- **THEN** its US certification is PG

#### Scenario: No US certification
- **WHEN** a film is added whose TMDB release dates carry no US certification (for example *Kantara* (2022), an Indian release with no MPA rating)
- **THEN** its certification is recorded as none, and the detail page shows no rating badge rather than a blank one

### Requirement: Local poster and backdrop cache
When a Title is created or refreshed, the system SHALL download its poster and backdrop from TMDB's image service and serve them from the app. If an image is missing on TMDB or its download fails, the Title SHALL still be saved, shown with a placeholder, and the image retried on the next refresh.

#### Scenario: Images cached on add
- **WHEN** *Inside Out* is added
- **THEN** its poster and backdrop are served from the app's own image path, not from image.tmdb.org

#### Scenario: TMDB has no poster
- **WHEN** a Title is added whose TMDB record has no poster
- **THEN** the grid shows a placeholder card with the title and year in place of the poster

#### Scenario: Image service refuses the download
- **WHEN** *Ratatouille* is added and image.tmdb.org answers 429 for its backdrop
- **THEN** the Title and its copy are saved, the poster (which downloaded) is shown, the detail page uses a plain hero without a backdrop, and the backdrop is fetched again on the next refresh

### Requirement: Manual refresh from TMDB
Editors SHALL be able to refresh a single Title from TMDB. A refresh SHALL update the Title's cached metadata, images and fetch time, and SHALL NOT change its copies. If a refresh fails, the existing cached data SHALL be kept.

#### Scenario: Successful refresh
- **WHEN** an editor taps "Refresh from TMDB" on *Wonka* and TMDB now lists a longer overview
- **THEN** the overview and fetch time are updated, and *Wonka*'s copies are unchanged

#### Scenario: Movie removed from TMDB
- **WHEN** an editor refreshes a Title and TMDB answers "not found" for its id
- **THEN** the cached metadata is kept, and the editor is told that TMDB no longer has that id

#### Scenario: Refresh while rate-limited
- **WHEN** an editor refreshes *Encanto* and TMDB answers 429
- **THEN** the cached metadata and fetch time are unchanged, and the editor is told to try again in a minute

### Requirement: TMDB attribution
The app SHALL have an About page, linked from every page, that shows the TMDB logo and the notice "This product uses the TMDB API but is not endorsed or certified by TMDB."

#### Scenario: About page
- **WHEN** a family member opens About from the footer on a phone
- **THEN** they see the TMDB logo, the attribution notice and a link to themoviedb.org
