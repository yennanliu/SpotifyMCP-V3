# Recommendation V2: Spotify-only recommendations — design and feasibility

[繁體中文](recommendation-v2.zh-TW.md)

Status: **proposal, no code yet**. Last checked against Spotify's docs: 2026-10-18.

## 1. The request

Build a V2 of `recommend_from_playlist` that uses **Spotify as its only data source**:

1. Take a playlist or a list of songs as input.
2. Look up each song on Spotify, one by one.
3. Collect the songs Spotify considers similar to each one as candidates.
4. Rank the candidates and return the top N.

V1 (today) gets its similarity signal from Deezer and Last.fm, then maps the results
back to Spotify by artist name. V2 would drop both outside services.

## 2. TL;DR

| Question | Answer |
| --- | --- |
| Can we ask Spotify "which songs are similar to this one"? | **No.** Every Spotify endpoint that answers that is closed to our app (§3.1). |
| Can we build a Spotify-only V2 anyway? | **Yes, with weaker signals.** We can build it from collaborators, catalog, genre and era. Quality will be below V1's (§4, option C). |
| Is there a Spotify-only design that matches what was asked? | **Yes, but only in Extended Quota Mode**, which this project can't realistically get. It works by counting which songs show up in the same public playlists (§4, option D). |
| Is there a bigger problem first? | **Yes.** Spotify's February 2026 Development Mode changes probably break parts of V1 too (§3.2). We should verify that before V2 work starts. |
| Recommendation | Run a 1-day spike (§7). Then fix V1's compatibility. Then build V2 as option C, behind a `source` parameter, and keep V1's hybrid mode as the default. |
| Effort | Spike 1 d. Option C: 4–6 engineer-days in total. Option D adds about 3–4 d, but only if we get Extended Quota. |
| Money | $0 API cost. The app owner needs Spotify Premium (required for Development Mode since 2026). No new infrastructure. |

## 3. Feasibility: what Spotify actually lets us call

### 3.1 The similarity endpoints are closed (November 2024)

On 2024-11-27 Spotify closed these to every app created after that date, and to every
Development Mode app without a pending extension request
([announcement](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)):

| Endpoint / feature | What it would have given V2 |
| --- | --- |
| `GET /recommendations` | Similar tracks from seed tracks. This is exactly what the request asks for. |
| `GET /artists/{id}/related-artists` | Spotify's "Fans also like" list |
| `GET /audio-features`, `/audio-analysis` | Tempo, energy, valence and similar, for ranking by sound |
| Algorithmic and Spotify-owned editorial playlists | Song Radio, "This Is …", Daily Mix and editorial playlists all return 404 |
| `GET /browse/featured-playlists`, `/browse/categories/{id}/playlists` | Curated candidate pools |

The "similar songs" and "Song Radio" lists in the Spotify app come from these same
closed systems. The public Web API offers no other route to them.

### 3.2 The Development Mode lockdown (February / March 2026)

Spotify then tightened Development Mode again. The new rules applied to new apps from
2026-02-11 and to existing apps from 2026-03-09
([migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)).
These changes affect both V1 and V2:

| Change | Effect on us |
| --- | --- |
| `GET /playlists/{id}/tracks` renamed to `/playlists/{id}/items` | V1's `_playlist_tracks` calls the old path. |
| Playlist `items` are returned **only for playlists the user owns or collaborates on**. For any other playlist you get metadata without items. | With Client Credentials there is no user, so we probably can't read any playlist's tracks. This breaks V1's input step and rules out option D. |
| `GET /artists/{id}/top-tracks` removed | V1 picks tracks this way, and so does the `get_artist_top_tracks` tool. |
| Batch `GET /artists?ids=…` and `/tracks?ids=…` removed | V1 fetches playlist genres this way. Each ID now needs its own call. |
| Search `limit` max cut from 50 to 10 (default from 20 to 5) | `search_tracks` and `search_artists` accept up to 50, so anything above 10 now gets a 400. |
| `popularity` removed from tracks, albums and artists; `followers` removed from artists | We can't rank by popularity. `_track_summary` already handles a missing `popularity`. |
| App owner must have Spotify Premium | This is an ongoing cost for whoever owns the Client ID. |

**Caveat:** I couldn't call the live API from this environment (no credentials). Our
CI tests are fully mocked, so they wouldn't catch any of this. Whether our app is
affected depends on its mode (Development or Extended Quota). That makes it the first
thing the spike (§7) checks.

### 3.3 Extended Quota Mode is effectively unavailable

Extended Quota Mode lifts the February 2026 restrictions. Since 2025-05-15, though, it
is reserved for "established, scalable, and impactful use cases", which in practice
means a registered business with a large user base
([criteria update](https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access)).
Even an app that gets Extended Quota today still can't use the endpoints in §3.1. Those
stay limited to apps that already relied on them before November 2024.

### 3.4 What still works in Development Mode with Client Credentials

These are the building blocks a Spotify-only V2 can use:

- `GET /search`: `type=track|artist|album|playlist`, max 10 per page, paged with
  `offset`. Track search supports the field filters `artist:`, `album:`, `year:`
  (including ranges such as `year:2005-2010`) and `genre:`.
- `GET /tracks/{id}`: one track, including its full artist credits (features and
  collaborators).
- `GET /artists/{id}`: one artist, including `genres` (which is empty for many artists).
- `GET /artists/{id}/albums`: an artist's albums, singles and `appears_on` releases.
- `GET /albums/{id}`: an album and its first 50 tracks.
- `GET /playlists/{id}`: metadata only, unless we own the playlist.

### 3.5 Not an option: unofficial endpoints

The web player calls private endpoints ("Song radio", "Fans also like", "Similar
songs") using a token taken from a logged-in browser session. Using them would break
Spotify's Developer Terms (no reverse engineering or scraping), could get the Client ID
or account banned, and fails without warning whenever Spotify changes them. **Out of
scope.**

## 4. Design options

| | A. Spotify similarity endpoints | B. Unofficial web-player API | C. Spotify-native signals | D. Playlist co-occurrence |
| --- | --- | --- | --- | --- |
| Matches the request ("Spotify's similar songs") | Exactly | Exactly | Partly | Closely (crowd-sourced similarity) |
| Works in Development Mode | ❌ closed | ⚠️ against the Terms | ✅ | ❌ can't read others' playlist items |
| Works in Extended Quota | ❌ only for pre-2024 apps | ⚠️ against the Terms | ✅ | ✅ |
| Discovery (surfaces new artists) | High | High | **Low to medium** | High |
| Needs name matching across services | No | No | No | No |
| Verdict | Infeasible | Rejected | **Recommended** | Only with Extended Quota |

## 5. Algorithm

### 5.1 Shared pipeline

```
input  ──►  seeds  ──►  candidate generation  ──►  scoring  ──►  diversify  ──►  top N
(playlist   (≤20 tracks, (one or more signals;      (weighted   (round-robin
 or songs)   one by one)  each candidate is a        votes)       by seed,
                          Spotify track ID)                       artist cap)
```

**Input.** V2 accepts either a playlist reference or a list of songs, where each song
is a track URL/URI/ID or a `"title - artist"` string. A song list sidesteps the
playlist-items restriction completely, so it should be the main path in Development
Mode. A playlist is only readable if the app owns it, or if the app has Extended
Quota.

**Seeds.** Resolve each song to a Spotify track:

- an ID costs one `GET /tracks/{id}`;
- a string costs one `search type=track` with `track:"…" artist:"…"`, keeping the first
  result that matches exactly after normalization.

Cap the seeds at `MAX_SEEDS = 20`, the same cost bound V1 uses. For a long playlist,
pick seeds by artist frequency, which is how V1 picks them, so the playlist's main
artists come first.

**Diversify and output.** Reuse V1's `spread()` idea. Every candidate gets a "lead
seed" (the seed that voted for it most). Seeds take turns, and no artist gets more than
`tracks_per_artist` picks. Drop the seed tracks themselves, and drop duplicates that
share an ISRC (`external_ids` was restored in March 2026). If ISRC is missing, fall
back to a normalized `title + primary artist` key. Each pick keeps a `because` field
naming the seeds that led to it.

### 5.2 Option C: Spotify-native signals (recommended)

For each seed track `s` (artist `a`, release year `y`, artist genres `G`), collect
candidates from four signals. Each signal has a weight `w`:

| Signal | How | Calls per seed | `w` | Why it helps |
| --- | --- | --- | --- | --- |
| **C1 Collaborators** | Other artists credited on `s`, plus artists on `a`'s `appears_on` releases (`/artists/{a}/albums?include_groups=appears_on`). Take tracks from their latest albums. | 1–3 | 1.0 | An artist who features with the seed artist is the strongest similarity signal Spotify still exposes. |
| **C2 Genre + era search** | `search type=track q=genre:"g" year:(y-3)-(y+3)` for the top 1–2 genres in `G`, 10 results each | 1–2 | 0.7 | Same scene, same period. Skipped when `G` is empty. |
| **C3 Genre artist search** | `search type=artist q=genre:"g"`, then each artist's latest album | 2–3 | 0.5 | Reaches artists that C1 and C2 miss. |
| **C4 Same artist, deeper cuts** | `/artists/{a}/albums`, then tracks from albums other than the seed's | 1–2 | 0.3 | A safe fallback for niche seeds. It's low-discovery, so its weight is low. |

Scoring:

```
score(c) = Σ_seeds  weight(s) · Σ_signals  w_signal · rank_decay(c, signal, s)
rank_decay = 1 - rank / len(list)                      # same rule V1 uses
weight(s)  = how many times s's artist appears in the input   # same as V1
bonus      × 1.3 when c is reached by ≥2 different seeds  (agreement)
penalty    × 0.5 when c's artist is already in the input   (favor discovery)
```

There's no popularity tiebreaker, because Spotify removed that field. Ties break by the
number of distinct seeds, then by release date (newest first).

**Known weaknesses.**

- Genre tags are sparse and broad. Many artists have none, and C2/C3 then return
  nothing.
- Without collaborators or genres, results fall back to C4 (more of the same artist).
- Expect results that are less adventurous than V1's Deezer/Last.fm signal. It's
  roughly "same scene and its neighbors", not "fans of X also love Y".

### 5.3 Option D: playlist co-occurrence (Extended Quota only)

This is item-to-item collaborative filtering over public user playlists. It is the
closest thing to "Spotify's similar songs" that we could build ourselves.

```
for each seed s:
    playlists = search(type=playlist, q='"{s.title}" {s.artist}', up to 2 pages = 20)
              + search(type=playlist, q='{s.artist}', 1 page = 10)
    for each playlist p (skip null results, Spotify-owned and giant ones > 500 tracks):
        items = GET /playlists/{p}/items  (≤100 per page, read at most 2 pages)
        if s not in items and s.artist not in items: discard p   # search matched the name only
        for each track c in items, c ≠ s:
            co[c][s] += 1 / log2(2 + len(items))                  # long playlists count less

score(c) = Σ_s weight(s) · co[c][s] / sqrt(df(c))    # df = playlists containing c; damps generic hits
```

- Playlist search matches a playlist's *name and description*, not the songs in it. That
  is why the code checks that the seed is actually in the playlist. The share of
  playlists that pass this check is unknown, and the spike has to measure it.
- Candidates are already Spotify track objects, so there's no name matching. This also
  removes V1's main failure mode.

## 6. Cost and performance

Estimates assume 20 seeds, 6 concurrent workers (V1's setting) and about 200 ms per
Spotify call. Spotify doesn't publish its rate limits. It counts calls over a rolling
30-second window and returns `429` with a `Retry-After` header when you go over
([docs](https://developer.spotify.com/documentation/web-api/concepts/rate-limits)).
So **the request count is the cost that matters**.

| | V1 (today) | V2 option C | V2 option D |
| --- | --- | --- | --- |
| Spotify calls per run | about 60 (plus about 40 to Deezer/Last.fm) | **about 120–200** | **about 300–450** |
| Latency (estimated) | about 8 s (measured, before the 2026 changes) | about 6–10 s | about 15–25 s |
| Risk of 429 | Low | Medium. Needs `Retry-After` handling. | High. Needs a cache and backoff. |
| Outside dependencies | Deezer (non-commercial terms), optional Last.fm key | None | None |
| Money | $0 | $0 | $0, but needs Extended Quota |
| Infrastructure | None | None. Optional in-process TTL cache. | A TTL cache is strongly advised. |

Ongoing costs:

- The Spotify Premium subscription for the app owner. Development Mode has required it
  since 2026, whichever version we ship.
- A Development Mode app allows at most 5 users if it is created after Feb 2026, but
  that limit only matters for Authorization Code (user login) flows.

Ways to bring the cost down:

- Cache `GET /artists/{id}`, `/artists/{id}/albums` and search results for 24 h.
  Artists repeat a lot across seeds.
- Stop collecting early once there are 3×N candidates.
- Add backoff on `429` that respects `Retry-After`. Today `_api` raises on the first
  `429`.

## 7. Effort and plan

| Step | Work | Effort |
| --- | --- | --- |
| **0. Spike (do first)** | With real credentials, check the app's mode. Call the old and new playlist paths, `top-tracks`, batch `/artists`, and search with `limit=50`. Check whether `items` comes back for a public playlist we don't own. Sample 20 seeds and record how many have genres or collaborators (option C coverage). If Extended Quota, also measure the co-occurrence hit rate (option D). | 1 d |
| 1. V1 compatibility (separate PR) | Only if the spike confirms breakage: switch to `/items`, cap search at 10, replace `top-tracks` and batch `/artists`, add `429` backoff. | 1–2 d |
| 2. V2 option C | New `recommend_v2(songs \| playlist, limit, tracks_per_artist)` tool, or a `source="spotify"` parameter on the existing tool. Includes the input parser, the four signals, scoring, diversify, and respx-mocked tests matching the existing suite. | 2–3 d |
| 3. Offline evaluation | Hold out 20% of several owned playlists, recommend from the other 80%, and compare recall@N, artist diversity and novelty for V1 and V2. Its output decides the default. | 1 d |
| 4. Caching | In-process TTL cache for artist, album and search lookups | 0.5 d |
| — Option D | Only if Extended Quota: playlist search, verification, co-occurrence scoring, plus the cache | +3–4 d |

**Total for the recommended path:** 4–6 engineer-days for steps 0, 2, 3 and 4, plus
1–2 days for step 1 if needed.

## 8. Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Option C quality falls short of V1 | High | Ship it as an option, not a replacement. Let step 3's numbers decide the default. |
| Spotify cuts more Development Mode features | Medium (it has done so twice in 16 months) | Keep the signals modular so one going dark only removes one signal. Keep V1's hybrid mode. |
| A 429 storm on large inputs | Medium | Cap seeds, cache, back off with `Retry-After`. |
| Sparse genres make C2/C3 empty | High for niche or non-English artists | C1 and C4 still return candidates. Report `because` so users can see why. |
| Users expect the Spotify app's "similar songs" | High | Document plainly that the public API doesn't expose them. |

## 9. Decision needed

1. Approve the 1-day spike, which needs a real Client ID and secret.
2. After the spike, choose between:
   - **V2 = option C** alongside V1 (recommended), or
   - drop V2 and fix V1 only, if the spike shows option C's coverage is poor.
3. Option D only if the project ever gets Extended Quota.

## References

- [Changes to the Web API (2024-11-27)](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [Updating the criteria for Web API extended access (2025-04-15)](https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access)
- [February 2026 Web API Dev Mode changes: migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Update on developer access and platform security (2026-02-06)](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
- [Rate limits](https://developer.spotify.com/documentation/web-api/concepts/rate-limits)
