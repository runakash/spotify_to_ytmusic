#!/usr/bin/env python3

"""
Music Metadata Normalization and Matching

This module provides utilities for comparing and matching music tracks
between different streaming services (Spotify and YouTube Music) by normalizing metadata.
"""

import re


def transliterate_text(text: str) -> str:
    """Convert non-ASCII characters to their ASCII equivalents."""
    replacements = {
        **{c: "a" for c in "áàâäãå"},
        **{c: "A" for c in "ÁÀÂÄÃÅ"},
        **{c: "e" for c in "éèêë"},
        **{c: "E" for c in "ÉÈÊË"},
        **{c: "i" for c in "íìîï"},
        **{c: "I" for c in "ÍÌÎÏ"},
        **{c: "o" for c in "óòôöõø"},
        **{c: "O" for c in "ÓÒÔÖÕØ"},
        **{c: "u" for c in "úùûü"},
        **{c: "U" for c in "ÚÙÛÜ"},
        **{c: "y" for c in "ýÿ"},
        **{c: "Y" for c in "ÝŸ"},
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ñ": "n",
        "Ñ": "N",
        "ç": "c",
        "Ç": "C",
        "ß": "ss",
        "þ": "th",
        "Þ": "TH",
    }
    return "".join(replacements.get(char, char) for char in (text or ""))


def clean_metadata(metadata: str, metadata_type: str = "track") -> str:
    """Standardize and clean music metadata by removing variations and formatting."""
    metadata = transliterate_text(metadata).lower()
    metadata = metadata.replace("&", "and")

    if metadata_type == "track":
        metadata = re.sub(
            r"[\[(\{](?:feat|featuring|ft|with|prod|prod\.? by|produced by)\.? .*?[\]\)\}]",
            "",
            metadata,
        )
        metadata = re.sub(
            r"\b(?:deluxe|instrumental|bonus\strack|radio|video|edit|version|edition|single|mono|original|mix|lp|extended|remaster(?:ed)?|re-?edit)(?:\b|$)",
            "",
            metadata,
        )
        metadata = re.sub(
            r"\b(?:ft\.?|feat\.?|featuring|with|prod\.?(?:\s?by)?)\b.*$",
            "",
            metadata,
        )
    elif metadata_type == "album":
        metadata = re.sub(
            r"\b(?:the|super|deluxe|special|anniversary|\d{2}th|extended|version|expanded|edition|re-?master(?:ed)?)(?:\b|$)",
            "",
            metadata,
        )
        metadata = re.sub(r"\b(?:ep|lp|single|instrumentals?)(?:\b|$)", "", metadata)
    elif metadata_type == "artist":
        metadata = re.sub(r"\bthe\s+", "", metadata)
        metadata = re.sub(r"\s+", "", metadata)

    metadata = re.sub(r"(?<!\d)(?:17|18|19|20)\d{2}(?!\d)", "", metadata)
    metadata = re.sub(r"[.,\"`´’':;+*!\/\-\(\)\[\]\{\}]", "", metadata)
    metadata = re.sub(r"\s+", " ", metadata).strip()
    return metadata.strip()


def clean_track_name(track_name: str) -> str:
    return clean_metadata(track_name, "track")


def clean_album_name(album_name: str) -> str:
    return clean_metadata(album_name, "album")


def clean_artist_name(artist_name: str) -> str:
    return clean_metadata(artist_name, "artist")


def levenshtein_distance(text1: str, text2: str) -> int:
    """Calculate the edit distance between two strings."""
    if len(text1) < len(text2):
        return levenshtein_distance(text2, text1)
    if not text2:
        return len(text1)
    prev = list(range(len(text2) + 1))
    for i, ca in enumerate(text1):
        curr = [i + 1]
        for j, cb in enumerate(text2):
            curr.append(min(prev[j + 1] + 1, curr[-1] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def text_similarity(text1: str, text2: str) -> bool:
    """Determine if two text strings should be considered similar/matching."""
    text1 = text1.strip().lower()
    text2 = text2.strip().lower()

    if not text1 or not text2:
        return text1 == text2

    if len(text1) > 1.7 * len(text2) or len(text2) > 1.7 * len(text1):
        return False

    if text1 == text2 or text1 in text2 or text2 in text1:
        return True

    text1_parts = text1.split()
    text2_parts = text2.split()
    if len(text1_parts) > 1 and len(text1_parts) == len(text2_parts):
        if set(text1_parts) == set(text2_parts):
            return True

    if levenshtein_distance(text1, text2) <= 2:
        return True

    if text1.replace(" ", "") == text2.replace(" ", ""):
        return True

    return False


def normalized_track_name_match(track1_name: str, track2_name: str) -> bool:
    cleaned_track1 = clean_track_name(track1_name)
    cleaned_track2 = clean_track_name(track2_name)

    if text_similarity(cleaned_track1, cleaned_track2):
        return True

    track1_parts = track1_name.split(":")
    track2_parts = track2_name.split(":")

    if len(track1_parts) == 2:
        cleaned_track1 = clean_track_name(track1_parts[1])
    if len(track2_parts) == 2:
        cleaned_track2 = clean_track_name(track2_parts[1])

    return text_similarity(cleaned_track1, cleaned_track2)


def normalized_album_name_match(album1_name: str, album2_name: str) -> bool:
    cleaned_album1 = clean_album_name(album1_name)
    cleaned_album2 = clean_album_name(album2_name)
    return text_similarity(cleaned_album1, cleaned_album2)


def normalized_artist_name_match(artist1_name: str, artist2_name: str) -> bool:
    if artist1_name.strip().lower() in [
        name_part.strip()
        for name_part in re.split(r"[&,\/]|and|with", artist2_name.lower())
    ] or artist2_name.strip().lower() in [
        name_part.strip()
        for name_part in re.split(r"[&,\/]|and|with", artist1_name.lower())
    ]:
        return True

    cleaned_artist1 = clean_artist_name(artist1_name)
    cleaned_artist2 = clean_artist_name(artist2_name)
    return text_similarity(cleaned_artist1, cleaned_artist2)


def normalized_track_match(
    spotify_track_name: str,
    spotify_album: str,
    spotify_artist: str,
    youtube_track: dict,
) -> bool:
    """Determine if a Spotify track matches a YouTube Music track."""
    youtube_track_name = youtube_track.get("title", "")
    raw_album = youtube_track.get("album")
    if isinstance(raw_album, dict):
        youtube_album = raw_album.get("name") or spotify_album
    elif isinstance(raw_album, str) and raw_album:
        youtube_album = raw_album
    else:
        youtube_album = spotify_album

    artists = youtube_track.get("artists") or []
    youtube_artist = artists[0].get("name", "") if artists else ""

    if spotify_track_name == youtube_track_name and spotify_artist == youtube_artist:
        return True

    spotify_is_instrumental = (
        "instrumental"
        in f"{spotify_track_name} {spotify_album} {spotify_artist}".lower()
    )
    youtube_is_instrumental = (
        "instrumental"
        in f"{youtube_track_name} {youtube_album} {youtube_artist}".lower()
    )
    if spotify_is_instrumental != youtube_is_instrumental:
        return False

    if normalized_track_name_match(
        spotify_track_name, youtube_track_name
    ) and normalized_artist_name_match(spotify_artist, youtube_artist):
        if normalized_album_name_match(spotify_album, youtube_album):
            return True

        cleaned_spotify_track = clean_track_name(spotify_track_name)
        cleaned_youtube_track = clean_track_name(youtube_track_name)
        if cleaned_spotify_track == cleaned_youtube_track:
            return True

    return False
