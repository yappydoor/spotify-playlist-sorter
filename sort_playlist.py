#!/usr/bin/env python3
"""Sort tracks in your Spotify playlists."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

import requests
from dotenv import load_dotenv

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private"
TOKEN_CACHE = ".token_cache.json"
CHUNK = 100

SORT_KEYS = ("artist", "album", "title", "added_at")


class AuthError(RuntimeError):
    pass


class SpotifyAPI:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.session = requests.Session()
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    def authenticate(self) -> None:
        if self._load_cache() and self._refresh_if_needed():
            return
        self._authorization_code_flow()

    def _load_cache(self) -> bool:
        if not os.path.exists(TOKEN_CACHE):
            return False
        try:
            with open(TOKEN_CACHE, encoding="utf-8") as f:
                data = json.load(f)
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self._expires_at = data.get("expires_at", 0)
            return bool(self.refresh_token or self.access_token)
        except (OSError, json.JSONDecodeError, ValueError):
            return False

    def _save_cache(self) -> None:
        with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_at": getattr(self, "_expires_at", 0),
                },
                f,
            )

    def _set_tokens(self, payload: dict[str, Any]) -> None:
        self.access_token = payload["access_token"]
        if "refresh_token" in payload:
            self.refresh_token = payload["refresh_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60
        self._save_cache()

    def _refresh_if_needed(self) -> bool:
        if self.access_token and time.time() < getattr(self, "_expires_at", 0):
            return True
        if not self.refresh_token:
            return False
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
        }
        resp = requests.post(
            TOKEN_URL,
            data=data,
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        if resp.status_code != 200:
            return False
        self._set_tokens(resp.json())
        return True

    def _authorization_code_flow(self) -> None:
        state = secrets.token_urlsafe(16)
        code_verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

        parsed = urllib.parse.urlparse(self.redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8888
        path = parsed.path or "/callback"

        result: dict[str, str] = {}
        event = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                req = urllib.parse.urlparse(self.path)
                if req.path != path:
                    self.send_response(404)
                    self.end_headers()
                    return
                q = urllib.parse.parse_qs(req.query)
                if q.get("state", [None])[0] != state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid state")
                    event.set()
                    return
                if "error" in q:
                    result["error"] = q["error"][0]
                    body = b"<h1>Authorization failed</h1><p>You can close this tab.</p>"
                else:
                    result["code"] = q["code"][0]
                    body = b"<h1>OK</h1><p>You can close this tab and return to the terminal.</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
                event.set()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        server = HTTPServer((host, port), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        print("\nOpen this link in your browser and approve access:")
        print(url)
        print()
        webbrowser.open(url)

        if not event.wait(timeout=180):
            server.server_close()
            raise AuthError("Authorization timed out (3 minutes). Run again.")

        server.server_close()
        if "error" in result:
            raise AuthError(f"Spotify denied access: {result['error']}")
        if "code" not in result:
            raise AuthError("No authorization code received.")

        token_data = {
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        resp = requests.post(
            TOKEN_URL,
            data=token_data,
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(f"Failed to get token: {resp.status_code} {resp.text}")
        self._set_tokens(resp.json())
        print("Authorization successful.\n")

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self._refresh_if_needed():
            self.authenticate()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        resp = self.session.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 401:
            if self.refresh_token and self._refresh_if_needed():
                headers["Authorization"] = f"Bearer {self.access_token}"
                resp = self.session.request(method, url, headers=headers, timeout=30, **kwargs)
            else:
                self.authenticate()
                headers["Authorization"] = f"Bearer {self.access_token}"
                resp = self.session.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "2"))
            time.sleep(retry)
            return self.request(method, path, **kwargs)
        if not resp.ok:
            raise RuntimeError(f"API {method} {path}: {resp.status_code} {resp.text}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def get_me(self) -> dict[str, Any]:
        return self.request("GET", "/me")

    def get_my_playlists(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        url = "/me/playlists?limit=50"
        while url:
            data = self.request("GET", url)
            items.extend(data.get("items", []))
            next_url = data.get("next")
            url = next_url.replace(API_BASE, "") if next_url else ""
        return items

    def get_playlist_items(self, playlist_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        url = (
            f"/playlists/{playlist_id}/items?limit=50"
            f"&additional_types=track,episode&market=from_token"
        )
        while url:
            data = self.request("GET", url)
            items.extend(data.get("items", []))
            next_url = data.get("next")
            url = next_url.replace(API_BASE, "") if next_url else ""
        # Newer Web API puts the object in "item"; older responses use "track".
        for row in items:
            if not row.get("track") and row.get("item"):
                row["track"] = row["item"]
        return items

    def replace_playlist_items(self, playlist_id: str, uris: list[str]) -> None:
        # First chunk replaces the playlist; remaining chunks are appended.
        first = uris[:CHUNK]
        self.request(
            "PUT",
            f"/playlists/{playlist_id}/items",
            json={"uris": first},
        )
        for i in range(CHUNK, len(uris), CHUNK):
            chunk = uris[i : i + CHUNK]
            self.request(
                "POST",
                f"/playlists/{playlist_id}/items",
                json={"uris": chunk},
            )


def playlist_track(item: dict[str, Any]) -> dict[str, Any]:
    track = item.get("track") or item.get("item") or {}
    return track if isinstance(track, dict) else {}


def track_uri(item: dict[str, Any]) -> str | None:
    track = playlist_track(item)
    if not track or track.get("is_local"):
        return None
    uri = track.get("uri")
    if uri and (uri.startswith("spotify:track:") or uri.startswith("spotify:episode:")):
        return uri
    return None


def sort_key_fn(mode: str) -> Callable[[dict[str, Any]], tuple]:
    def artist(item: dict[str, Any]) -> tuple:
        track = playlist_track(item)
        artists = track.get("artists") or []
        name = (artists[0].get("name") if artists else "") or ""
        album = (track.get("album") or {}).get("name") or ""
        disc = track.get("disc_number") or 0
        num = track.get("track_number") or 0
        title = track.get("name") or ""
        return (name.casefold(), album.casefold(), disc, num, title.casefold())

    def album(item: dict[str, Any]) -> tuple:
        track = playlist_track(item)
        album_obj = track.get("album") or {}
        album_name = album_obj.get("name") or ""
        artists = track.get("artists") or []
        artist_name = (artists[0].get("name") if artists else "") or ""
        disc = track.get("disc_number") or 0
        num = track.get("track_number") or 0
        title = track.get("name") or ""
        return (album_name.casefold(), artist_name.casefold(), disc, num, title.casefold())

    def title(item: dict[str, Any]) -> tuple:
        track = playlist_track(item)
        return ((track.get("name") or "").casefold(),)

    def added_at(item: dict[str, Any]) -> tuple:
        return (item.get("added_at") or "",)

    return {
        "artist": artist,
        "album": album,
        "title": title,
        "added_at": added_at,
    }[mode]


def pick_playlist(playlists: list[dict[str, Any]], me_id: str) -> dict[str, Any]:
    owned = []
    for i, p in enumerate(playlists, 1):
        owner = (p.get("owner") or {}).get("id")
        flag = " (yours)" if owner == me_id else ""
        print(f"{i:3}. {p.get('name')} — {p.get('tracks', {}).get('total', '?')} tracks{flag}")
        owned.append(p)

    while True:
        raw = input("\nPlaylist number: ").strip()
        if not raw.isdigit():
            print("Enter a number.")
            continue
        idx = int(raw)
        if 1 <= idx <= len(owned):
            return owned[idx - 1]
        print("No such number.")


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Sort a Spotify playlist")
    parser.add_argument(
        "--by",
        choices=SORT_KEYS,
        default=None,
        help="Sort key: artist, album, title, added_at",
    )
    parser.add_argument("--playlist", help="Playlist ID or URL (otherwise pick from a list)")
    parser.add_argument(
        "--desc",
        action="store_true",
        help="Sort descending",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview order only; do not modify the playlist",
    )
    args = parser.parse_args()

    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip()

    if not client_id or not client_secret:
        print(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET are required.\n"
            "1) Copy .env.example → .env\n"
            "2) Paste keys from the Spotify Developer Dashboard\n"
            "See README.md"
        )
        return 1

    api = SpotifyAPI(client_id, client_secret, redirect_uri)
    try:
        api.authenticate()
    except AuthError as e:
        print(f"Authorization error: {e}")
        return 1

    me = api.get_me()
    me_id = me["id"]
    print(f"Signed in as: {me.get('display_name') or me_id}")

    if args.playlist:
        playlist_id = args.playlist.strip()
        if "playlist/" in playlist_id:
            playlist_id = playlist_id.split("playlist/")[1].split("?")[0]
        playlist = api.request("GET", f"/playlists/{playlist_id}")
    else:
        playlists = api.get_my_playlists()
        if not playlists:
            print("No playlists found.")
            return 1
        playlist = pick_playlist(playlists, me_id)

    playlist_id = playlist["id"]
    owner_id = (playlist.get("owner") or {}).get("id")
    if owner_id != me_id:
        print("Warning: this playlist is not yours. Changes may fail.")

    mode = args.by
    if not mode:
        print("\nSort by:")
        for i, key in enumerate(SORT_KEYS, 1):
            labels = {
                "artist": "artist (+ album, track number)",
                "album": "album (+ track number)",
                "title": "track title",
                "added_at": "date added",
            }
            print(f"  {i}. {key} — {labels[key]}")
        while True:
            raw = input("Choice (1-4): ").strip()
            if raw.isdigit() and 1 <= int(raw) <= 4:
                mode = SORT_KEYS[int(raw) - 1]
                break
            if raw in SORT_KEYS:
                mode = raw
                break
            print("Enter 1–4 or a sort key name.")

    print(f"\nLoading tracks: “{playlist.get('name')}”…")
    items = api.get_playlist_items(playlist_id)
    sortable = [it for it in items if track_uri(it)]
    skipped = len(items) - len(sortable)
    if skipped:
        print(f"Skipped local/empty items: {skipped}")

    key_fn = sort_key_fn(mode)
    sorted_items = sorted(sortable, key=key_fn, reverse=args.desc)
    uris = [track_uri(it) for it in sorted_items]
    uris = [u for u in uris if u]

    print(f"Tracks to write: {len(uris)}")
    print("First 10 after sorting:")
    for i, it in enumerate(sorted_items[:10], 1):
        track = playlist_track(it)
        name = track.get("name") or "(untitled / unavailable)"
        if track.get("type") == "episode":
            show = (track.get("show") or {}).get("name") or ""
            print(f"  {i}. {show} — {name}")
        else:
            artists = ", ".join(
                a.get("name", "") for a in (track.get("artists") or []) if a.get("name")
            ) or "(no artist)"
            album = (track.get("album") or {}).get("name") or ""
            print(f"  {i}. {artists} — {name} [{album}]")

    if args.dry_run:
        print("\n--dry-run: playlist was not changed.")
        return 0

    if not confirm(f"\nOverwrite order in “{playlist.get('name')}”?"):
        print("Cancelled.")
        return 0

    print("Writing new order…")
    api.replace_playlist_items(playlist_id, uris)
    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
