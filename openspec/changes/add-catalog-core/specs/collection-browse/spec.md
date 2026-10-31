# Spec Delta

## Purpose

Lets anyone in the family browse what we own, on a phone first: a poster grid they can search, filter and sort, and a detail page for each movie showing every copy we have.

## ADDED Requirements

### Requirement: Poster grid of owned titles
The home view SHALL be a grid of poster cards, one for each Title that has at least one Copy. Each card SHALL show the poster (or a placeholder), the title, the year and one chip per distinct format owned. On a phone-width screen, the grid SHALL fit at least three posters per row with no horizontal scrolling.

#### Scenario: Grid shows owned titles with format chips
- **WHEN** the catalog holds *Frozen* with a Blu-ray copy and a Digital HD copy on Movies Anywhere, and *Cars 2* with no copies
- **THEN** the grid shows *Frozen* with "Blu-ray" and "Digital HD" chips, and does not show *Cars 2*

#### Scenario: Phone layout
- **WHEN** a family member opens the grid in Chrome on an Android phone 412 CSS pixels wide
- **THEN** posters show at least three per row, and the page does not scroll sideways

#### Scenario: Empty collection
- **WHEN** the catalog has no copies yet
- **THEN** the grid shows an empty state. For an editor it links to Add, and for anyone else it says nothing has been added yet

### Requirement: Text search in the collection
The grid SHALL filter titles by a search box that matches title words. Matching SHALL ignore case and accents.

#### Scenario: Partial, accent-insensitive match
- **WHEN** a family member types "amelie" with *Amélie* in the collection
- **THEN** *Amélie* is shown

#### Scenario: No match in the collection
- **WHEN** a family member types "godzilla" and no owned title matches
- **THEN** the grid shows "Nothing in the collection matches 'godzilla'". For an editor, it also offers to search TMDB to add it

### Requirement: Grid filters
The grid SHALL offer these filters: kind (physical, digital or any), format, digital service and genre. Filters SHALL combine, and SHALL also combine with the text search. A filter SHALL list only the values present in the collection.

#### Scenario: Physical Blu-rays only
- **WHEN** a family member picks kind "physical" and format "Blu-ray"
- **THEN** only titles with at least one physical Blu-ray copy are shown

#### Scenario: Service filter
- **WHEN** a family member picks the service "Movies Anywhere"
- **THEN** only titles with a digital copy on Movies Anywhere are shown

#### Scenario: Genre plus search
- **WHEN** a family member picks the genre "Animation" and types "toy"
- **THEN** *Toy Story* and *Toy Story 3* are shown, but not the live-action *Toys* (1992)

#### Scenario: Filter options reflect the collection
- **WHEN** the collection has no VHS copies
- **THEN** VHS is not offered in the format filter

### Requirement: Grid sorts
The grid SHALL sort by title, by release year (newest first) or by date added (newest first). Title sort SHALL ignore a leading "The", "A" or "An". The default sort SHALL be date added.

#### Scenario: Title sort ignores articles
- **WHEN** the collection holds *The Incredibles*, *Inside Out* and *A Bug's Life*, sorted by title
- **THEN** the order is *A Bug's Life*, *The Incredibles*, *Inside Out*

#### Scenario: Default sort
- **WHEN** a family member opens the grid without choosing a sort
- **THEN** the most recently added title is first

### Requirement: Browse state in the URL
The current search, filters and sort SHALL be reflected in the page address. Reloading the page, or opening a shared link, SHALL restore the same view.

#### Scenario: Back from a detail page
- **WHEN** a family member filters to Animation, opens *Coco* and goes back
- **THEN** the grid is still filtered to Animation, at the same scroll position

### Requirement: Title detail page
Each Title SHALL have a detail page at a stable address. It SHALL show a blurred backdrop hero, the poster, title, year, runtime as hours and minutes, US certification, genres, overview, TMDB collection name, TMDB score, and every Copy, each as a format chip with its service for digital copies.

#### Scenario: Detail for a title with mixed copies
- **WHEN** a family member opens *The Lion King* (1994), owned on DVD and in Digital 4K on Movies Anywhere
- **THEN** the page shows the backdrop hero, "1994 · 1h 29m · G", genres, the overview, the TMDB score, a "DVD" chip and a "Digital 4K · Movies Anywhere" chip

#### Scenario: Title with no copies
- **WHEN** someone opens the address of *Cars 2*, which has no copies
- **THEN** the page shows its metadata and says it is not in the collection right now

#### Scenario: Unknown title
- **WHEN** someone opens the detail address of a Title id that does not exist
- **THEN** a "not found" page with a link back to the grid is shown

### Requirement: Editor actions on the detail page
For editors, the detail page SHALL offer: add a copy, edit or delete each copy, refresh from TMDB, and delete the title. These actions SHALL NOT be shown to anyone who is not an editor.

#### Scenario: Editor sees actions
- **WHEN** an editor opens *Moana* under /admin
- **THEN** they see "Add a copy", edit and delete on each copy, "Refresh from TMDB" and "Delete title"

#### Scenario: Family view has no edit controls
- **WHEN** a family member opens *Moana* on the public site
- **THEN** no add, edit, delete or refresh controls are shown

### Requirement: Format chip colors
Format chips SHALL use one consistent color per format family everywhere they appear: gold for 4K UHD and Digital 4K, blue for Blu-ray and 3D Blu-ray, gray for DVD and VHS, and teal for Digital HD and Digital SD. Services SHALL be shown as plain text, never as logos. Chip text SHALL meet WCAG AA contrast.

#### Scenario: Consistent chip colors
- **WHEN** the grid and the *Dune* (2021) detail page both show its 4K UHD copy
- **THEN** both chips are the same gold, and its Apple TV copy reads "Digital 4K · Apple TV" in text with no logo
