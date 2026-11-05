#!/usr/bin/env python

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from spotify2ytmusic import backend, spotify_backup


class TestSpotifyBackupAndIteration(unittest.TestCase):
    def test_tracks_normalizes_item_and_track(self):
        new, old = {"type": "track", "name": "New"}, {"type": "track", "name": "Old"}
        items = [
            {"item": new},
            {"track": old},
            {"item": {"type": "episode", "name": "Podcast"}},
            {"item": None},
        ]
        self.assertEqual(
            spotify_backup._tracks(items),
            [{"item": new, "track": new}, {"track": old}],
        )

    def test_pkce_challenge_rfc7636_vector(self):
        self.assertEqual(
            spotify_backup.SpotifyAPI._pkce_challenge(
                "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
            ),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        )

    def test_normalize_spotify_playlist_id(self):
        self.assertEqual(
            backend.normalize_spotify_playlist_id(
                "spotify:playlist:68QlHDwCiXfhodLpS72iOx"
            ),
            "68QlHDwCiXfhodLpS72iOx",
        )
        self.assertEqual(
            backend.normalize_spotify_playlist_id(
                "https://open.spotify.com/playlist/68QlHDwCiXfhodLpS72iOx?si=abc123"
            ),
            "68QlHDwCiXfhodLpS72iOx",
        )

    def test_iter_spotify_playlist_supports_item_key_and_skips_malformed(self):
        data = {
            "playlists": [
                {
                    "id": "pl1",
                    "name": "Test",
                    "tracks": [
                        {
                            "item": {
                                "name": "Song Item",
                                "artists": [{"name": "Artist 1"}],
                                "album": {"name": "Album 1"},
                            }
                        },
                        {"item": {"name": "Malformed Missing Keys"}},
                        {"track": None},
                    ],
                }
            ]
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            tmp_path = f.name
        try:
            tracks = list(
                backend.iter_spotify_playlist(
                    "https://open.spotify.com/playlist/pl1",
                    spotify_playlist_file=tmp_path,
                    reverse_playlist=False,
                )
            )
            self.assertEqual(
                tracks, [backend.SongInfo("Song Item", "Artist 1", "Album 1")]
            )
        finally:
            os.remove(tmp_path)


class TestCopier(unittest.TestCase):
    @patch("spotify2ytmusic.backend.get_ytmusic")
    def test_copier_success(self, mock_get_ytmusic):
        mock_ytmusic_instance = MagicMock()
        mock_get_ytmusic.return_value = mock_ytmusic_instance
        mock_ytmusic_instance.get_playlist.return_value = {"title": "Test Playlist"}
        mock_ytmusic_instance.add_playlist_items.return_value = None

        backend.copier(
            backend.iter_spotify_playlist(
                "68QlHDwCiXfhodLpS72iOx",
                spotify_playlist_file="tests/playliststest.json",
            ),
            dst_pl_id="dst_test",
            track_sleep=0,
        )

        mock_ytmusic_instance.get_playlist.assert_called_once_with(
            playlistId="dst_test"
        )

    @patch("spotify2ytmusic.backend.get_ytmusic")
    def test_copier_albums(self, mock_get_ytmusic):
        mock_ytmusic_instance = MagicMock()
        mock_get_ytmusic.return_value = mock_ytmusic_instance
        mock_ytmusic_instance.get_playlist.return_value = {"title": "Test Playlist"}
        mock_ytmusic_instance.add_playlist_items.return_value = None

        backend.copier(
            backend.iter_spotify_liked_albums(
                spotify_playlist_file="tests/playliststest.json"
            ),
            dst_pl_id="dst_test",
            track_sleep=0,
        )

        mock_ytmusic_instance.get_playlist.assert_called_once_with(
            playlistId="dst_test"
        )

    @patch("spotify2ytmusic.backend.get_ytmusic")
    def test_copier_liked_playlists(self, mock_get_ytmusic):
        mock_ytmusic_instance = MagicMock()
        mock_get_ytmusic.return_value = mock_ytmusic_instance
        mock_ytmusic_instance.get_playlist.return_value = {"title": "Test Playlist"}
        mock_ytmusic_instance.add_playlist_items.return_value = None

        backend.copier(
            backend.iter_spotify_playlist(
                None, spotify_playlist_file="tests/playliststest.json"
            ),
            dst_pl_id="dst_test",
            track_sleep=0,
        )

        mock_ytmusic_instance.get_playlist.assert_called_once_with(
            playlistId="dst_test"
        )


if __name__ == "__main__":
    unittest.main()
