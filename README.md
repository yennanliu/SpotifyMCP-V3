# Spotify MCP

A minimal [FastMCP](https://gofastmcp.com) server exposing Spotify's catalog as MCP tools.

**Phase 1** uses the Client Credentials flow: app-level auth, no user login. That covers
public catalog data. User-specific features (playback control, playlists, saved library)
require the Authorization Code flow and are out of scope here.

## Tools

| Tool | Description |
| --- | --- |
| `search_tracks(query, limit=10)` | Search for tracks |
| `search_artists(query, limit=10)` | Search for artists |
| `get_track(track_id)` | One track's details |
| `get_artist(artist_id)` | One artist's details |
| `get_artist_top_tracks(artist_id, market="US")` | An artist's top tracks |
| `get_album_tracks(album_id, limit=50)` | An album's track list |

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

That serves over stdio. For HTTP instead:

```bash
uv run fastmcp run server.py:mcp --transport http --port 8000
```

## Use from Claude Code

```bash
claude mcp add spotify \
  -e SPOTIFY_CLIENT_ID=... \
  -e SPOTIFY_CLIENT_SECRET=... \
  -- uv --directory /Users/jliu/SpotifyMCP-V3 run server.py
```

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
