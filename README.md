# Spotify MCP

A minimal [FastMCP](https://gofastmcp.com) server exposing Spotify's catalog as MCP tools.

[![CI](https://github.com/yennanliu/SpotifyMCP-V3/actions/workflows/ci.yml/badge.svg)](https://github.com/yennanliu/SpotifyMCP-V3/actions/workflows/ci.yml)

**Phase 1** uses Spotify's Client Credentials flow: app-level auth, no user login, no
redirect URI, no token storage on disk. That covers public catalog data and public
playlists. User-specific features (playback control, private playlists, saved library)
need the Authorization Code flow and are out of scope here.

## Tools

| Tool | Description |
| --- | --- |
| `search_tracks(query, limit=10)` | Search for tracks |
| `search_artists(query, limit=10)` | Search for artists |
| `get_track(track_id)` | One track's details |
| `get_artist(artist_id)` | One artist's details |
| `get_artist_top_tracks(artist_id, market="US")` | An artist's top tracks |
| `get_album_tracks(album_id, limit=50)` | An album's track list |
| `get_playlist_tracks(playlist, limit=100)` | A public playlist's tracks (URL, URI or ID; up to 500) |
| `recommend_from_playlist(playlist, limit=20, market="US", tracks_per_artist=1)` | Tracks by artists similar to a playlist's |

Responses are flattened to small summary dicts rather than Spotify's raw payloads, so
image arrays and pagination cursors stay out of the model's context. `limit` must be
1–50, Spotify's own page-size cap; out-of-range values are rejected before any request.

## How recommendations work

Spotify closed `/recommendations`, `/artists/{id}/related-artists` and
`/audio-features` to new apps in November 2024, and they return 404 or 403 for this
app. So `recommend_from_playlist` builds recommendations from other sources:

1. **Read the playlist.** Its artists are the seeds, weighted by how many tracks each has
   on it. The 20 most frequent are used.
2. **Find similar artists** for each seed from [Deezer](https://developers.deezer.com/api)
   (free, no key needed) and, if `LASTFM_API_KEY` is set,
   [Last.fm](https://www.last.fm/api/show/artist.getSimilar). A source that is down is
   skipped; the other still contributes.
3. **Score candidates.** Each seed votes for its similar artists, with closer matches
   getting more weight. Artists already on the playlist are excluded, and ones sharing a
   Spotify genre with the playlist get a 1.5× boost.
4. **Keep it diverse.** Seeds take turns: each one's best candidate comes first, then
   each one's second best, and so on. A seed can lead to at most (its tracks on the
   playlist + 1) picks. Without this, one dense cluster (for example K-pop, where every
   artist lists the same peers) takes every top slot, and a short list covers only one
   style.
5. **Map back to Spotify.** Each candidate is matched by exact name; ones that only
   fuzzy-match are dropped. Their top tracks in `market` are returned, with `because`
   listing the seeds that led there. A track is skipped if it is already on the playlist,
   if any artist credited on it is on the playlist (top tracks are often features led by
   someone else), or if any credited artist already has `tracks_per_artist` picks. When
   that leaves the list short, the next-ranked candidates fill it.

A 77-track playlist takes about 6 seconds, making roughly 100 HTTP calls run six at a
time. If Spotify rate-limits a call (HTTP 429), the server waits for the `Retry-After`
time and retries, up to 3 times, unless the wait is over 30 seconds. Deezer's API is for
non-commercial use; check its terms before shipping this commercially.

To add Last.fm as a second source, get a free key at https://www.last.fm/api/account/create
and pass it alongside the Spotify credentials (`-e LASTFM_API_KEY=...`).

## Setup

1. Create an app at the [Spotify developer dashboard](https://developer.spotify.com/dashboard)
   and copy its Client ID and Client Secret.
2. Install dependencies:

   ```bash
   uv sync
   ```

3. Export the credentials:

   ```bash
   export SPOTIFY_CLIENT_ID=...
   export SPOTIFY_CLIENT_SECRET=...
   ```

## Run

```bash
uv run server.py
```

That serves over stdio, which is what MCP hosts launch. For HTTP instead:

```bash
uv run fastmcp run server.py:mcp --transport http --port 8000
```

## Testing it locally

### 1. Unit tests — no credentials needed

Every Spotify HTTP call is mocked with `respx`, so the suite runs offline:

```bash
uv run pytest -v
```

### 2. Inspect the server without an MCP host

Connects to `server.py` as a subprocess and lists what it exposes:

```bash
uv run python -c "
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    async with Client(StdioTransport('uv', ['run', 'server.py'])) as c:
        for tool in await c.list_tools():
            print(tool.name, list(tool.input_schema['properties']))

asyncio.run(main())
"
```

### 3. Make a real Spotify call

With the credentials exported, call a tool directly:

```bash
uv run python -c "
import json, server
print(json.dumps(server.search_tracks('radiohead', limit=3), indent=2))
"
```

If the credentials are missing you get a clear error naming the variables to set,
rather than a 401 from Spotify. Errors from Spotify itself (bad credentials, an
invalid ID) come back with Spotify's own explanation, e.g.
`Spotify returned 400: invalid id`.

### 4. The FastMCP inspector

```bash
uv run fastmcp dev inspector server.py:mcp
```

Opens a browser UI where you can call each tool by hand and see the raw JSON.

## Use from Claude Code

```bash
claude mcp add spotify \
  -e SPOTIFY_CLIENT_ID=... \
  -e SPOTIFY_CLIENT_SECRET=... \
  -- uv --directory /Users/jliu/SpotifyMCP-V3 run server.py
```

Then in a session, ask something like *"search Spotify for Radiohead's top tracks"*.
Check it registered with `claude mcp list`.

Or the equivalent entry in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "spotify": {
      "command": "uv",
      "args": ["--directory", "/Users/jliu/SpotifyMCP-V3", "run", "server.py"],
      "env": {
        "SPOTIFY_CLIENT_ID": "...",
        "SPOTIFY_CLIENT_SECRET": "..."
      }
    }
  }
}
```

## Use from ChatGPT

ChatGPT can't launch a local stdio server; it connects only to a public HTTPS URL. So
serve over HTTP and put a temporary tunnel in front of it. This needs a paid ChatGPT plan
with Developer mode, on the web.

1. Serve over HTTP:

   ```bash
   SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... \
     uv run fastmcp run server.py:mcp --transport http --port 8000
   ```

2. In a second terminal, open a tunnel ([cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/),
   `brew install cloudflared`; no account needed):

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

   It prints a URL like `https://<random-words>.trycloudflare.com`. Check that it
   reaches the server before going further. An HTTP `200` means it does:

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<random-words>.trycloudflare.com/mcp \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"0"}}}'
   ```

3. In ChatGPT, open **Settings → Apps & Connectors → Advanced settings** and turn on
   **Developer mode**.
4. In **Apps & Connectors**, click **Create** and fill in:
   - **Connection:** *Server URL*, set to the tunnel URL **with `/mcp` appended**
   - **Authentication:** *No authentication*
   - Tick the box acknowledging that custom MCP servers carry risk, then click **Create**.
     ChatGPT should list the eight tools.
5. In a new chat, click **+**, choose **Developer mode**, and turn the connector on.
   ChatGPT asks you to approve each tool call.

Things to know:

- **Anyone with the URL can call the server**, since it has no auth. They get read-only
  catalog search through your app. They can't reach your Spotify account or see the
  client secret. Don't share the URL, and stop the tunnel when you're done.
- **A quick tunnel gets a new URL on every restart**, so you have to update the
  connector in ChatGPT each time.

## Example prompts

These work in any MCP host (Claude Code, Claude Desktop or ChatGPT). Together they cover
all eight tools and the error paths.

| Prompt | Exercises |
| --- | --- |
| *Search Spotify for 3 tracks called "Yellow".* | `search_tracks` |
| *Find the artist Radiohead and tell me their follower count and genres.* | `search_artists` |
| *What are Radiohead's top 5 tracks in the US? And in Taiwan?* | `search_artists` → `get_artist_top_tracks` with `market` |
| *Look up track `70LcF31zb1H0PyJoS1Sx1r`: name, album and length in minutes.* | `get_track` |
| *Get artist `4Z8W4fKeB5YxbusRsdQVPb` and summarise them.* | `get_artist` |
| *List the full tracklist of OK Computer (album `6dVIqQ8qmQ5GBnJ9shOYGE`).* | `get_album_tracks` |
| *找周杰倫最熱門的 3 首歌，並列出每首歌所屬的專輯。* | Non-ASCII queries, chained calls |
| *Find Daft Punk's most popular track, then show me the rest of the album it's on.* | Multi-step: search → top tracks → album |
| *Look up track `notarealid`.* | Error path: should report `Spotify returned 400: Invalid base62 id` |
| *What's on this playlist? https://open.spotify.com/playlist/1sKQOPBrwSLv0Ul80U8foE* | `get_playlist_tracks` |
| *Recommend 15 songs like this playlist, for Taiwan: https://open.spotify.com/playlist/1sKQOPBrwSLv0Ul80U8foE* | `recommend_from_playlist` with `market` |
| *Search for 100 tracks by Taylor Swift.* | `limit` above 50: ChatGPT should either cap it at 50 or relay the validation error |

Playback, "save this as a playlist" and "my library" prompts are expected to fail,
because Phase 1 only has access to the public catalog and public playlists.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the test suite on Python
3.12 and 3.13 for every push to `main` and every pull request, then boots the server
and asserts all eight tools register. No secrets are needed — the tests are fully mocked.

## Layout

```
server.py       the MCP server (eight tools + token caching)
test_server.py  43 tests, all offline
pyproject.toml  deps and pytest config, managed by uv
```

## Phase 2 ideas

- Authorization Code flow for user-scoped access
- Playback control (play, pause, skip, transfer device)
- Write recommendations back as a new playlist (needs user login)
- Cache similar-artist lookups across calls
