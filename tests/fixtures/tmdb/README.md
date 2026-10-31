# TMDB fixtures

These are hand-written in the shape of TMDB v3 responses (`/search/movie` and
`/movie/{id}?append_to_response=release_dates`). They are not recorded responses,
because no TMDB token was available when they were written. Ids, titles, years,
runtimes, genres, collections, IMDb ids and US certifications match TMDB as far
as known. Overviews, vote counts and image paths are placeholders.

Naming:
- `search-<query slug>[-<year>].json` answers `/search/movie`. A missing file
  means TMDB found nothing.
- `movie-<id>.json` answers `/movie/<id>`. A missing file means TMDB answers 404.

To replace one with a recorded response:

    curl -H "Authorization: Bearer $TMDB_READ_TOKEN" \
      "https://api.themoviedb.org/3/movie/129?append_to_response=release_dates&language=en-US" > movie-129.json
