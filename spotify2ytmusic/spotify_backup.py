#!/usr/bin/env python3
#
#  This file is licensed under the MIT license
#  This file originates from https://github.com/caseychu/spotify-backup

import base64
import codecs
import hashlib
import http.client
import http.server
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

try:
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CONTEXT = None


DEFAULT_CLIENT_ID = "5c098bcc800e45d49e476265bc9b6934"


class SpotifyAPI:
    """Class to interact with the Spotify API using an OAuth token."""

    BASE_URL = "https://api.spotify.com/v1/"

    def __init__(self, auth):
        self._auth = auth

    def get(self, url, params={}, tries=3):
        """Fetch a resource from Spotify API."""
        url = self._construct_url(url, params)
        for _ in range(tries):
            try:
                req = self._create_request(url)
                return self._read_response(req)
            except Exception as err:
                print(f"Error fetching URL {url}: {err}")
                time.sleep(2)
        sys.exit("Failed to fetch data from Spotify API after retries.")

    def list(self, url, params={}):
        """Fetch paginated resources and return as a combined list."""
        response = self.get(url, params)
        items = response["items"]

        while response["next"]:
            response = self.get(response["next"])
            items += response["items"]
        return items

    @staticmethod
    def _pkce_verifier() -> str:
        return secrets.token_urlsafe(64)

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def _exchange_code_for_token(
        client_id: str, code: str, redirect_uri: str, code_verifier: str
    ) -> str:
        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, context=_SSL_CONTEXT) as res:
                reader = codecs.getreader("utf-8")
                payload = json.load(reader(res))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            sys.exit(f"Spotify token exchange failed ({e.code}): {err}")
        access_token = payload.get("access_token")
        if not access_token:
            sys.exit("Spotify token exchange failed: missing access_token in response.")
        return access_token

    @staticmethod
    def authorize(client_id, scope):
        """Open a browser for user authorization (PKCE) and return SpotifyAPI instance."""
        redirect_uri = f"http://127.0.0.1:{SpotifyAPI._SERVER_PORT}/redirect"
        code_verifier = SpotifyAPI._pkce_verifier()
        code_challenge = SpotifyAPI._pkce_challenge(code_verifier)
        url = SpotifyAPI._construct_auth_url(
            client_id, scope, redirect_uri, code_challenge
        )
        print(f"Open this link if the browser doesn't open automatically: {url}")
        webbrowser.open(url)

        server = SpotifyAPI._AuthorizationServer("127.0.0.1", SpotifyAPI._SERVER_PORT)
        server.spotify_client_id = client_id
        server.spotify_redirect_uri = redirect_uri
        server.spotify_code_verifier = code_verifier
        try:
            while True:
                server.handle_request()
        except SpotifyAPI._Authorization as auth:
            return SpotifyAPI(auth.access_token)
        finally:
            server.server_close()

    @staticmethod
    def _construct_auth_url(client_id, scope, redirect_uri, code_challenge=None):
        params = {
            "response_type": "code" if code_challenge else "token",
            "client_id": client_id,
            "scope": scope,
            "redirect_uri": redirect_uri,
        }
        if code_challenge:
            params["code_challenge_method"] = "S256"
            params["code_challenge"] = code_challenge
        return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
            params
        )

    def _construct_url(self, url, params):
        """Construct a full API URL."""
        if not url.startswith(self.BASE_URL):
            url = self.BASE_URL + url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return url

    def _create_request(self, url):
        """Create an authenticated request."""
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self._auth}")
        return req

    def _read_response(self, req):
        """Read and parse the response."""
        with urllib.request.urlopen(req, context=_SSL_CONTEXT) as res:
            reader = codecs.getreader("utf-8")
            return json.load(reader(res))

    _SERVER_PORT = 43019

    class _AuthorizationServer(http.server.HTTPServer):
        allow_reuse_address = True

        def __init__(self, host, port):
            super().__init__((host, port), SpotifyAPI._AuthorizationHandler)
            self.spotify_client_id = None
            self.spotify_redirect_uri = None
            self.spotify_code_verifier = None

        def handle_error(self, request, client_address):
            raise

    class _AuthorizationHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/redirect"):
                self._handle_redirect()
            elif self.path.startswith("/token?"):
                self._handle_token()
            else:
                self.send_error(404)

        def _handle_redirect(self):
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            if "error" in q:
                msg = (q.get("error_description") or q["error"] or ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"Authorization failed: {msg}".encode("utf-8"))
                sys.exit(f"Spotify authorization failed: {msg}")
            if "code" in q:
                code = q["code"][0]
                token = SpotifyAPI._exchange_code_for_token(
                    self.server.spotify_client_id,
                    code,
                    self.server.spotify_redirect_uri,
                    self.server.spotify_code_verifier,
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<script>close()</script>Thanks! You may now close this window."
                )
                raise SpotifyAPI._Authorization(token)
            # Legacy implicit grant fallback if fragment was returned
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b'<script>location.replace("token?" + location.hash.slice(1));</script>'
            )

        def _handle_token(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<script>close()</script>Thanks! You may now close this window."
            )
            match = re.search("access_token=([^&]*)", self.path)
            if not match:
                sys.exit("Spotify authorization failed: missing access_token.")
            raise SpotifyAPI._Authorization(match.group(1))

        def log_message(self, format, *args):
            pass

    class _Authorization(Exception):
        def __init__(self, access_token):
            self.access_token = access_token


def _tracks(items):
    """Normalize paging items to the {"track": {...}} shape backend.py reads.

    The Feb 2026 Spotify API renamed playlist entries from "track" to "item".
    Podcast episodes (type != "track") and unavailable entries are skipped.
    """
    normalized = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        track_obj = entry.get("track") or entry.get("item")
        if not track_obj:
            continue
        item_type = track_obj.get("type")
        if item_type and item_type != "track":
            continue
        normalized.append({**entry, "track": track_obj})
    return normalized


def fetch_user_data(spotify, dump):
    """Fetch playlists and liked songs based on the dump parameter."""
    playlists = []
    liked_albums = []

    if "liked" in dump:
        print("Loading liked albums and songs...")
        liked_tracks = _tracks(spotify.list("me/tracks", {"limit": 50}))
        liked_albums = spotify.list("me/albums", {"limit": 50})
        playlists.append({"name": "Liked Songs", "tracks": liked_tracks})

    if "playlists" in dump:
        print("Loading playlists...")
        playlist_data = spotify.list("me/playlists", {"limit": 50})
        for playlist in playlist_data:
            owner_id = (playlist.get("owner") or {}).get("id")
            if owner_id == "spotify":
                print(f"Skipping Spotify editorial playlist: {playlist.get('name')}")
                continue
            print(f"Loading playlist: {playlist['name']}")
            tracks_ref = (
                (playlist.get("tracks") or {}).get("href")
                or (playlist.get("items") or {}).get("href")
                or f"playlists/{playlist['id']}/tracks"
            )
            playlist["tracks"] = _tracks(spotify.list(tracks_ref, {"limit": 100}))
            playlists.append(playlist)

    return playlists, liked_albums


def write_to_file(file, format, playlists, liked_albums):
    """Write fetched data to a file in the specified format."""
    print(f"Writing to {file}...")
    with open(file, "w", encoding="utf-8") as f:
        if format == "json":
            json.dump({"playlists": playlists, "albums": liked_albums}, f)
        else:
            for playlist in playlists:
                f.write(playlist["name"] + "\r\n")
                for track in playlist["tracks"]:
                    track_info = track.get("track") or track.get("item")
                    if track_info:
                        f.write(
                            "{name}\t{artists}\t{album}\t{uri}\t{release_date}\r\n".format(
                                uri=track_info["uri"],
                                name=track_info["name"],
                                artists=", ".join(
                                    [
                                        artist["name"]
                                        for artist in track_info["artists"]
                                    ]
                                ),
                                album=track_info["album"]["name"],
                                release_date=track_info["album"]["release_date"],
                            )
                        )
                f.write("\r\n")


def main(dump="playlists,liked", format="json", file="playlists.json", token=""):
    print("Starting backup...")
    client_id = os.environ.get("SPOTIFY_CLIENT_ID") or DEFAULT_CLIENT_ID
    spotify = (
        SpotifyAPI(token)
        if token
        else SpotifyAPI.authorize(
            client_id=client_id,
            scope="playlist-read-private playlist-read-collaborative user-library-read",
        )
    )

    playlists, liked_albums = fetch_user_data(spotify, dump)
    write_to_file(file, format, playlists, liked_albums)
    print(f"Backup completed! Data written to {file}")


if __name__ == "__main__":
    main()
