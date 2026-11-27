# Loading a list of Fandango at Home films

A digital copy can hold a Fandango at Home link beside its YouTube link, and Play starts a film on Fandango at Home
first when it has one. You can paste each link into the digital copy by hand (open the film, edit the digital copy,
paste the address into **Fandango at Home link**), or load a whole list in one request. This is the
[YouTube list load](youtube-links.md) for Fandango at Home: the same rules, the same options and the same answer. A
row is linked only when it matches exactly one digital copy; every other row comes back with a reason, and nothing
is guessed. The list is personal: keep it out of any repository, and out of anywhere you share.

The two loads are independent. A copy's YouTube link never stops a Fandango at Home link being loaded onto it, and
the reverse; each load writes only its own link.

## The file

A JSON array, or a CSV with a header row, with these four columns:

```csv
title,year,fandango_id,play_link
The Addams Family,1991,15429,https://athome.fandango.com/content/browse/details/The-Addams-Family/15429
"Princess Bride, The",1987,20931,https://www.vudu.com/content/movies/details/The-Princess-Bride/20931
```

The same rows as JSON: `[{"title": "The Addams Family", "year": 1991, "fandango_id": "15429", "play_link":
"https://athome.fandango.com/content/browse/details/The-Addams-Family/15429"}, ...]`. The `fandango_id` is the number
at the end of the film's Fandango at Home page, 1 to 12 digits. The `play_link` must be that page's address (on
`athome.fandango.com`, or the old `www.vudu.com` address of the same page) and its number must be the `fandango_id`;
a bare number is not an address.

**Check a list before you load it.** A list gathered by a script or by an assistant can be wrong in a way that looks
right: if the ids have slipped by one row against the titles, every row is well formed and the load links the wrong
film to each copy, and Play then starts the wrong film. Open a few of the addresses and check that each one is the
film named beside it, and run the dry run below before the real load.

## Try it first, then load it

Both requests need an editor key (`MATINEE_API_KEY`); the read key is refused. A dry run changes nothing and gives the
same answer the real load would. With `format=csv` the answer is the rows that did not load, as a spreadsheet:

```sh
curl -sS -X POST -H "Authorization: Bearer $MATINEE_API_KEY" -H 'Content-Type: text/csv' \
  --data-binary @my-fandango-films.csv \
  'https://movies.example.com/admin/api/fandango-links?dry_run=1&format=csv' -o not-loaded.csv
```

A healthy dry run counts the films whose links you already pasted by hand as `already_linked`: if one of those comes
back as "already has a different Fandango at Home link" instead, the list and the copy disagree, and one of them is
wrong. When the not-loaded list looks right, load for real. This answers with JSON, `{"linked": 2, "already_linked":
0, "not_loaded": [...], "dry_run": false}` (use `-H 'Content-Type: application/json'` and a `.json` file for a JSON
list):

```sh
curl -sS -X POST -H "Authorization: Bearer $MATINEE_API_KEY" -H 'Content-Type: text/csv' \
  --data-binary @my-fandango-films.csv \
  'https://movies.example.com/admin/api/fandango-links'
```

Every link is written in one transaction: all of them or none. Loading the same list again links nothing new and only
reports what is still unlinked. An id that is already on a copy is never replaced, and the year is never loosened.

## Why a row did not load

Titles are compared exactly as for YouTube (see [How a title is compared](youtube-links.md#how-a-title-is-compared)),
and the reasons are the YouTube ones with these Fandango at Home sentences:

| Reason | What it means and what to do |
| --- | --- |
| The fandango_id isn't a Fandango at Home id | The id is not 1 to 12 digits. Correct it in the list. |
| There is no play_link to check the fandango_id against | Fill in the page's address, or paste the link on the copy. |
| The play_link isn't a Fandango at Home address | Use the film's `athome.fandango.com/.../details/<name>/<number>` address. |
| The fandango_id and the play_link are different films | One of them is wrong. Open the page, copy its address, and use it on the copy. |
| This fandango_id is listed for more than one film | One number on two different films: one of them is wrong. |
| The digital copy already has a different Fandango at Home link | An id on a copy is never replaced. If the old one is wrong, clear it on the copy, then load again. |
| That Fandango at Home id is already on another film | One id belongs to one copy. Check which film it really is. |

The other reasons (listed more than once, a different year, nothing in Matinee, more than one title, disc only, more
than one digital copy) mean what they mean in the [YouTube table](youtube-links.md#why-a-row-did-not-load-and-what-to-do).
The not-loaded CSV has the columns `title, year, fandango_link, reason, near`. If the load answers 409 (something
changed while it ran), nothing was loaded: run it again.
