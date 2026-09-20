"""A minimal Spotify MCP server (phase 1: read-only catalog search).

Uses Spotify's Client Credentials flow, so it needs only an app's client ID and
secret -- no user login. That means catalog data only; anything user-specific
(playback, playlists, library) needs the Authorization Code flow instead.
"""

import os
import time

import httpx
from fastmcp import FastMCP

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

mcp = FastMCP("Spotify")

_token: str | None = None
_token_expires_at: float = 0.0


def _get_token() -> str:
    """Return a cached app access token, fetching a new one when it expires."""
    global _token, _token_expires_at

    if _token and time.time() < _token_expires_at:
        return _token

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET "
            "(create an app at https://developer.spotify.com/dashboard)"
        )

    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10,
    )
    resp.raise_for_status()
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
    resp.raise_for_status()
    return resp.json()


def _track_summary(track: dict) -> dict:
    return {
        "id": track["id"],
        "name": track["name"],
        "artists": [a["name"] for a in track["artists"]],
        "album": track.get("album", {}).get("name"),
        "duration_ms": track["duration_ms"],
        "popularity": track.get("popularity"),
        "url": track["external_urls"]["spotify"],
    }


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
def search_tracks(query: str, limit: int = 10) -> list[dict]:
    """Search Spotify for tracks matching a free-text query."""
    data = _api("/search", q=query, type="track", limit=limit)
    return [_track_summary(t) for t in data["tracks"]["items"]]


@mcp.tool
def search_artists(query: str, limit: int = 10) -> list[dict]:
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
def get_album_tracks(album_id: str, limit: int = 50) -> list[dict]:
    """List the tracks on an album by its Spotify album ID."""
    data = _api(f"/albums/{album_id}/tracks", limit=limit)
    return [_track_summary(t) for t in data["items"]]


if __name__ == "__main__":
    mcp.run()
