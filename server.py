"""A minimal Spotify MCP server (phase 1: read-only catalog search).

Uses Spotify's Client Credentials flow, so it needs only an app's client ID and
secret -- no user login. That means catalog data and public playlists only;
anything user-specific (playback, private playlists, library) needs the
Authorization Code flow instead.

Spotify closed its recommendations and related-artists endpoints to new apps in
November 2024, so recommend_from_playlist builds its own: similar artists come
from Deezer (no key needed) and, when LASTFM_API_KEY is set, Last.fm.
"""

import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
DEEZER_API = "https://api.deezer.com"
LASTFM_API = "https://ws.audioscrobbler.com/2.0/"

# How many of a playlist's most frequent artists seed the similar-artist lookups.
# Each seed costs one or two external calls, so this bounds a recommendation's cost.
MAX_SEEDS = 20

mcp = FastMCP("Spotify")

_token: str | None = None
_token_expires_at: float = 0.0

# Spotify rejects page sizes outside 1-50 with a bare 400, so bound them up front.
Limit = Annotated[int, Field(ge=1, le=50)]


def _raise_for_status(resp: httpx.Response) -> None:
    """Raise a ToolError carrying Spotify's own explanation of a failed request.

    A ToolError reaches the client verbatim and is logged without a traceback,
    unlike the generic "400 Bad Request" an HTTPStatusError would produce.
    """
    if resp.is_success:
        return
    try:
        body = resp.json()
    except ValueError:
        body = {}
    # The token endpoint uses OAuth's flat shape; the Web API nests it.
    error = body.get("error")
    if isinstance(error, dict):
        detail = error.get("message")
    else:
        detail = body.get("error_description") or error
    raise ToolError(
        f"Spotify returned {resp.status_code}: {detail or resp.reason_phrase}"
    )


def _get_token() -> str:
    """Return a cached app access token, fetching a new one when it expires."""
    global _token, _token_expires_at

    if _token and time.time() < _token_expires_at:
        return _token

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ToolError(
            "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET "
            "(create an app at https://developer.spotify.com/dashboard)"
        )

    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10,
    )
    _raise_for_status(resp)
    payload = resp.json()

    _token = payload["access_token"]
    # Shave 60s off so we never hand out a token that expires mid-request.
    _token_expires_at = time.time() + payload["expires_in"] - 60
    return _token


def _api(path: str, **params) -> dict:
    """GET a Spotify API path and return the decoded JSON body."""
    resp = httpx.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {_get_token()}"},
        params=params,
        timeout=10,
    )
    _raise_for_status(resp)
    return resp.json()


def _track_summary(track: dict, album: str | None = None) -> dict:
    """Summarise a full or simplified track.

    Simplified tracks (an album's track list) carry neither an album nor a
    popularity, so the caller passes the album name and popularity is left out
    rather than reported as null.
    """
    summary = {
        "id": track["id"],
        "name": track["name"],
        "artists": [a["name"] for a in track["artists"]],
        "album": album or track.get("album", {}).get("name"),
        "duration_ms": track["duration_ms"],
        "url": track["external_urls"]["spotify"],
    }
    if "popularity" in track:
        summary["popularity"] = track["popularity"]
    return summary


def _normalize(name: str) -> str:
    """Fold an artist name for comparison across services ("ＫＩＩＩ" == "kiii")."""
    return unicodedata.normalize("NFKC", name).casefold().strip()


def _playlist_id(playlist: str) -> str:
    """Accept a playlist URL, a spotify:playlist: URI, or a bare ID."""
    match = re.search(r"playlist[/:]([A-Za-z0-9]+)", playlist)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{22}", playlist):
        return playlist
    raise ToolError(f"Not a Spotify playlist URL, URI or ID: {playlist!r}")


def _playlist_tracks(playlist_id: str, limit: int) -> list[dict]:
    """Fetch up to `limit` tracks, skipping podcast episodes and local files."""
    tracks = []
    while len(tracks) < limit:
        page = _api(
            f"/playlists/{playlist_id}/tracks",
            limit=min(100, limit - len(tracks)),
            offset=len(tracks),
        )
        tracks += [
            item["track"]
            for item in page["items"]
            if item.get("track")
            and item["track"].get("type") == "track"
            and item["track"].get("id")
        ]
        if not page.get("next"):
            break
    return tracks[:limit]


def _deezer_similar(artist: str) -> list[str]:
    """Names of artists Deezer considers related, best match first."""
    found = httpx.get(
        f"{DEEZER_API}/search/artist", params={"q": artist, "limit": 5}, timeout=10
    ).json()
    # Only trust an exact name match; Deezer's fuzzy top hit is often someone else.
    wanted = _normalize(artist)
    match = next(
        (a for a in found.get("data", []) if _normalize(a["name"]) == wanted), None
    )
    if match is None:
        return []
    related = httpx.get(
        f"{DEEZER_API}/artist/{match['id']}/related", params={"limit": 20}, timeout=10
    ).json()
    return [a["name"] for a in related.get("data", [])]


def _lastfm_similar(artist: str) -> list[str]:
    """Names of artists Last.fm listeners pair with this one, if a key is set."""
    api_key = os.environ.get("LASTFM_API_KEY")
    if not api_key:
        return []
    data = httpx.get(
        LASTFM_API,
        params={
            "method": "artist.getsimilar",
            "artist": artist,
            "api_key": api_key,
            "format": "json",
            "limit": 20,
        },
        timeout=10,
    ).json()
    return [a["name"] for a in data.get("similarartists", {}).get("artist", [])]


def _similar_artists(artist: str) -> list[list[str]]:
    """Similar-artist lists from every source, best effort.

    A source that is down or errors is skipped rather than failing the whole
    recommendation; the others still contribute.
    """
    results = []
    for source in (_deezer_similar, _lastfm_similar):
        try:
            results.append(source(artist))
        except (httpx.HTTPError, ValueError, KeyError):
            continue
    return results


def _artist_summary(artist: dict) -> dict:
    return {
        "id": artist["id"],
        "name": artist["name"],
        "genres": artist.get("genres", []),
        "followers": artist.get("followers", {}).get("total"),
        "popularity": artist.get("popularity"),
        "url": artist["external_urls"]["spotify"],
    }


@mcp.tool
def search_tracks(query: str, limit: Limit = 10) -> list[dict]:
    """Search Spotify for tracks matching a free-text query."""
    data = _api("/search", q=query, type="track", limit=limit)
    return [_track_summary(t) for t in data["tracks"]["items"]]


@mcp.tool
def search_artists(query: str, limit: Limit = 10) -> list[dict]:
    """Search Spotify for artists matching a free-text query."""
    data = _api("/search", q=query, type="artist", limit=limit)
    return [_artist_summary(a) for a in data["artists"]["items"]]


@mcp.tool
def get_track(track_id: str) -> dict:
    """Get the details of one track by its Spotify track ID."""
    return _track_summary(_api(f"/tracks/{track_id}"))


@mcp.tool
def get_artist(artist_id: str) -> dict:
    """Get the details of one artist by their Spotify artist ID."""
    return _artist_summary(_api(f"/artists/{artist_id}"))


@mcp.tool
def get_artist_top_tracks(artist_id: str, market: str = "US") -> list[dict]:
    """Get an artist's most popular tracks in a market (ISO 3166-1 alpha-2)."""
    data = _api(f"/artists/{artist_id}/top-tracks", market=market)
    return [_track_summary(t) for t in data["tracks"]]


@mcp.tool
def get_album_tracks(album_id: str, limit: Limit = 50) -> list[dict]:
    """List the tracks on an album by its Spotify album ID."""
    # The album endpoint embeds the first 50 tracks alongside the album's name,
    # which the bare /tracks endpoint omits.
    album = _api(f"/albums/{album_id}")
    tracks = album["tracks"]["items"][:limit]
    return [_track_summary(t, album=album["name"]) for t in tracks]


@mcp.tool
def get_playlist_tracks(
    playlist: str, limit: Annotated[int, Field(ge=1, le=500)] = 100
) -> list[dict]:
    """List the tracks on a public playlist, given its URL, URI or ID."""
    return [_track_summary(t) for t in _playlist_tracks(_playlist_id(playlist), limit)]


@mcp.tool
def recommend_from_playlist(
    playlist: str,
    limit: Limit = 20,
    market: str = "US",
    tracks_per_artist: Annotated[int, Field(ge=1, le=3)] = 1,
) -> list[dict]:
    """Recommend tracks by artists similar to those on a public playlist.

    Each playlist artist (weighted by how often they appear) votes for its
    similar artists; artists already on the playlist are excluded, and ones that
    share genres with the playlist rank higher. Returns each artist's top tracks
    in `market`, with `because` naming the playlist artists that led to it.
    """
    tracks = _playlist_tracks(_playlist_id(playlist), limit=500)
    if not tracks:
        raise ToolError("That playlist has no tracks to recommend from.")

    seeds = Counter(a["name"] for t in tracks for a in t["artists"])
    on_playlist = {_normalize(name) for name in seeds}
    artist_ids = {a["name"]: a["id"] for t in tracks for a in t["artists"]}
    # /artists takes at most 50 IDs, so sample genres from the most frequent.
    seed_ids = [artist_ids[name] for name, _ in seeds.most_common(50)]
    playlist_genres = {
        genre
        for a in _api("/artists", ids=",".join(seed_ids))["artists"]
        if a
        for genre in a.get("genres", [])
    }

    top_seeds = seeds.most_common(MAX_SEEDS)
    with ThreadPoolExecutor(max_workers=6) as pool:
        lookups = pool.map(_similar_artists, [seed for seed, _ in top_seeds])

    # votes[candidate][seed]: how strongly each playlist artist points at it.
    votes: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, str] = {}
    for (seed, weight), similar_lists in zip(top_seeds, lookups):
        for similar in similar_lists:
            for rank, name in enumerate(similar):
                key = _normalize(name)
                if key in on_playlist:
                    continue
                # Earlier entries are closer matches, so they earn more.
                votes[key][seed] += weight * (1 - rank / len(similar))
                names.setdefault(key, name)
    if not votes:
        raise ToolError("Couldn't find similar artists for this playlist.")

    def resolve(key: str) -> dict | None:
        """The Spotify artist with exactly this name, if there is one."""
        hits = _api("/search", q=names[key], type="artist", limit=5)["artists"]["items"]
        return next((a for a in hits if _normalize(a["name"]) == key), None)

    # Resolve extra candidates: some names won't be on Spotify, and the
    # diversity cap below skips others.
    wanted = -(-limit // tracks_per_artist)
    ranked = sorted(votes, key=lambda k: sum(votes[k].values()), reverse=True)
    candidates = ranked[: wanted * 3]
    with ThreadPoolExecutor(max_workers=6) as pool:
        artists = list(pool.map(resolve, candidates))

    scored = []
    for key, artist in zip(candidates, artists):
        if artist is None:
            continue
        score = sum(votes[key].values())
        if playlist_genres.intersection(artist.get("genres", [])):
            score *= 1.5
        scored.append((score, key, artist))
    scored.sort(key=lambda r: r[0], reverse=True)

    # Without a cap, one dense cluster (say, K-pop, where every artist lists the
    # same peers) fills the whole list. Each playlist artist may lead to as many
    # recommendations as it has tracks on the playlist, plus one.
    chosen, led = [], Counter()
    for score, key, artist in scored:
        lead = votes[key].most_common(1)[0][0]
        if led[lead] > seeds[lead]:
            continue
        led[lead] += 1
        chosen.append((score, key, artist))
        if len(chosen) == wanted:
            break

    with ThreadPoolExecutor(max_workers=6) as pool:
        top_tracks = pool.map(
            lambda a: _api(f"/artists/{a['id']}/top-tracks", market=market)["tracks"],
            [artist for _, _, artist in chosen],
        )
    on_playlist_ids = {t["id"] for t in tracks}
    picks = []
    for (score, key, _), top in zip(chosen, top_tracks):
        fresh = [t for t in top if t["id"] not in on_playlist_ids]
        for track in fresh[:tracks_per_artist]:
            picks.append(
                {
                    **_track_summary(track),
                    "because": [seed for seed, _ in votes[key].most_common(3)],
                    "score": round(score, 2),
                }
            )
    return picks[:limit]


if __name__ == "__main__":
    mcp.run()
