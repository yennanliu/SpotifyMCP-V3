# Spotify MCP

A minimal [FastMCP](https://gofastmcp.com) server exposing Spotify's catalog as MCP tools.

[![CI](https://github.com/yennanliu/SpotifyMCP-V3/actions/workflows/ci.yml/badge.svg)](https://github.com/yennanliu/SpotifyMCP-V3/actions/workflows/ci.yml)

**Phase 1** uses Spotify's Client Credentials flow: app-level auth, no user login, no
redirect URI, no token storage on disk. That covers public catalog data. User-specific
features (playback control, playlists, saved library) need the Authorization Code flow
and are out of scope here.

## Tools

| Tool | Description |
| --- | --- |
| `search_tracks(query, limit=10)` | Search for tracks |
| `search_artists(query, limit=10)` | Search for artists |
| `get_track(track_id)` | One track's details |
| `get_artist(artist_id)` | One artist's details |
| `get_artist_top_tracks(artist_id, market="US")` | An artist's top tracks |
| `get_album_tracks(album_id, limit=50)` | An album's track list |

Responses are flattened to small summary dicts rather than Spotify's raw payloads, so
image arrays and pagination cursors stay out of the model's context. `limit` must be
1–50, Spotify's own page-size cap; out-of-range values are rejected before any request.

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
     ChatGPT should list the six tools.
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
all six tools and the error paths.

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
| *Search for 100 tracks by Taylor Swift.* | `limit` above 50: ChatGPT should either cap it at 50 or relay the validation error |

Playback, playlists and "my library" prompts are expected to fail, because Phase 1 only
has access to the public catalog.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the test suite on Python
3.12 and 3.13 for every push to `main` and every pull request, then boots the server
and asserts all six tools register. No secrets are needed — the tests are fully mocked.

## Layout

```
server.py       the MCP server (six tools + token caching)
test_server.py  22 tests, all offline
pyproject.toml  deps and pytest config, managed by uv
```

## Phase 2 ideas

- Authorization Code flow for user-scoped access
- Playback control (play, pause, skip, transfer device)
- Playlist read and write
- Recommendations and audio features
