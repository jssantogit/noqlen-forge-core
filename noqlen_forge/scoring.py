from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from .audio import Track


@dataclass(slots=True)
class ScoredRelease:
    release: dict
    score: int
    reasons: list[str]


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value).strip().lower()
    return re.sub(r"\s+", " ", value)


def score_release(tracks: list[Track], release: dict) -> ScoredRelease:
    reasons: list[str] = []
    score = 0
    local_artist = normalize_text(_album_artist(tracks))
    local_album = normalize_text(_album_title(tracks))
    release_artist = normalize_text(_artist_credit(release))
    release_title = normalize_text(release.get("title", ""))
    release_tracks = release_track_rows(release)

    artist_ratio = _ratio(local_artist, release_artist)
    album_ratio = _ratio(local_album, release_title)
    if len(tracks) == 1:
        album_ratio = max(album_ratio, _ratio(normalize_text(_strip_single_suffix(_album_title(tracks))), release_title), _ratio(normalize_text(tracks[0].title), release_title))
    score += round(20 * artist_ratio)
    score += round(20 * album_ratio)
    reasons.append(f"artist={round(20 * artist_ratio)}/20")
    reasons.append(f"album={round(20 * album_ratio)}/20")

    if len(release_tracks) == len(tracks):
        score += 20
        reasons.append("track_count=20/20")
    else:
        diff = abs(len(release_tracks) - len(tracks))
        points = max(0, 20 - diff * 6)
        score += points
        reasons.append(f"track_count={points}/20")

    title_points, order_points, duration_points = _track_points(tracks, release_tracks)
    score += title_points + order_points + duration_points
    reasons.append(f"track_titles={title_points}/20")
    reasons.append(f"track_order={order_points}/8")
    reasons.append(f"duration={duration_points}/7")

    if release.get("status", "").lower() == "official":
        score += 3
        reasons.append("official=3/3")
    if str(release.get("country", "")).upper() in {"", "XW"}:
        score += 1
        reasons.append("country=1/1")
    if release.get("date") or release.get("label-info"):
        score += 1
        reasons.append("date_label=1/1")

    return ScoredRelease(release=release, score=min(score, 100), reasons=reasons)


def rank_releases(tracks: list[Track], releases: list[dict]) -> list[ScoredRelease]:
    return sorted((score_release(tracks, release) for release in releases), key=lambda item: item.score, reverse=True)


def release_track_rows(release: dict) -> list[dict]:
    rows: list[dict] = []
    for medium in release.get("media", []) or []:
        for track in medium.get("tracks", []) or []:
            recording = track.get("recording") or {}
            rows.append(
                {
                    "id": track.get("id", ""),
                    "recording_id": recording.get("id", ""),
                    "title": track.get("title") or recording.get("title", ""),
                    "position": track.get("position"),
                    "length": (track.get("length") or recording.get("length") or 0) / 1000,
                }
            )
    return rows


def _track_points(tracks: list[Track], release_tracks: list[dict]) -> tuple[int, int, int]:
    if not tracks or not release_tracks:
        return 0, 0, 0
    pairs = list(zip(sorted(tracks, key=lambda t: t.tracknumber or 999), release_tracks))
    title_hits = 0
    order_hits = 0
    duration_hits = 0
    for index, (track, mb_track) in enumerate(pairs, start=1):
        if _ratio(normalize_text(track.title), normalize_text(mb_track.get("title", ""))) >= 0.86:
            title_hits += 1
        if track.tracknumber in {None, index, mb_track.get("position")}:
            order_hits += 1
        mb_length = mb_track.get("length") or 0
        if track.duration is None or not mb_length or abs(track.duration - mb_length) <= 4:
            duration_hits += 1
    denominator = max(len(tracks), len(release_tracks))
    return round(20 * title_hits / denominator), round(8 * order_hits / denominator), round(7 * duration_hits / denominator)


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _album_artist(tracks: list[Track]) -> str:
    for track in tracks:
        if track.albumartist:
            return track.albumartist
    return tracks[0].artist if tracks else ""


def _album_title(tracks: list[Track]) -> str:
    return tracks[0].album if tracks else ""


def _strip_single_suffix(value: str) -> str:
    clean = value.strip()
    suffixes = (" - Single", "- Single", " Single", "(Single)", "[Single]", " - EP", "(EP)")
    lowered = clean.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix.lower()):
            return clean[: -len(suffix)].strip()
    return clean


def _artist_credit(release: dict) -> str:
    credits = release.get("artist-credit") or []
    return "".join(item.get("name", "") if isinstance(item, dict) else str(item) for item in credits)
