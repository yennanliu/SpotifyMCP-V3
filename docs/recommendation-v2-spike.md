# Recommendation V2 spike: results

[繁體中文](recommendation-v2-spike.zh-TW.md) · Design doc: [recommendation-v2.md](recommendation-v2.md)

This is step 0 of the [V2 plan](recommendation-v2.md#7-effort-and-plan). Run on
2026-10-18 against the live Spotify API with
[`spike/recommendation_v2_spike.py`](../spike/recommendation_v2_spike.py). The raw
output is in [`spike/results.json`](../spike/results.json).

## TL;DR

- **The app we tested is not under the Feb 2026 Development Mode restrictions.** Every
  endpoint and field the design doc listed as removed still works: search `limit=50`,
  batch `/artists` and `/tracks`, `top-tracks`, `popularity`, and reading items of a
  public playlist we don't own. Only the November 2024 closures apply. So **V1 isn't
  broken, and step 1 (the V1 compatibility fix) isn't needed** for this Client ID.
- **E2E: all 8 MCP tools pass** over a real stdio MCP client.
  `recommend_from_playlist` returned 5 picks for the 77-track sample playlist in
  4.5 s.
- **Option C is viable.** For 20 of 20 seeds at least one strong signal (C1–C3) fired,
  so no seed fell back to "more of the same artist". 118 calls for 20 seeds.
- **Option D is feasible on this app too, but its raw output is noisy.** 27% of the
  playlists found by search contain the seed track. Top candidates are polluted by
  covers of the seed song and by single-artist playlists. That's fixable (see below).
- **Caveat: these results apply to this Client ID only.** We ran the spike with the
  credentials from the SpotifyMCP2 project's Claude Desktop config, and its dashboard
  mode is unknown. A new app created today in Development Mode should still get the
  restrictions, according to Spotify's migration guide. Check the mode under *Developer
  Dashboard → app → Settings* before relying on any of this.

## 1. Endpoint probes (app token, Client Credentials)

| Probe | Result | Design doc expected |
| --- | --- | --- |
| `search` `limit=10` | 200 | 200 |
| `search` `limit=50` | **200** | 400 (Feb 2026 cap) |
| `GET /tracks/{id}`, `/artists/{id}`, `/artists/{id}/albums` | 200 | 200 |
| batch `GET /artists?ids`, `/tracks?ids` | **200** | removed |
| `GET /artists/{id}/top-tracks` | **200** | removed |
| `GET /browse/new-releases` | **200** | removed |
| `GET /recommendations` | 404 | 404 ✔ |
| `GET /artists/{id}/related-artists` | 404 | 404 ✔ |
| `GET /audio-features/{id}` | 403 | 403 ✔ |

Fields: `track.popularity`, `track.external_ids.isrc`, `track.available_markets`,
`artist.popularity` and `artist.followers` are all **present**. Radiohead's genres:
`art rock`, `alternative rock`.

## 2. Playlist access

| Case | Metadata | `/tracks` (old path) | `/items` (new path) |
| --- | --- | --- | --- |
| App token, someone else's public playlist | 200, 77 items embedded | 200 | 200 |
| App token, Spotify editorial (Today's Top Hits) | 404 | 404 | 404 |
| User token, own playlist | 200 | 200 | 200 |
| User token, someone else's public playlist | 200, 77 items embedded | 200 | 200 |

Both paths work, so V1's `/tracks` call is fine today. Moving to `/items` is still a
cheap way to stay ahead of the rename. Playlist search for "radiohead" returned 10
results: 4 were `null` and none were Spotify-owned. Code must skip nulls, which V1
doesn't search for.

## 3. Option C coverage (20 seeds across languages and genres)

| Signal | Seeds where it fires |
| --- | --- |
| Seed resolved by search | 20/20, but 1 was wrong (see below) |
| C1 collaborators or `appears_on` releases | 20/20 |
| Artist has genres | 19/20 (Childish Gambino has none) |
| C2 genre + era track search returns results | 17/20 |
| C3 genre artist search returns artists | 9/20 |
| C4 own releases | 20/20 |
| Only C4 fires | **0/20** |
| Cost | 118 calls, 18.4 s sequential (about 3–4 s at 6 workers) |

Observations:

- **Seed resolution needs field filters and an exact-match check.** A plain search for
  `Blinding Lights The Weeknd` returned *Don't Start Now* by Dua Lipa. Use
  `track:"…" artist:"…"` and verify the name, as the design doc proposed.
- **C1's `appears_on` fires for everyone, but it is mostly compilations** ("Now That's
  What I Call…", soundtracks). Keep C1's weight on *co-credited* artists (only 3/20
  seeds have one) and down-weight `appears_on` artists from multi-artist compilations.
- **C3 is weak.** Artist search with `genre:` returned nothing for `art rock`, `grunge`,
  `french house` or `mandopop`, even though C2's track search with the same genres
  works. Lower its weight or drop it.
- C2 is uneven: *Creep* got 1 track, *Smells Like Teen Spirit* and *One More Time* got
  0, while pop, K-pop, J-pop and Mandopop got the full 10.

## 4. Option D: playlist co-occurrence (10 seeds)

| Metric | Value |
| --- | --- |
| Seeds with at least one usable playlist | 10/10 |
| Searched playlists that contain the seed **track** | 24/89 (27%) |
| Searched playlists that contain only the seed **artist** | 32/89 (36%) |
| Cost | 109 calls, 30.7 s sequential (about 5–6 s at 6 workers) |

Good top-5 lists (before any cleanup):

- *Take Five*: Coltrane *Blue Train*, Branford Marsalis, Jun Miyake.
- *Lose Yourself*: Coolio *Gangsta's Paradise*, Dr. Dre *Still D.R.E.*, JAY-Z.
- *倔強*: 費玉清, 王傑, 瘦子E.SO, 冰球樂團.

Failure modes to fix:

| Problem | Example | Fix |
| --- | --- | --- |
| Covers and remixes of the seed song | *Lemon*: 5 of the top 5 are covers. *One More Time*: 5 of 5 are remixes. | Drop candidates whose normalized title matches a seed's title |
| One playlist dominated by one artist | *Dynamite*: the top 5 are all GiGi Waterproof | Cap each artist's contribution per playlist, and require a candidate to appear in ≥2 playlists |
| Name-only matches | *Bohemian Rhapsody*: a Latin playlist mentioning Queen | Weight playlists containing the seed track above artist-only ones |

## 5. E2E: every MCP tool through stdio

| Tool | Result | Time |
| --- | --- | --- |
| `list_tools` | ✅ 8 tools | — |
| `search_tracks` (limit 3 / limit 20) | ✅ 3 / ✅ 20 | 0.8 s / 0.4 s |
| `search_artists` | ✅ 3 | 0.3 s |
| `get_track` | ✅ | 0.1 s |
| `get_artist` | ✅ | 0.1 s |
| `get_artist_top_tracks` | ✅ 10 | 0.1 s |
| `get_album_tracks` | ✅ 3 | 0.1 s |
| `get_playlist_tracks` | ✅ 5 | 0.1 s |
| `recommend_from_playlist` (limit 5) | ✅ 5 | 4.5 s |

The whole run made 246 app-token calls and 8 user-token calls, with zero 429s.

## 6. Corrections to the design doc

| Design doc said | Spike found (this Client ID) |
| --- | --- |
| §3.2: V1 is probably broken | Not broken. All 8 tools pass. |
| §3.2: no popularity | `popularity` is present, so it can be a tiebreaker again. |
| §4: option D only with Extended Quota | Works here. Whether this app *is* Extended Quota is unknown. |
| §6: option D costs about 300–450 calls | About 220 for 20 seeds with 10 playlists each, plus cleanup |
| §5.2: C1 is the strongest signal | True for co-credits, but `appears_on` is mostly compilation noise. C3 is weak. |

## 7. Recommendation

1. **Check the app's mode** in the Developer Dashboard. If it's Extended Quota, or
   otherwise unrestricted, that decides the rest.
2. **If it's unrestricted, build option D as V2**, with option C's C1/C2 as a fallback
   for seeds whose playlists are thin. Include the three fixes from §4. This is closest
   to the original request ("songs Spotify users consider similar"). It needs no
   outside services, and its candidates are already Spotify tracks.
3. **If it's a restricted Development Mode app**, build option C only (minus C3), as the
   design doc recommends.
4. Either way, skip step 1 (the V1 fix) for now, and run step 3's offline evaluation
   (V1 vs. V2, recall@N) before changing the default.

## Reproducing

```bash
SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... \
  [SPOTIFY_REFRESH_TOKEN=...] uv run python spike/recommendation_v2_spike.py
```

The script is read-only: GET calls only, plus one token refresh if a refresh token is
given. It takes about 1 minute and makes about 250 calls. It overwrites
`spike/results.json`.
