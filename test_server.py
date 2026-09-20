"""Tests for the Spotify MCP server.

Every Spotify HTTP call is mocked with respx, so these run offline and need no
real credentials.
"""

import time

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

import server

TOKEN_ROUTE = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    """Give every test fake credentials and a cleared token cache."""
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test-secret")
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
        with pytest.raises(RuntimeError, match="SPOTIFY_CLIENT_ID"):
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
    def test_get_album_tracks_handles_simplified_tracks(self, respx_mock):
        """Album tracks omit `album` and `popularity`; summarising must not crash."""
        mock_token(respx_mock)
        respx_mock.get(f"{API}/albums/al1/tracks").mock(
            return_value=httpx.Response(200, json={"items": [SIMPLIFIED_TRACK]})
        )
        [track] = server.get_album_tracks("al1")
        assert track["name"] == "Airbag"
        assert track["album"] is None
        assert track["popularity"] is None

    @respx.mock
    def test_http_errors_propagate(self, respx_mock):
        mock_token(respx_mock)
        respx_mock.get(f"{API}/tracks/nope").mock(
            return_value=httpx.Response(404, json={"error": {"message": "not found"}})
        )
        with pytest.raises(httpx.HTTPStatusError):
            server.get_track("nope")


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
    async def test_missing_credentials_reach_the_client(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_CLIENT_ID")
        async with Client(server.mcp) as client:
            with pytest.raises(ToolError, match="SPOTIFY_CLIENT_ID"):
                await client.call_tool("search_tracks", {"query": "x"})
