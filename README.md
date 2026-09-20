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
image arrays and pagination cursors stay out of the model's context.

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
rather than a 401 from Spotify.

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

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the test suite on Python
3.12 and 3.13 for every push to `main` and every pull request, then boots the server
and asserts all six tools register. No secrets are needed — the tests are fully mocked.

## Layout

```
server.py       the MCP server (six tools + token caching)
test_server.py  16 tests, all offline
pyproject.toml  deps and pytest config, managed by uv
```

## Phase 2 ideas

- Authorization Code flow for user-scoped access
- Playback control (play, pause, skip, transfer device)
- Playlist read and write
- Recommendations and audio features
