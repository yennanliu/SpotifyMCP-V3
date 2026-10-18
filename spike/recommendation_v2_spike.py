"""Spike for docs/recommendation-v2.md: check its assumptions against the live API.

Read-only. Makes GET calls only and writes nothing to any Spotify account.

    SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... \
      [SPOTIFY_REFRESH_TOKEN=...] uv run python spike/recommendation_v2_spike.py

Sections:
  1. Endpoint probes: which endpoints and fields the doc says are gone, and are they?
  2. Playlist access: can we read the items of a playlist we don't own? (option D)
  3. Option C coverage: for 20 varied seeds, how many have collaborators,
     appears_on releases, genres, and genre-search hits?
  4. Option D hit rate: do playlists found by searching a seed contain it, and
     what do they co-occur with?
  5. E2E: every MCP tool in server.py, called through a real stdio MCP client.

SPOTIFY_REFRESH_TOKEN is optional. It enables the user-token checks in section 2:
reading a playlist the user owns, and a public playlist they don't.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

# Public, user-made playlist (the one the README's example prompts use).
PUBLIC_PLAYLIST = "1sKQOPBrwSLv0Ul80U8foE"
# Today's Top Hits: Spotify-owned editorial, closed to new apps since Nov 2024.
EDITORIAL_PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"
RADIOHEAD = "4Z8W4fKeB5YxbusRsdQVPb"
DAFT_PUNK = "4tZwfgrHOc3mvqYlEYSvVi"
CREEP = "70LcF31zb1H0PyJoS1Sx1r"

# Spread across languages, genres and eras on purpose: genre tags are thinnest
# for non-English and niche artists, which is where option C is weakest.
SEEDS = [
    "Creep Radiohead",
    "Blinding Lights The Weeknd",
    "Bohemian Rhapsody Queen",
    "Smells Like Teen Spirit Nirvana",
    "Lose Yourself Eminem",
    "Redbone Childish Gambino",
    "One More Time Daft Punk",
    "Strobe deadmau5",
    "Take Five Dave Brubeck",
    "Clair de Lune Debussy",
    "Despacito Luis Fonsi",
    "Levitating Dua Lipa",
    "Dynamite BTS",
    "Hype Boy NewJeans",
    "Lemon 米津玄師",
    "晴天 周杰倫",
    "告白氣球 周杰倫",
    "小幸運 田馥甄",
    "倔強 五月天",
    "Shape of You Ed Sheeran",
]


class Spotify:
    """A thin GET client that counts calls and waits out 429s."""

    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=15
        )
        self.calls = 0
        self.rate_limited = 0

    def get(self, path: str, **params) -> httpx.Response:
        while True:
            self.calls += 1
            resp = self.client.get(path, params=params)
            if resp.status_code != 429:
                return resp
            self.rate_limited += 1
            time.sleep(int(resp.headers.get("Retry-After", "2")) + 1)


def client_token() -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def user_token() -> str | None:
    refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    if not refresh:
        return None
    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=15,
    )
    if not resp.is_success:
        print(f"  (user token refresh failed: {resp.status_code})")
        return None
    if "refresh_token" in resp.json():
        # Spotify may rotate it; the caller's stored copy could now be stale.
        print("  NOTE: Spotify returned a new refresh token; the stored one may be stale.")
    return resp.json()["access_token"]


def outcome(resp: httpx.Response) -> str:
    if resp.is_success:
        return str(resp.status_code)
    try:
        error = resp.json().get("error")
        message = error.get("message") if isinstance(error, dict) else error
    except ValueError:
        message = resp.reason_phrase
    return f"{resp.status_code} {message}"


def probe_endpoints(sp: Spotify) -> dict:
    """Section 1: each row is (label, path, params, what the doc predicts)."""
    probes = [
        ("search limit=10", "/search", {"q": "radiohead", "type": "track", "limit": 10}, "200"),
        ("search limit=50", "/search", {"q": "radiohead", "type": "track", "limit": 50}, "400 (Feb 2026 cap)"),
        ("GET /tracks/{id}", f"/tracks/{CREEP}", {}, "200"),
        ("GET /artists/{id}", f"/artists/{RADIOHEAD}", {}, "200"),
        ("GET /artists/{id}/albums", f"/artists/{RADIOHEAD}/albums", {"limit": 10}, "200"),
        ("batch GET /artists?ids", "/artists", {"ids": f"{RADIOHEAD},{DAFT_PUNK}"}, "403/404 (removed)"),
        ("batch GET /tracks?ids", "/tracks", {"ids": CREEP}, "403/404 (removed)"),
        ("GET /artists/{id}/top-tracks", f"/artists/{RADIOHEAD}/top-tracks", {"market": "US"}, "403/404 (removed)"),
        ("GET /recommendations", "/recommendations", {"seed_tracks": CREEP}, "404 (Nov 2024)"),
        ("GET /artists/{id}/related-artists", f"/artists/{RADIOHEAD}/related-artists", {}, "404 (Nov 2024)"),
        ("GET /audio-features/{id}", f"/audio-features/{CREEP}", {}, "403 (Nov 2024)"),
        ("GET /browse/new-releases", "/browse/new-releases", {}, "403/404 (removed)"),
    ]
    rows, bodies = [], {}
    for label, path, params, expected in probes:
        resp = sp.get(path, **params)
        rows.append({"probe": label, "got": outcome(resp), "doc_expects": expected})
        if resp.is_success:
            bodies[label] = resp.json()

    fields = {}
    track = bodies.get("GET /tracks/{id}", {})
    artist = bodies.get("GET /artists/{id}", {})
    fields["track.popularity present"] = "popularity" in track
    fields["track.external_ids.isrc present"] = bool(track.get("external_ids", {}).get("isrc"))
    fields["track.available_markets present"] = "available_markets" in track
    fields["artist.popularity present"] = "popularity" in artist
    fields["artist.followers present"] = "followers" in artist
    fields["artist.genres (Radiohead)"] = artist.get("genres")
    return {"probes": rows, "fields": fields}


def playlist_items(sp: Spotify, playlist_id: str) -> dict:
    """Try every way of reading a playlist's tracks and report what comes back."""
    meta = sp.get(f"/playlists/{playlist_id}")
    body = meta.json() if meta.is_success else {}
    # The Feb 2026 rename moved `tracks` to `items`; check both shapes.
    embedded = body.get("items") or body.get("tracks") or {}
    old = sp.get(f"/playlists/{playlist_id}/tracks", limit=5)
    new = sp.get(f"/playlists/{playlist_id}/items", limit=5)

    def count(resp):
        return len(resp.json().get("items", [])) if resp.is_success else None

    return {
        "GET /playlists/{id}": outcome(meta),
        "owner": (body.get("owner") or {}).get("id"),
        "embedded items returned": len(embedded.get("items", []) or []) if isinstance(embedded, dict) else None,
        "GET /playlists/{id}/tracks": outcome(old),
        "  items": count(old),
        "GET /playlists/{id}/items": outcome(new),
        "  items ": count(new),
    }


def probe_playlists(sp: Spotify, user: Spotify | None) -> dict:
    """Section 2: playlist access with the app token and, if given, a user token."""
    result = {
        "app token, public user playlist": playlist_items(sp, PUBLIC_PLAYLIST),
        "app token, Spotify editorial playlist": playlist_items(sp, EDITORIAL_PLAYLIST),
    }
    # How often does playlist search hit null entries or Spotify-owned lists?
    resp = sp.get("/search", q="radiohead", type="playlist", limit=10)
    if resp.is_success:
        items = resp.json()["playlists"]["items"]
        result["playlist search: results / null / spotify-owned"] = (
            f"{len(items)} / {sum(1 for p in items if not p)} / "
            f"{sum(1 for p in items if p and p['owner']['id'] == 'spotify')}"
        )
    if user is None:
        result["user token"] = "skipped (no SPOTIFY_REFRESH_TOKEN)"
        return result

    me = user.get("/me").json()
    owned = [
        p for p in user.get("/me/playlists", limit=50).json().get("items", [])
        if p and p["owner"]["id"] == me.get("id")
    ]
    # Report shape only: playlist names and IDs are the user's own data.
    result["user token, own playlist"] = (
        {k: v for k, v in playlist_items(user, owned[0]["id"]).items() if k != "owner"}
        if owned else "no owned playlist found"
    )
    result["user token, someone else's public playlist"] = {
        k: v for k, v in playlist_items(user, PUBLIC_PLAYLIST).items() if k != "owner"
    }
    return result


def option_c_coverage(sp: Spotify) -> dict:
    """Section 3: which of option C's four signals fire for each seed."""
    start, calls_before = time.time(), sp.calls
    rows = []
    for query in SEEDS:
        hits = sp.get("/search", q=query, type="track", limit=1)
        items = hits.json()["tracks"]["items"] if hits.is_success else []
        if not items:
            rows.append({"seed": query, "found": False})
            continue
        track = items[0]
        artist_id = track["artists"][0]["id"]
        artist = sp.get(f"/artists/{artist_id}").json()
        genres = artist.get("genres", [])

        appears = sp.get(
            f"/artists/{artist_id}/albums", include_groups="appears_on", limit=10
        )
        own = sp.get(
            f"/artists/{artist_id}/albums", include_groups="album,single", limit=10
        )
        genre_tracks = genre_artists = 0
        if genres:
            year = (track["album"].get("release_date") or "2000")[:4]
            gt = sp.get(
                "/search",
                q=f'genre:"{genres[0]}" year:{int(year) - 3}-{int(year) + 3}',
                type="track",
                limit=10,
            )
            genre_tracks = len(gt.json()["tracks"]["items"]) if gt.is_success else 0
            ga = sp.get("/search", q=f'genre:"{genres[0]}"', type="artist", limit=10)
            genre_artists = len(ga.json()["artists"]["items"]) if ga.is_success else 0

        rows.append(
            {
                "seed": query,
                "found": True,
                "matched": f"{track['name']} - {track['artists'][0]['name']}",
                "C1 co-credited artists": len(track["artists"]) - 1,
                "C1 appears_on releases": len(appears.json().get("items", [])) if appears.is_success else None,
                "genres": genres,
                "C2 genre+era tracks": genre_tracks,
                "C3 genre artists": genre_artists,
                "C4 own releases": len(own.json().get("items", [])) if own.is_success else None,
            }
        )

    found = [r for r in rows if r["found"]]

    def share(pred):
        return f"{sum(1 for r in found if pred(r))}/{len(found)}"

    summary = {
        "seeds resolved": f"{len(found)}/{len(rows)}",
        "C1 has collaborators or appears_on": share(
            lambda r: r["C1 co-credited artists"] or r["C1 appears_on releases"]
        ),
        "has genres": share(lambda r: r["genres"]),
        "C2 genre+era search returns tracks": share(lambda r: r["C2 genre+era tracks"]),
        "C3 genre artist search returns artists": share(lambda r: r["C3 genre artists"]),
        "C4 has own releases": share(lambda r: r["C4 own releases"]),
        "only C4 fires (no C1/C2/C3)": share(
            lambda r: not (r["C1 co-credited artists"] or r["C1 appears_on releases"])
            and not r["C2 genre+era tracks"]
            and not r["C3 genre artists"]
        ),
        "calls": sp.calls - calls_before,
        "seconds (sequential)": round(time.time() - start, 1),
    }
    return {"summary": summary, "seeds": rows}


def option_d_cooccurrence(sp: Spotify, seeds: list[str]) -> dict:
    """Section 5: playlist co-occurrence, only meaningful if section 2 can read items.

    Playlist search matches names and descriptions, not contents, so the key
    number is how many returned playlists actually contain the seed track.
    """
    start, calls_before = time.time(), sp.calls
    rows = []
    for query in seeds:
        hits = sp.get("/search", q=query, type="track", limit=1)
        items = hits.json()["tracks"]["items"] if hits.is_success else []
        if not items:
            continue
        seed = items[0]
        seed_artist = seed["artists"][0]["id"]
        found = sp.get(
            "/search",
            q=f'"{seed["name"]}" {seed["artists"][0]["name"]}',
            type="playlist",
            limit=10,
        )
        playlists = [p for p in found.json()["playlists"]["items"] if p] if found.is_success else []
        has_track = has_artist = 0
        co: dict[str, float] = {}
        names: dict[str, str] = {}
        for p in playlists:
            page = sp.get(f"/playlists/{p['id']}/items", limit=100)
            if not page.is_success:
                continue
            tracks = [
                (i.get("item") or i.get("track"))
                for i in page.json().get("items", [])
            ]
            tracks = [t for t in tracks if t and t.get("type") == "track" and t.get("id")]
            ids = {t["id"] for t in tracks}
            artists = {a["id"] for t in tracks for a in t["artists"]}
            if seed["id"] in ids:
                has_track += 1
            elif seed_artist in artists:
                has_artist += 1
            else:
                continue  # search matched the name only; no evidence of similarity
            weight = 1 / (2 + len(tracks)) ** 0.5
            for t in tracks:
                if t["id"] == seed["id"] or seed_artist in {a["id"] for a in t["artists"]}:
                    continue
                co[t["id"]] = co.get(t["id"], 0) + weight
                names[t["id"]] = f"{t['name']} - {t['artists'][0]['name']}"
        top = sorted(co, key=co.get, reverse=True)[:5]
        rows.append(
            {
                "seed": f"{seed['name']} - {seed['artists'][0]['name']}",
                "playlists returned": len(playlists),
                "contain seed track": has_track,
                "contain only seed artist": has_artist,
                "candidates (other artists)": len(co),
                "top 5": [names[t] for t in top],
            }
        )
    usable = [r for r in rows if r["contain seed track"] + r["contain only seed artist"]]
    return {
        "summary": {
            "seeds with ≥1 usable playlist": f"{len(usable)}/{len(rows)}",
            "playlists containing seed track": f"{sum(r['contain seed track'] for r in rows)}/"
            f"{sum(r['playlists returned'] for r in rows)}",
            "calls": sp.calls - calls_before,
            "seconds (sequential)": round(time.time() - start, 1),
        },
        "seeds": rows,
    }


async def e2e_tools() -> list[dict]:
    """Section 4: call every tool through a real stdio MCP client."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    root = Path(__file__).resolve().parent.parent
    calls = [
        ("search_tracks", {"query": "radiohead", "limit": 3}),
        ("search_tracks", {"query": "radiohead", "limit": 20}),
        ("search_artists", {"query": "radiohead", "limit": 3}),
        ("get_track", {"track_id": CREEP}),
        ("get_artist", {"artist_id": RADIOHEAD}),
        ("get_artist_top_tracks", {"artist_id": RADIOHEAD, "market": "US"}),
        ("get_album_tracks", {"album_id": "6dVIqQ8qmQ5GBnJ9shOYGE", "limit": 3}),
        ("get_playlist_tracks", {"playlist": PUBLIC_PLAYLIST, "limit": 5}),
        ("recommend_from_playlist", {"playlist": PUBLIC_PLAYLIST, "limit": 5}),
    ]
    transport = StdioTransport(
        "uv", ["--directory", str(root), "run", "server.py"], env=dict(os.environ)
    )
    rows = []
    async with Client(transport) as client:
        registered = {t.name for t in await client.list_tools()}
        rows.append({"tool": "list_tools", "ok": True, "detail": f"{len(registered)} tools"})
        for name, args in calls:
            start = time.time()
            try:
                result = await client.call_tool(name, args)
                data = result.structured_content or {}
                data = data.get("result", data)
                size = len(data) if isinstance(data, (list, dict)) else 1
                rows.append({"tool": name, "args": args, "ok": True, "detail": f"{size} item(s)"})
            except Exception as exc:  # report every failure, don't stop at the first
                rows.append({"tool": name, "args": args, "ok": False, "detail": str(exc)[:160]})
            rows[-1]["seconds"] = round(time.time() - start, 1)
    return rows


def main() -> None:
    for var in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        if not os.environ.get(var):
            sys.exit(f"Set {var}")

    sp = Spotify(client_token())
    utoken = user_token()
    user = Spotify(utoken) if utoken else None

    report = {"run_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    print("1. endpoint probes ...")
    report["endpoints"] = probe_endpoints(sp)
    print("2. playlist access ...")
    report["playlists"] = probe_playlists(sp, user)
    print("3. option C coverage ...")
    report["option_c"] = option_c_coverage(sp)
    print("4. option D co-occurrence ...")
    # Ten seeds keeps it to ~120 calls; the spread still covers each language.
    report["option_d"] = option_d_cooccurrence(sp, SEEDS[::2])
    print("5. e2e tools over stdio ...")
    report["e2e"] = asyncio.run(e2e_tools())
    report["totals"] = {
        "app calls": sp.calls,
        "user calls": user.calls if user else 0,
        "429s": sp.rate_limited + (user.rate_limited if user else 0),
    }

    out = Path(__file__).with_name("results.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
