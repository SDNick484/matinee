# Loading a list of owned YouTube films

If you already own films on YouTube, you can paste each link into the digital copy by hand (open the film, edit the
digital copy, paste the address into **YouTube link**), or load a whole list in one request. A row is linked only when
it matches exactly one digital copy; every other row comes back with a reason, and nothing is guessed. The list is
personal: keep it out of any repository, and out of anywhere you share.

## The file

A JSON array, or a CSV with a header row, with these four columns (a CSV may start with a UTF-8 byte-order mark and
end with a blank line, as an export from a spreadsheet or from Matinee does):

```csv
title,year,video_id,play_link
The Dark Knight,2008,dK8x2LmQw9A,https://www.youtube.com/watch?v=dK8x2LmQw9A
"Princess Bride, The",1987,pB7rT4yUi0o,https://www.youtube.com/watch?v=pB7rT4yUi0o
Marvel Studios' Thor: Ragnarok,2017,-t0RrAgNaR0,https://www.youtube.com/watch?v=-t0RrAgNaR0
```

The same rows as JSON: `[{"title": "The Dark Knight", "year": 2008, "video_id": "dK8x2LmQw9A", "play_link":
"https://www.youtube.com/watch?v=dK8x2LmQw9A"}, ...]`. `year` may be a number or text. A video id is exactly 11
letters, digits, `-` or `_` (so it can start with `-`), and it must be the `v=` of the `play_link`.

## Try it first, then load it

Both requests need an editor key (`MATINEE_API_KEY`); the read key is refused. A dry run changes nothing and gives the
same answer the real load would. With `format=csv` the answer is the rows that did not load, as a spreadsheet you can
open and work through:

```sh
curl -sS -X POST -H "Authorization: Bearer $MATINEE_API_KEY" -H 'Content-Type: text/csv' \
  --data-binary @my-youtube-films.csv \
  'https://movies.example.com/admin/api/youtube-links?dry_run=1&format=csv' -o not-loaded.csv
```

When the not-loaded list looks right, load for real. This answers with JSON, `{"linked": 2, "already_linked": 0,
"not_loaded": [...], "dry_run": false}` (use `-H 'Content-Type: application/json'` and a `.json` file for a JSON list):

```sh
curl -sS -X POST -H "Authorization: Bearer $MATINEE_API_KEY" -H 'Content-Type: text/csv' \
  --data-binary @my-youtube-films.csv \
  'https://movies.example.com/admin/api/youtube-links'
```

Every link is written in one transaction: all of them or none. Loading the same list again links nothing new
(`already_linked` counts the rows done earlier) and only reports what is still unlinked. An id that is already on a
copy is never replaced, and the year is never loosened.

## How a title is compared

Case, accents and punctuation are ignored, `&` reads as "and", a leading "The", "A" or "An" is dropped, and a trailing
", The" moves to the front. These retail extras are removed first: `Marvel Studios'`, `Illumination Presents:`,
`Dr. Seuss'`, `(Unrated)`, a trailing `(YYYY)`, ` The Movie`, `: Based on a True Story`, ` - Director's Cut`,
`: Director's Definitive Cut` and `: Final Cut`. So "Marvel Studios' Thor: Ragnarok" (2017) meets "Thor: Ragnarok"
(2017), and "40 Year-Old Virgin, The" (2005) meets "The 40 Year-Old Virgin" (2005). "Dune" is not "Dune: Part Two",
and "Inception" listed as 2020 does not meet *Inception* (2010).

## Why a row did not load, and what to do

Each not-loaded row says why and, in `near`, which Matinee titles it came near and what Matinee holds of them
("Digital 4K · Movies Anywhere" or "Blu-ray only"). To fix one by hand: open the film, edit its digital copy, and
paste the link into **YouTube link**.

| Reason | What it means and what to do |
| --- | --- |
| The video_id isn't a YouTube id | The id is not 11 letters, digits, `-` or `_`. Correct it in the list. |
| There is no play_link | The `play_link` is empty, so the id could not be checked. Fill it in, or paste the link on the copy. |
| The play_link isn't a YouTube address | Use a `youtube.com/watch?v=` or `youtu.be/` address. |
| The video_id and the play_link are different videos | One of them is wrong. Open the video, copy its address, and use it on the copy. |
| The title or the year is missing | A row needs both to be matched. |
| Listed more than once for the same year | The same film appears twice, so there is no telling which id is right. Keep one row, or paste the right link on the copy. |
| This video_id is listed for more than one film | One id on two different films: one of them is wrong. |
| Matinee has this title under a different year | The year in the list and in Matinee differ (see `near`). Fix whichever is wrong, or paste the link on the copy. |
| Nothing in Matinee has this title | The film is not in Matinee yet, or is named very differently. Add it, then load again. |
| More than one title in Matinee has this title and year | Two titles match. Paste the link on the right one. |
| Matinee has it on disc only | There is no digital copy to link. Add the digital copy, then load again. |
| That title has more than one digital copy | Matinee cannot tell which copy is the YouTube one. Paste the link on the right copy. |
| The digital copy already has a different YouTube link | An id on a copy is never replaced. If the old one is wrong, clear it on the copy, then load again. |
| That YouTube id is already on another film | One id belongs to one copy. Check which film it really is. |

The not-loaded CSV has the columns `title, year, youtube_link, reason, near`: the address from your list is in
`youtube_link`, ready to paste. If the load answers 409 (something changed while it ran), nothing was loaded: run it
again.
