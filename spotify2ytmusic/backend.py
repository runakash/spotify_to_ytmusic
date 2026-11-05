#!/usr/bin/env python3

import json
import sys
import os
import time
import re

import ytmusicapi
from ytmusicapi import YTMusic
from typing import Optional, Union, Iterator, Dict, List
from collections import namedtuple
from dataclasses import dataclass, field
from .normalized_metadata_algorithm import normalized_track_match


SongInfo = namedtuple("SongInfo", ["title", "artist", "album"])

DEFAULT_PROGRESS_FILE = "s2yt_progress.jsonl"


class CopyProgress:
    """Record of tracks already copied, so an interrupted copy can resume.

    Each copied track is appended as one JSON line and flushed right away, so
    progress survives a crash or Ctrl-C.  Tracks are keyed by destination
    (YTMusic playlist ID, or "LIKED" for liked songs) plus the Spotify
    title/artist/album, which lets a re-run skip them without searching again.
    """

    LIKED = "LIKED"

    def __init__(self, filename: Optional[str] = DEFAULT_PROGRESS_FILE):
        self.filename = filename
        self._done = set()
        if not filename or not os.path.exists(filename):
            return
        with open(filename, "r", encoding="utf-8") as f:
            contents = f.read()
        if contents and not contents.endswith("\n"):
            with open(filename, "a", encoding="utf-8") as f:
                f.write("\n")
        for line in contents.splitlines():
            try:
                entry = json.loads(line)
                self._done.add(
                    (entry["dst"], entry["title"], entry["artist"], entry["album"])
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    @staticmethod
    def _key(dst_pl_id: Optional[str], track: SongInfo) -> tuple:
        dst = dst_pl_id if dst_pl_id is not None else CopyProgress.LIKED
        return (dst, track.title, track.artist, track.album)

    def is_done(self, dst_pl_id: Optional[str], track: SongInfo) -> bool:
        return self._key(dst_pl_id, track) in self._done

    def mark_done(self, dst_pl_id: Optional[str], track: SongInfo, video_id: str):
        key = self._key(dst_pl_id, track)
        self._done.add(key)
        if not self.filename:
            return
        dst, title, artist, album = key
        entry = {
            "dst": dst,
            "title": title,
            "artist": artist,
            "album": album,
            "videoId": video_id,
        }
        with open(self.filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_ytmusic() -> YTMusic:
    """
    Create and return a YTMusic client instance.

    Tries authentication in the following order:
      1. YTMUSIC_COOKIE environment variable (rebuilds oauth.json automatically)
      2. oauth.json (OAuth or headers setup)
      3. browser.json (created by 'ytmusicapi browser')
      4. headers_auth.json (manual headers)

    Supports brand accounts via the YT_BRAND_ACCOUNT_ID environment variable.
    """
    cookie = os.environ.get("YTMUSIC_COOKIE")
    if cookie:
        ytmusicapi.setup(
            filepath="oauth.json",
            headers_raw=f"cookie: {cookie}\n"
            f"x-goog-authuser: {os.environ.get('YTMUSIC_AUTHUSER', '0')}\n"
            "authorization: SAPISIDHASH",
        )

    brand_account_id = os.environ.get("YT_BRAND_ACCOUNT_ID")
    kwargs = {}
    if brand_account_id:
        kwargs["user"] = brand_account_id

    candidates = ["oauth.json", "browser.json", "headers_auth.json"]
    for auth_file in candidates:
        if not os.path.exists(auth_file):
            continue
        try:
            return YTMusic(auth_file, **kwargs)
        except (json.decoder.JSONDecodeError, Exception) as e:
            print(f"WARNING: Failed to load '{auth_file}': {e}")
            print("         Trying next auth method...")
            continue

    print("ERROR: No valid YTMusic auth file found ('oauth.json', 'browser.json', or 'headers_auth.json').")
    print("       Have you logged in to YTMusic? Run 'ytmusicapi browser' or 'ytmusicapi oauth' to login.")
    sys.exit(1)


def _ytmusic_safe_title(title: str) -> str:
    """Normalize a title the way YTMusic will store it.

    YTMusic trims leading/trailing whitespace off playlist titles, so a title that
    still has it would never match on lookup and get re-created (and re-copied) on
    every run.  It also rejects "<" and ">"; swap in look-alike characters for those.
    """
    return title.strip().replace("<", "‹").replace(">", "›")


def _ytmusic_create_playlist(
    yt: YTMusic, title: str, description: str, privacy_status: str = "PRIVATE"
) -> str:
    """Wrapper on ytmusic.create_playlist

    This wrapper does retries with back-off because sometimes YouTube Music will
    rate limit requests or otherwise fail.

    privacy_status can be: PRIVATE, PUBLIC, or UNLISTED
    """

    def _create(
        yt: YTMusic, title: str, description: str, privacy_status: str
    ) -> Union[str, dict]:
        exception_sleep = 5
        for _ in range(10):
            try:
                """Create a playlist on YTMusic, retrying if it fails."""
                id = yt.create_playlist(
                    title=title, description=description, privacy_status=privacy_status
                )
                return id
            except Exception as e:
                print(
                    f"ERROR: (Retrying create_playlist: {title}) {e} in {exception_sleep} seconds"
                )
                time.sleep(exception_sleep)
                exception_sleep *= 2

        return {
            "s2yt error": 'ERROR: Could not create playlist "{title}" after multiple retries'
        }

    title = _ytmusic_safe_title(title)
    description = _ytmusic_safe_title(description)
    id = _create(yt, title, description, privacy_status)
    #  create_playlist returns a dict if there was an error
    if isinstance(id, dict):
        print(f"ERROR: Failed to create playlist (name: {title}): {id}")
        sys.exit(1)

    time.sleep(1)  # seems to be needed to avoid missing playlist ID error

    return id


def load_playlists_json(filename: str = "playlists.json", encoding: str = "utf-8"):
    """Load the `playlists.json` Spotify playlist file"""
    with open(filename, "r", encoding=encoding) as f:
        return json.load(f)


def create_playlist(pl_name: str, privacy_status: str = "PRIVATE") -> None:
    """Create a YTMusic playlist


    Args:
        `pl_name` (str): The name of the playlist to create. It should be different to "".

        `privacy_status` (str: PRIVATE, PUBLIC, UNLISTED) The privacy setting of created playlist.
    """
    yt = get_ytmusic()

    id = _ytmusic_create_playlist(
        yt, title=pl_name, description=pl_name, privacy_status=privacy_status
    )
    print(f"Playlist ID: {id}")


def iter_spotify_liked_albums(
    spotify_playlist_file: str = "playlists.json",
    spotify_encoding: str = "utf-8",
) -> Iterator[SongInfo]:
    """Songs from liked albums on Spotify."""
    spotify_pls = load_playlists_json(spotify_playlist_file, spotify_encoding)

    if "albums" not in spotify_pls:
        return None

    for album in [x["album"] for x in spotify_pls["albums"]]:
        for track in album["tracks"]["items"]:
            yield SongInfo(track["name"], track["artists"][0]["name"], album["name"])


def add_album_to_library(
    yt: YTMusic,
    album_name: str,
    artist_name: str,
    dry_run: bool = False,
    track_sleep: float = 0.5,
) -> bool:
    """
    Add a Spotify album to the YTMusic library using playlist-level liking.

    Unlike the per-track rate_song approach used by copier(), this calls
    rate_playlist(album_browse_id, "LIKE") which adds the album to the
    Library > Albums tab without inflating Liked Songs with individual tracks.

    Returns True if the album was found and added, False otherwise.
    """
    results = yt.search(query=f"{album_name} by {artist_name}", filter="albums")
    if not results:
        print(f"  ERROR: Album not found on YTMusic: {album_name} by {artist_name}")
        return False

    album = results[0]
    album_browse_id = album.get("browseId")
    if not album_browse_id:
        print(f"  ERROR: No browseId for album '{album_name}'")
        return False

    artist_display = "<Unknown>"
    if album.get("artists"):
        artist_display = album["artists"][0].get("name", "<Unknown>")
    print(f"  YTMusic: {album.get('title', album_name)} - {artist_display}")

    if dry_run:
        print("  (dry-run, skipped)")
        return True

    exception_sleep = 5
    last_error = None
    for _ in range(10):
        try:
            yt.rate_playlist(album_browse_id, "LIKE")
            break
        except Exception as e:
            last_error = e
            print(f"  ERROR: (Retrying add_album_to_library) {e} in {exception_sleep}s")
            time.sleep(exception_sleep)
            exception_sleep *= 2
    else:
        print(
            f"  ERROR: Failed to add album after multiple retries: {album_name} ({last_error})"
        )
        return False

    if track_sleep:
        time.sleep(track_sleep)

    return True


def normalize_spotify_playlist_id(raw_id: Optional[str]) -> Optional[str]:
    """Normalize a Spotify playlist ID, URI, or URL to the bare playlist ID."""
    if raw_id is None:
        return None
    cleaned = raw_id.strip()
    match = re.match(r"^spotify:playlist:([A-Za-z0-9]+)$", cleaned)
    if match:
        return match.group(1)
    match = re.search(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)", cleaned)
    if match:
        return match.group(1)
    return cleaned


def iter_spotify_playlist(
    src_pl_id: Optional[str] = None,
    spotify_playlist_file: str = "playlists.json",
    spotify_encoding: str = "utf-8",
    reverse_playlist: bool = True,
) -> Iterator[SongInfo]:
    """Songs from a specific album ("Liked Songs" if None)

    Args:
        `src_pl_id` (Optional[str], optional): The ID of the source playlist. Defaults to None.
        `spotify_playlist_file` (str, optional): The path to the playlists backup files. Defaults to "playlists.json".
        `spotify_encoding` (str, optional): Characters encoding. Defaults to "utf-8".
        `reverse_playlist` (bool, optional): Is the playlist reversed when loading?  Defaults to True.

    Yields:
        Iterator[SongInfo]: The song's information
    """
    spotify_pls = load_playlists_json(spotify_playlist_file, spotify_encoding)

    def find_spotify_playlist(spotify_pls: Dict, src_pl_id: Union[str, None]) -> Dict:
        """Return the spotify playlist that matches the `src_pl_id`.

        Args:
            `spotify_pls`: The playlist datastrcuture saved by spotify-backup.
            `src_pl_id`: The ID of a playlist to find, or None for the "Liked Songs" playlist.
        """
        normalized_id = normalize_spotify_playlist_id(src_pl_id)
        for src_pl in spotify_pls["playlists"]:
            if src_pl_id is None and str(src_pl.get("name")) == "Liked Songs":
                return src_pl
            if normalized_id is not None and str(src_pl.get("id")) == normalized_id:
                return src_pl
        msg = f"Could not find Spotify playlist {src_pl_id}"
        if normalized_id is not None and normalized_id != src_pl_id:
            msg += f" (normalized to {normalized_id})"
        if src_pl_id and re.match(r"^(PL|RD|OL)", src_pl_id):
            msg += ". That looks like a YouTube playlist ID; use a Spotify playlist ID or URL."
        raise ValueError(msg)

    src_pl = find_spotify_playlist(spotify_pls, src_pl_id)
    src_pl_name = src_pl["name"]

    print(f"== Spotify Playlist: {src_pl_name}")

    pl_tracks = src_pl["tracks"]
    if reverse_playlist:
        pl_tracks = reversed(pl_tracks)

    for src_track in pl_tracks:
        track_info = src_track.get("track") or src_track.get("item")
        if track_info is None:
            print(
                f"WARNING: Spotify track seems to be malformed, Skipping.  Track: {src_track!r}"
            )
            continue

        try:
            src_album_name = track_info["album"]["name"]
            src_track_artist = track_info["artists"][0]["name"]
            src_track_name = track_info["name"]
        except (KeyError, IndexError, TypeError):
            print(f"ERROR: Spotify track seems to be malformed.  Track: {src_track!r}")
            continue

        yield SongInfo(src_track_name, src_track_artist, src_album_name)


def get_playlist_id_by_name(yt: YTMusic, title: str) -> Optional[str]:
    """Look up a YTMusic playlist ID by name.

    Args:
        `yt` (YTMusic): _description_
        `title` (str): _description_

    Returns:
        Optional[str]: The playlist ID or None if not found.
    """
    #  ytmusicapi seems to run into some situations where it gives a Traceback on listing playlists
    #  https://github.com/sigma67/ytmusicapi/issues/539
    try:
        playlists = yt.get_library_playlists(limit=5000)
    except (KeyError, TypeError) as e:
        print("=" * 60)
        print(f"Attempting to look up playlist '{title}' failed with {type(e).__name__}: {e}")
        print(
            "This is a bug in ytmusicapi that prevents 'copy_all_playlists' from working."
        )
        print(
            "You will need to manually copy playlists using s2yt_list_playlists and s2yt_copy_playlist"
        )
        print(
            "until this bug gets resolved.  Try `pip install --upgrade ytmusicapi` just to verify"
        )
        print("you have the latest version of that library.")
        print("=" * 60)
        raise

    title = _ytmusic_safe_title(title)
    for pl in playlists:
        if _ytmusic_safe_title(pl["title"]) == title:
            return pl["playlistId"]

    return None


@dataclass
class ResearchDetails:
    query: Optional[str] = field(default=None)
    songs: Optional[List[Dict]] = field(default=None)
    suggestions: Optional[List[str]] = field(default=None)


def lookup_song(
    yt: YTMusic,
    track_name: str,
    artist_name: str,
    album_name,
    yt_search_algo: int,
    details: Optional[ResearchDetails] = None,
) -> dict:
    """Look up a song on YTMusic

    Given the Spotify track information, it does a lookup for the album by the same
    artist on YTMusic, then looks at the first 3 hits looking for a track with exactly
    the same name. In the event that it can't find that exact track, it then does
    a search of songs for the track name by the same artist and simply returns the
    first hit.

    The idea is that finding the album and artist and then looking for the exact track
    match will be more likely to be accurate than searching for the song and artist and
    relying on the YTMusic yt_search_algorithm to figure things out, especially for short tracks
    that might have many contradictory hits like "Survival by Yes".

    Args:
        `yt` (YTMusic)
        `track_name` (str): The name of the researched track
        `artist_name` (str): The name of the researched track's artist
        `album_name` (str): The name of the researched track's album
        `yt_search_algo` (int): 0 for exact matching, 1 for extended matching (search past 1st result), 2 for approximate matching (search in videos)
        `details` (ResearchDetails): If specified, more information about the search and the response will be populated for use by the caller.

    Raises:
        ValueError: If no track is found, it returns an error

    Returns:
        dict: The infos of the researched song
    """
    albums = yt.search(query=f"{album_name} by {artist_name}", filter="albums")
    for album in albums[:3]:
        # print(album)
        # print(f"ALBUM: {album['browseId']} - {album['title']} - {album['artists'][0]['name']}")

        try:
            for track in yt.get_album(album["browseId"])["tracks"]:
                if yt_search_algo == 3:
                    if normalized_track_match(
                        track_name, album_name, artist_name, track
                    ):
                        return track
                elif track["title"] == track_name:
                    return track
            # print(f"{track['videoId']} - {track['title']} - {track['artists'][0]['name']}")
        except Exception as e:
            print(f"Unable to lookup album ({e}), continuing...")

    query = f"{track_name} by {artist_name}"
    if details:
        details.query = query
        details.suggestions = yt.get_search_suggestions(query=query)
    songs = yt.search(query=query, filter="songs")

    match yt_search_algo:
        case 0:
            if details:
                details.songs = songs
            return songs[0]

        case 1:
            for song in songs:
                if (
                    song["title"] == track_name
                    and song["artists"][0]["name"] == artist_name
                    and song["album"]["name"] == album_name
                ):
                    return song
                # print(f"SONG: {song['videoId']} - {song['title']} - {song['artists'][0]['name']} - {song['album']['name']}")

            raise ValueError(
                f"Did not find {track_name} by {artist_name} from {album_name}"
            )

        case 2:
            #  This would need to do fuzzy matching
            for song in songs:
                # Remove everything in brackets in the song title
                song_title_without_brackets = re.sub(r"[\[(].*?[])]", "", song["title"])
                if (
                    (
                        song_title_without_brackets == track_name
                        and song["album"]["name"] == album_name
                    )
                    or (song_title_without_brackets == track_name)
                    or (song_title_without_brackets in track_name)
                    or (track_name in song_title_without_brackets)
                ) and (
                    song["artists"][0]["name"] == artist_name
                    or artist_name in song["artists"][0]["name"]
                ):
                    return song

            # Finds approximate match
            # This tries to find a song anyway. Works when the song is not released as a music but a video.
            else:
                track_name = track_name.lower()
                first_song_title = songs[0]["title"].lower()
                if (
                    track_name not in first_song_title
                    or songs[0]["artists"][0]["name"] != artist_name
                ):  # If the first song is not the one we are looking for
                    print("Not found in songs, searching videos")
                    new_songs = yt.search(
                        query=f"{track_name} by {artist_name}", filter="videos"
                    )  # Search videos

                    # From here, we search for videos reposting the song. They often contain the name of it and the artist. Like with 'Nekfeu - Ecrire'.
                    for new_song in new_songs:
                        new_song_title = new_song[
                            "title"
                        ].lower()  # People sometimes mess up the capitalization in the title
                        if (
                            track_name in new_song_title
                            and artist_name in new_song_title
                        ) or (track_name in new_song_title):
                            print("Found a video")
                            return new_song
                    else:
                        # Basically we only get here if the song isn't present anywhere on YouTube
                        raise ValueError(
                            f"Did not find {track_name} by {artist_name} from {album_name}"
                        )
                else:
                    return songs[0]

        case 3:
            if details:
                details.songs = songs
            for song in songs:
                if normalized_track_match(track_name, album_name, artist_name, song):
                    return song

            print(f"\nDid not find '{track_name} by {artist_name}' from {album_name}")
            for song in songs[:5]:
                artist = (
                    song["artists"][0]["name"]
                    if song.get("artists")
                    else "<Unknown>"
                )
                album = (
                    song["album"]["name"]
                    if isinstance(song.get("album"), dict)
                    else song.get("album", "<Unknown>")
                )
                print(f"    POSSIBLE MATCH: {song.get('title')} - {artist} - {album}")
            raise ValueError(
                f"Did not find '{track_name} by {artist_name}' from {album_name}"
            )


def copier(
    src_tracks: Iterator[SongInfo],
    dst_pl_id: Optional[str] = None,
    dry_run: bool = False,
    track_sleep: float = 0.1,
    yt_search_algo: int = 0,
    *,
    yt: Optional[YTMusic] = None,
    progress_file: Optional[str] = None,
):
    """
    Copy `src_tracks` to the YTMusic playlist `dst_pl_id` (or liked songs if None).

    Tracks recorded in `progress_file` as already copied to this destination are
    skipped.  Pass `progress_file=None` to disable progress tracking.
    """
    if yt is None:
        yt = get_ytmusic()

    progress = CopyProgress(progress_file)
    skipped_count = 0
    existing_video_ids = set()

    if dst_pl_id is not None:
        #  A just-created playlist can take a little while before it can be fetched.
        for wait in (5, 10, 20, 40, 0):
            try:
                yt_pl = yt.get_playlist(playlistId=dst_pl_id)
                break
            except Exception as e:
                if wait:
                    print(f"Playlist {dst_pl_id} not available yet, retrying in {wait}s")
                    time.sleep(wait)
                    continue
                print(f"ERROR: Unable to find YTMusic playlist {dst_pl_id}: {e}")
                print(
                    "       Make sure the YTMusic playlist ID is correct, it should be something like "
                )
                print("      'PL_DhcdsaJ7echjfdsaJFhdsWUd73HJFca'")
                sys.exit(1)
        print(f"== Youtube Playlist: {yt_pl['title']}")
        existing_video_ids = {
            track.get("videoId")
            for track in yt_pl.get("tracks", [])
            if isinstance(track, dict) and track.get("videoId")
        }

    tracks_added_set = set()
    duplicate_count = 0
    error_count = 0

    for src_track in src_tracks:
        if progress.is_done(dst_pl_id, src_track):
            skipped_count += 1
            continue

        print(f"Spotify:   {src_track.title} - {src_track.artist} - {src_track.album}")

        try:
            dst_track = lookup_song(
                yt, src_track.title, src_track.artist, src_track.album, yt_search_algo
            )
        except Exception as e:
            print(f"ERROR: Unable to look up song on YTMusic: {e}")
            error_count += 1
            continue

        yt_artist_name = "<Unknown>"
        if "artists" in dst_track and len(dst_track["artists"]) > 0:
            yt_artist_name = dst_track["artists"][0]["name"]
        print(
            f"  Youtube: {dst_track['title']} - {yt_artist_name} - {dst_track['album'] if 'album' in dst_track else '<Unknown>'}"
        )

        if (
            dst_track["videoId"] in tracks_added_set
            or dst_track["videoId"] in existing_video_ids
        ):
            print("(DUPLICATE, this track has already been added)")
            duplicate_count += 1
            tracks_added_set.add(dst_track["videoId"])
            continue
        tracks_added_set.add(dst_track["videoId"])

        if not dry_run:
            exception_sleep = 5
            for _ in range(10):
                try:
                    if dst_pl_id is not None:
                        yt.add_playlist_items(
                            playlistId=dst_pl_id,
                            videoIds=[dst_track["videoId"]],
                            duplicates=False,
                        )
                    else:
                        yt.rate_song(dst_track["videoId"], "LIKE")
                    progress.mark_done(dst_pl_id, src_track, dst_track["videoId"])
                    break
                except Exception as e:
                    if "HTTP 401" in str(e) or "401" in str(e):
                        print(f"ERROR: YTMusic rejected the login ({e})")
                        print("       Refresh your YTMusic credentials and re-run.")
                        sys.exit(2)
                    print(
                        f"ERROR: (Retrying add_playlist_items: {dst_pl_id} {dst_track['videoId']}) {e} in {exception_sleep} seconds"
                    )
                    time.sleep(exception_sleep)
                    exception_sleep *= 2

        if track_sleep:
            time.sleep(track_sleep)

    print()
    if skipped_count:
        print(f"Skipped {skipped_count} tracks already copied in a previous run")
    print(
        f"Added {len(tracks_added_set)} tracks, encountered {duplicate_count} duplicates, {error_count} errors"
    )


def copy_playlist(
    spotify_playlist_id: str,
    ytmusic_playlist_id: str,
    spotify_playlists_encoding: str = "utf-8",
    dry_run: bool = False,
    track_sleep: float = 0.1,
    yt_search_algo: int = 0,
    reverse_playlist: bool = True,
    privacy_status: str = "PRIVATE",
    progress_file: Optional[str] = DEFAULT_PROGRESS_FILE,
):
    """
    Copy a Spotify playlist to a YTMusic playlist
    @@@
    """
    print("Using search algo n°: ", yt_search_algo)
    yt = get_ytmusic()
    pl_name: str = ""

    spotify_playlist_id = normalize_spotify_playlist_id(spotify_playlist_id)

    if ytmusic_playlist_id and ytmusic_playlist_id.startswith("+"):
        pl_name = ytmusic_playlist_id[1:]

        ytmusic_playlist_id = get_playlist_id_by_name(yt, pl_name)
        print(f"Looking up playlist '{pl_name}': id={ytmusic_playlist_id}")

    if ytmusic_playlist_id is None or ytmusic_playlist_id == "":
        if pl_name == "":
            print("No playlist name or ID provided, creating playlist...")
            spotify_pls: dict = load_playlists_json()
            for pl in spotify_pls["playlists"]:
                if len(pl.keys()) > 3 and pl["id"] == spotify_playlist_id:
                    pl_name = pl["name"]

        ytmusic_playlist_id = _ytmusic_create_playlist(
            yt,
            title=pl_name,
            description=pl_name,
            privacy_status=privacy_status,
        )

        #  create_playlist returns a dict if there was an error
        if isinstance(ytmusic_playlist_id, dict):
            print(f"ERROR: Failed to create playlist: {ytmusic_playlist_id}")
            sys.exit(1)
        print(f"NOTE: Created playlist '{pl_name}' with ID: {ytmusic_playlist_id}")

    copier(
        iter_spotify_playlist(
            spotify_playlist_id,
            spotify_encoding=spotify_playlists_encoding,
            reverse_playlist=reverse_playlist,
        ),
        ytmusic_playlist_id,
        dry_run,
        track_sleep,
        yt_search_algo,
        yt=yt,
        progress_file=progress_file,
    )


def copy_all_playlists(
    track_sleep: float = 0.1,
    dry_run: bool = False,
    spotify_playlists_encoding: str = "utf-8",
    yt_search_algo: int = 0,
    reverse_playlist: bool = True,
    privacy_status: str = "PRIVATE",
    progress_file: Optional[str] = DEFAULT_PROGRESS_FILE,
):
    """
    Copy all Spotify playlists (except Liked Songs) to YTMusic playlists
    """
    spotify_pls = load_playlists_json()
    yt = get_ytmusic()

    for src_pl in spotify_pls["playlists"]:
        if str(src_pl.get("name")) == "Liked Songs":
            continue

        pl_name = src_pl["name"]
        if pl_name == "":
            pl_name = f"Unnamed Spotify Playlist {src_pl['id']}"

        dst_pl_id = get_playlist_id_by_name(yt, pl_name)
        print(f"Looking up playlist '{pl_name}': id={dst_pl_id}")
        if dst_pl_id is None:
            dst_pl_id = _ytmusic_create_playlist(
                yt, title=pl_name, description=pl_name, privacy_status=privacy_status
            )

            #  create_playlist returns a dict if there was an error
            if isinstance(dst_pl_id, dict):
                print(f"ERROR: Failed to create playlist: {dst_pl_id}")
                sys.exit(1)
            print(f"NOTE: Created playlist '{pl_name}' with ID: {dst_pl_id}")

        copier(
            iter_spotify_playlist(
                src_pl["id"],
                spotify_encoding=spotify_playlists_encoding,
                reverse_playlist=reverse_playlist,
            ),
            dst_pl_id,
            dry_run,
            track_sleep,
            yt_search_algo,
            yt=yt,
            progress_file=progress_file,
        )
        print("\nPlaylist done!\n")

    print("All done!")
