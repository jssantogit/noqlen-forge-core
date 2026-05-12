from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def normalize_punctuation(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_feat(text: str) -> str:
    text = re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", "", text or "", flags=re.IGNORECASE)
    text = re.sub(r"\((?:feat\.?|ft\.?|featuring)\s+[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[(?:feat\.?|ft\.?|featuring)\s+[^]]*\]", "", text, flags=re.IGNORECASE)
    return text.strip()


def strip_parenthetical_noise(text: str) -> str:
    noise = r"(?:remaster(?:ed)?|deluxe|explicit|clean|radio edit|edit|version|single version|mono|stereo|bonus track|sped up|slowed|instrumental)"
    text = re.sub(rf"\s*[\[(][^\])]*(?:{noise})[^\])]*[\])]", "", text or "", flags=re.IGNORECASE)
    return text.strip()


def normalize_artist_name(text: str) -> str:
    return normalize_punctuation(strip_feat(text))


def normalize_album_title(text: str) -> str:
    return normalize_punctuation(strip_parenthetical_noise(text))


def normalize_track_title(text: str) -> str:
    return normalize_punctuation(strip_parenthetical_noise(strip_feat(text)))


def compare_ratio(left: str, right: str) -> float:
    left = normalize_punctuation(left)
    right = normalize_punctuation(right)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def confidence_allows(confidence: str, minimum: str) -> bool:
    return CONFIDENCE_ORDER.get(confidence, -1) >= CONFIDENCE_ORDER.get(minimum, 1)


def match_confidence(artist_ratio: float = 0.0, album_ratio: float = 0.0, title_ratio: float = 0.0, duration_delta: float | None = None, mbid_match: bool = False, barcode_match: bool = False, title_only: bool = False) -> tuple[str, str]:
    if mbid_match:
        return "high", "release MBID match"
    if barcode_match:
        return "high", "barcode match"
    duration_ok = duration_delta is None or duration_delta <= 3
    duration_far = duration_delta is not None and duration_delta > 8
    if artist_ratio >= 0.96 and album_ratio >= 0.96 and duration_ok:
        return "high", "artist and album exact match"
    if artist_ratio >= 0.92 and title_ratio >= 0.94 and duration_ok and album_ratio >= 0.85:
        return "high", "artist, title, album and duration match"
    if artist_ratio >= 0.86 and (album_ratio >= 0.86 or title_ratio >= 0.90) and not duration_far:
        return "medium", "artist and release metadata approximate match"
    if title_only or title_ratio >= 0.90:
        return "low", "title-only or weak metadata match"
    return "low", "metadata match too weak"


def safe_debug_url(url: str) -> str:
    return re.sub(r"([?&][^=]*(?:key|token|secret)[^=]*=)[^&]+", r"\1<masked>", url, flags=re.IGNORECASE)
