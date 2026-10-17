"""Tests for the Spotify MCP server.

Every Spotify HTTP call is mocked with respx, so these run offline and need no
real credentials.
"""

import time
from urllib.parse import unquote

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

import server

TOKEN_ROUTE = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
DEEZER = "https://api.deezer.com"
LASTFM = "https://ws.audioscrobbler.com/2.0/"
PLAYLIST = "1sKQOPBrwSLv0Ul80U8foE"


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    """Give every test fake credentials and a cleared token cache."""
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test-secret")
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    monkeypatch.setattr(server, "_token", None)
    monkeypatch.setattr(server, "_token_expires_at", 0.0)


def mock_token(respx_mock, token="token-1", expires_in=3600):
    return respx_mock.post(TOKEN_ROUTE).mock(
        return_value=httpx.Response(
            200, json={"access_token": token, "expires_in": expires_in}
        )
    )


# Sample payloads, trimmed to the fields the server reads.

TRACK = {
    "id": "t1",
    "name": "Paranoid Android",
    "artists": [{"name": "Radiohead"}],
    "album": {"name": "OK Computer"},
    "duration_ms": 383_000,
    "popularity": 78,
    "external_urls": {"spotify": "https://open.spotify.com/track/t1"},
}

ARTIST = {
    "id": "a1",
    "name": "Radiohead",
    "genres": ["art rock"],
    "followers": {"total": 9_000_000},
    "popularity": 80,
    "external_urls": {"spotify": "https://open.spotify.com/artist/a1"},
}

# An album's track list returns *simplified* tracks: no album, no popularity.
SIMPLIFIED_TRACK = {
    "id": "t2",
    "name": "Airbag",
    "artists": [{"name": "Radiohead"}],
    "duration_ms": 284_000,
    "external_urls": {"spotify": "https://open.spotify.com/track/t2"},
}


def track(track_id, artist, artist_id=None):
    """A full track by one artist."""
    return {
        **TRACK,
        "type": "track",
        "id": track_id,
        "name": f"{artist} song {track_id}",
        "artists": [{"name": artist, "id": artist_id or f"id-{artist}"}],
    }


def mock_playlist(respx_mock, tracks, playlist_id=PLAYLIST):
    """Serve a one-page playlist, plus the batch artist lookup for its genres."""
    respx_mock.get(f"{API}/playlists/{playlist_id}/tracks").mock(
        return_value=httpx.Response(
            200, json={"items": [{"track": t} for t in tracks], "next": None}
        )
    )
    respx_mock.get(f"{API}/artists").mock(
        return_value=httpx.Response(200, json={"artists": [{"genres": ["k-pop"]}]})
    )


def mock_deezer(respx_mock, related):
    """Fake Deezer, where `related` maps an artist name to its related artists."""
    ids = {name: str(i) for i, name in enumerate(related, start=1)}

    def search(request):
        q = request.url.params["q"]
        data = [{"id": int(ids[q]), "name": q}] if q in ids else []
        return httpx.Response(200, json={"data": data})

    def related_artists(request, artist_id):
        name = next(n for n, i in ids.items() if i == artist_id)
        return httpx.Response(200, json={"data": [{"name": n} for n in related[name]]})

    respx_mock.get(f"{DEEZER}/search/artist").mock(side_effect=search)
    respx_mock.get(url__regex=rf"{DEEZER}/artist/(?P<artist_id>\d+)/related").mock(
        side_effect=related_artists
    )


def mock_spotify_artists(respx_mock, genres=None, renamed=None):
    """Spotify artist search and top tracks for any name.

    `genres` maps a name to its genres; `renamed` maps a name to the different
    artist Spotify's search returns instead, to simulate a name collision.
    """
    genres, renamed = genres or {}, renamed or {}

    def search(request):
        q = request.url.params["q"]
        name = renamed.get(q, q)
        artist = {"id": f"id-{name}", "name": name, "genres": genres.get(q, [])}
        return httpx.Response(200, json={"artists": {"items": [artist]}})

    def top_tracks(request, artist_id):
        name = unquote(artist_id).removeprefix("id-")
        return httpx.Response(
            200, json={"tracks": [track(f"{name}-1", name), track(f"{name}-2", name)]}
        )

    respx_mock.get(f"{API}/search").mock(side_effect=search)
    respx_mock.get(url__regex=rf"{API}/artists/(?P<artist_id>[^/]+)/top-tracks").mock(
        side_effect=top_tracks
    )


class TestAuth:
    @respx.mock
    def test_fetches_and_caches_token(self, respx_mock):
        route = mock_token(respx_mock)
        assert server._get_token() == "token-1"
        assert server._get_token() == "token-1"
        assert route.call_count == 1, "second call should reuse the cached token"

    @respx.mock
    def test_refetches_when_expired(self, respx_mock):
        mock_token(respx_mock)
        server._get_token()
        server._token_expires_at = time.time() - 1
        mock_token(respx_mock, token="token-2")
        assert server._get_token() == "token-2"

    @respx.mock
    def test_expiry_has_a_safety_margin(self, respx_mock):
        mock_token(respx_mock, expires_in=3600)
        server._get_token()
        # 60s is shaved off so a token never expires mid-request.
        assert server._token_expires_at == pytest.approx(time.time() + 3540, abs=5)

    @respx.mock
    def test_sends_basic_auth(self, respx_mock):
        route = mock_token(respx_mock)
        server._get_token()
        assert route.calls[0].request.headers["authorization"].startswith("Basic ")

    def test_missing_credentials_is_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_CLIENT_ID")
        with pytest.raises(ToolError, match="SPOTIFY_CLIENT_ID"):
            server._get_token()

    @respx.mock
    def test_bad_credentials_surface_spotifys_reason(self, respx_mock):
        respx_mock.post(TOKEN_ROUTE).mock(
            return_value=httpx.Response(
                400,
                json={"error": "invalid_client", "error_description": "Invalid client"},
            )
        )
        with pytest.raises(ToolError, match="400: Invalid client"):
            server._get_token()


class TestTools:
    @respx.mock
    def test_search_tracks(self, respx_mock):
        mock_token(respx_mock)
        route = respx_mock.get(f"{API}/search").mock(
            return_value=httpx.Response(200, json={"tracks": {"items": [TRACK]}})
        )

        assert server.search_tracks("radiohead", limit=5) == [
            {
                "id": "t1",
                "name": "Paranoid Android",
                "artists": ["Radiohead"],
                "album": "OK Computer",
                "duration_ms": 383_000,
                "popularity": 78,
                "url": "https://open.spotify.com/track/t1",
            }
        ]

        request = route.calls[0].request
        assert dict(request.url.params) == {
            "q": "radiohead",
            "type": "track",
            "limit": "5",
        }
        assert request.headers["authorization"] == "Bearer token-1"

    @respx.mock
    def test_search_artists(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/search").mock(
            return_value=httpx.Response(200, json={"artists": {"items": [ARTIST]}})
        )
        [artist] = server.search_artists("radiohead")
        assert artist["name"] == "Radiohead"
        assert artist["followers"] == 9_000_000
        assert artist["genres"] == ["art rock"]

    @respx.mock
    def test_get_track(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/tracks/t1").mock(
            return_value=httpx.Response(200, json=TRACK)
        )
        assert server.get_track("t1")["name"] == "Paranoid Android"

    @respx.mock
    def test_get_artist(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/artists/a1").mock(
            return_value=httpx.Response(200, json=ARTIST)
        )
        assert server.get_artist("a1")["id"] == "a1"

    @respx.mock
    def test_get_artist_top_tracks_passes_market(self, respx_mock):
        mock_token(respx_mock)
        route = respx_mock.get(f"{API}/artists/a1/top-tracks").mock(
            return_value=httpx.Response(200, json={"tracks": [TRACK]})
        )
        assert len(server.get_artist_top_tracks("a1", market="GB")) == 1
        assert route.calls[0].request.url.params["market"] == "GB"

    @respx.mock
    def test_get_album_tracks_fills_in_the_album(self, respx_mock):
        """Album tracks omit `album` and `popularity`; neither should come back null."""
        mock_token(respx_mock)
        respx_mock.get(f"{API}/albums/al1").mock(
            return_value=httpx.Response(
                200,
                json={"name": "OK Computer", "tracks": {"items": [SIMPLIFIED_TRACK]}},
            )
        )
        [track] = server.get_album_tracks("al1")
        assert track["name"] == "Airbag"
        assert track["album"] == "OK Computer"
        assert "popularity" not in track

    @respx.mock
    def test_get_album_tracks_honours_limit(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/albums/al1").mock(
            return_value=httpx.Response(
                200,
                json={"name": "OK Computer", "tracks": {"items": [SIMPLIFIED_TRACK] * 3}},
            )
        )
        assert len(server.get_album_tracks("al1", limit=2)) == 2

    @respx.mock
    def test_api_errors_surface_spotifys_reason(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/tracks/nope").mock(
            return_value=httpx.Response(400, json={"error": {"message": "invalid id"}})
        )
        with pytest.raises(ToolError, match="400: invalid id"):
            server.get_track("nope")

    @respx.mock
    def test_api_errors_without_a_json_body(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/tracks/t1").mock(
            return_value=httpx.Response(502, text="<html>Bad Gateway</html>")
        )
        with pytest.raises(ToolError, match="502: Bad Gateway"):
            server.get_track("t1")


class TestPlaylists:
    @pytest.mark.parametrize(
        "playlist",
        [
            "https://open.spotify.com/playlist/1sKQOPBrwSLv0Ul80U8foE?si=c642c2fa",
            "spotify:playlist:1sKQOPBrwSLv0Ul80U8foE",
            "1sKQOPBrwSLv0Ul80U8foE",
        ],
    )
    def test_accepts_url_uri_or_id(self, playlist):
        assert server._playlist_id(playlist) == "1sKQOPBrwSLv0Ul80U8foE"

    def test_rejects_something_else(self):
        with pytest.raises(ToolError, match="Not a Spotify playlist"):
            server._playlist_id("https://open.spotify.com/album/6dVIqQ8qmQ5GBnJ9shOYGE")

    @respx.mock
    def test_pages_through_and_skips_non_tracks(self, respx_mock):
        mock_token(respx_mock)
        episode = {**track("e1", "Podcast"), "type": "episode"}
        local = {**track(None, "Local"), "type": "track"}
        route = respx_mock.get(f"{API}/playlists/{PLAYLIST}/tracks").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "items": [{"track": track("t1", "A")}, {"track": None}],
                        "next": "page-2",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "items": [
                            {"track": episode},
                            {"track": local},
                            {"track": track("t2", "B")},
                        ],
                        "next": None,
                    },
                ),
            ]
        )
        tracks = server.get_playlist_tracks(PLAYLIST)
        assert [t["id"] for t in tracks] == ["t1", "t2"]
        assert route.calls[1].request.url.params["offset"] == "1"


class TestRecommendations:
    @respx.mock
    def test_ranks_shared_neighbours_first_and_skips_playlist_artists(
        self, respx_mock
    ):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "IVE"), track("t2", "aespa")])
        mock_deezer(
            respx_mock,
            {
                # LE SSERAFIM is related to both seeds; aespa is already on the
                # playlist, so it must not come back.
                "IVE": ["aespa", "LE SSERAFIM", "ITZY"],
                "aespa": ["LE SSERAFIM", "NMIXX"],
            },
        )
        mock_spotify_artists(respx_mock)

        picks = server.recommend_from_playlist(PLAYLIST, limit=3)

        # NMIXX is 2nd of aespa's 2 (0.5); ITZY is 3rd of IVE's 3 (0.33).
        assert [p["artists"] for p in picks] == [["LE SSERAFIM"], ["NMIXX"], ["ITZY"]]
        assert picks[0]["because"] == ["aespa", "IVE"]
        assert picks[0]["id"] == "LE SSERAFIM-1"

    @respx.mock
    def test_shared_genres_rank_higher(self, respx_mock):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "IVE")])
        # Without the boost Coldplay (1.0) outranks ITZY (0.75); with it, ITZY
        # (0.75 * 1.5) wins.
        mock_deezer(respx_mock, {"IVE": ["Coldplay", "ITZY", "NMIXX", "STAYC"]})
        mock_spotify_artists(respx_mock, genres={"ITZY": ["k-pop"]})

        picks = server.recommend_from_playlist(PLAYLIST, limit=2)

        assert [p["artists"] for p in picks] == [["ITZY"], ["Coldplay"]]

    @respx.mock
    def test_skips_name_collisions_on_spotify(self, respx_mock):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "IVE")])
        mock_deezer(respx_mock, {"IVE": ["ITZY", "NMIXX"]})
        mock_spotify_artists(respx_mock, renamed={"ITZY": "ITZY Tribute Band"})

        picks = server.recommend_from_playlist(PLAYLIST, limit=2)

        assert [p["artists"] for p in picks] == [["NMIXX"]]

    @respx.mock
    def test_one_cluster_cannot_fill_the_list(self, respx_mock):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "IVE"), track("t2", "Night Tempo")])
        mock_deezer(
            respx_mock,
            {
                "IVE": ["ITZY", "NMIXX", "aespa", "STAYC"],
                "Night Tempo": ["Mariya Takeuchi"],
            },
        )
        mock_spotify_artists(respx_mock)

        picks = server.recommend_from_playlist(PLAYLIST, limit=3)

        # IVE has one track on the playlist, so it may lead to two picks.
        assert [p["artists"] for p in picks] == [
            ["ITZY"],
            ["Mariya Takeuchi"],
            ["NMIXX"],
        ]

    @respx.mock
    def test_skips_top_tracks_already_on_the_playlist(self, respx_mock):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "IVE"), track("ITZY-1", "Someone")])
        mock_deezer(respx_mock, {"IVE": ["ITZY"], "Someone": []})
        mock_spotify_artists(respx_mock)

        [pick] = server.recommend_from_playlist(PLAYLIST, limit=1)

        assert pick["id"] == "ITZY-2"

    @respx.mock
    def test_falls_back_to_lastfm_when_deezer_is_down(self, respx_mock, monkeypatch):
        monkeypatch.setenv("LASTFM_API_KEY", "fm-key")
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "IVE")])
        respx_mock.get(f"{DEEZER}/search/artist").mock(
            side_effect=httpx.ConnectError("down")
        )
        lastfm = respx_mock.get(LASTFM).mock(
            return_value=httpx.Response(
                200, json={"similarartists": {"artist": [{"name": "ITZY"}]}}
            )
        )
        mock_spotify_artists(respx_mock)

        [pick] = server.recommend_from_playlist(PLAYLIST, limit=1)

        assert pick["artists"] == ["ITZY"]
        assert lastfm.calls[0].request.url.params["api_key"] == "fm-key"

    @respx.mock
    def test_lastfm_is_skipped_without_a_key(self, respx_mock):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "IVE")])
        mock_deezer(respx_mock, {"IVE": ["ITZY"]})
        mock_spotify_artists(respx_mock)
        lastfm = respx_mock.get(LASTFM)

        server.recommend_from_playlist(PLAYLIST, limit=1)

        assert not lastfm.called

    @respx.mock
    def test_no_similar_artists_is_a_clear_error(self, respx_mock):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [track("t1", "Unknown Artist")])
        mock_deezer(respx_mock, {})

        with pytest.raises(ToolError, match="similar artists"):
            server.recommend_from_playlist(PLAYLIST)

    @respx.mock
    def test_empty_playlist_is_a_clear_error(self, respx_mock):
        mock_token(respx_mock)
        mock_playlist(respx_mock, [])

        with pytest.raises(ToolError, match="no tracks"):
            server.recommend_from_playlist(PLAYLIST)


class TestMCPServer:
    """Exercise the server the way a real MCP client would."""

    @pytest.mark.asyncio
    async def test_exposes_the_expected_tools(self):
        async with Client(server.mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert names == {
            "search_tracks",
            "search_artists",
            "get_track",
            "get_artist",
            "get_artist_top_tracks",
            "get_album_tracks",
            "get_playlist_tracks",
            "recommend_from_playlist",
        }

    @pytest.mark.asyncio
    async def test_every_tool_is_documented(self):
        async with Client(server.mcp) as client:
            tools = await client.list_tools()
        assert all(tool.description for tool in tools)

    @pytest.mark.asyncio
    @respx.mock
    async def test_tool_call_round_trip(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/search").mock(
            return_value=httpx.Response(200, json={"tracks": {"items": [TRACK]}})
        )
        async with Client(server.mcp) as client:
            result = await client.call_tool("search_tracks", {"query": "radiohead"})
        assert result.data[0]["name"] == "Paranoid Android"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [0, 51])
    async def test_out_of_range_limit_is_rejected_before_calling_spotify(
        self, limit
    ):
        # No respx mock: any HTTP request would fail the test.
        async with Client(server.mcp) as client:
            with pytest.raises(ToolError, match="limit"):
                await client.call_tool(
                    "search_tracks", {"query": "x", "limit": limit}
                )

    @pytest.mark.asyncio
    @respx.mock
    async def test_spotify_errors_reach_the_client(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/tracks/nope").mock(
            return_value=httpx.Response(400, json={"error": {"message": "invalid id"}})
        )
        async with Client(server.mcp) as client:
            with pytest.raises(ToolError, match="invalid id"):
                await client.call_tool("get_track", {"track_id": "nope"})

    @pytest.mark.asyncio
    async def test_missing_credentials_reach_the_client(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_CLIENT_ID")
        async with Client(server.mcp) as client:
            with pytest.raises(ToolError, match="SPOTIFY_CLIENT_ID"):
                await client.call_tool("search_tracks", {"query": "x"})
