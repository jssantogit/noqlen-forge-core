from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

TAG_SEPARATOR = "; "


@dataclass(slots=True)
class TagContext:
    artist: str = ""
    albumartist: str = ""
    album: str = ""
    title: str = ""
    source: str = ""


@dataclass(slots=True)
class TagDecision:
    original: str
    normalized: str
    keep: bool
    reason: str
    category: str


@dataclass(slots=True)
class FilteredTags:
    kept: list[str] = field(default_factory=list)
    removed: list[TagDecision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


TAG_NORMALIZATIONS = {
    "kpop": "K-pop",
    "k-pop": "K-pop",
    "rnb": "R&B",
    "r&b": "R&B",
    "krnb": "K-R&B",
    "k-rnb": "K-R&B",
    "k-r&b": "K-R&B",
    "korean rnb": "Korean R&B",
    "korean r&b": "Korean R&B",
    "contemporary rnb": "Contemporary R&B",
    "contemporary r&b": "Contemporary R&B",
    "alternative rnb": "Alternative R&B",
    "alternative r&b": "Alternative R&B",
    "uk garage": "UK Garage",
    "2 step": "2-step",
    "2-step garage": "2-step",
    "drum and bass": "Drum n Bass",
    "dnb": "Drum n Bass",
    "drum n bass": "Drum n Bass",
    "jersey club": "Jersey Club",
    "baltimore club": "Baltimore Club",
    "funk carioca": "Funk Carioca",
    "dance pop": "Dance-pop",
    "dance-pop": "Dance-pop",
    "synth pop": "Synth-pop",
    "synth-pop": "Synth-pop",
    "future bass": "Future Bass",
    "future house": "Future House",
    "bedroom pop": "Bedroom Pop",
    "hip hop soul": "Hip Hop Soul",
    "neo soul": "Neo-Soul",
    "neo-soul": "Neo-Soul",
    "technical death metal": "Technical Death Metal",
    "progressive death metal": "Progressive Death Metal",
    "neoclassical metal": "Neoclassical Metal",
    "brutal death metal": "Brutal Death Metal",
    "melodic death metal": "Melodic Death Metal",
    "progressive metal": "Progressive Metal",
    "progressive rock": "Progressive Rock",
    "girl group": "Girl Group",
    "girl groups": "Girl Group",
}

GENRE_STYLE_TAGS = {
    "K-pop",
    "Pop",
    "Dance-pop",
    "Synth-pop",
    "R&B",
    "K-R&B",
    "Korean R&B",
    "Contemporary R&B",
    "Alternative R&B",
    "UK Garage",
    "2-step",
    "Jersey Club",
    "Baltimore Club",
    "Funk Carioca",
    "Drum n Bass",
    "Footwork",
    "Future Garage",
    "Future Bass",
    "Future House",
    "Outsider House",
    "Bedroom Pop",
    "Hip Hop Soul",
    "Neo-Soul",
    "Technical Death Metal",
    "Death Metal",
    "Progressive Death Metal",
    "Progressive Metal",
    "Neoclassical Metal",
    "Metal",
    "Brutal Death Metal",
    "Melodic Death Metal",
    "Jazz Fusion",
    "Progressive Rock",
    "Girl Group",
}

MOOD_TAGS = {
    "Melancholic",
    "Energetic",
    "Aggressive",
    "Dreamy",
    "Chill",
    "Atmospheric",
    "Dark",
    "Happy",
    "Sad",
    "Intense",
    "Relaxed",
    "Upbeat",
}

ORIGIN_TAGS = {"Korean"}
ALLOWLIST_TAGS = GENRE_STYLE_TAGS | MOOD_TAGS | ORIGIN_TAGS
ALLOWLIST_LOWER = {tag.lower(): tag for tag in ALLOWLIST_TAGS}

PERSONAL_TAGS = {"fav", "favs", "favorite", "favorites", "favourite", "loved", "my top tracks", "top tracks", "playlist", "personal", "mine", "my music"}
PLATFORM_TAGS = {"spotify", "youtube", "local files", "apple music", "itunes"}
META_TAGS = {"seen live", "under 2000 listeners", "under 1000 listeners", "scrobbles", "lastfm", "last.fm"}
GENERIC_TAGS = {"song", "songs", "track", "tracks", "music", "hit", "hits", "single", "album", "ep", "vocal", "vocals", "male vocal", "female vocal", "female vocals", "male vocals"}
NOISY_PHRASES = {"one time flamengo", "maris song", "you don't even know my name do ya"}
RELATED_ARTIST_BLOCKLIST = {"aespa", "ive", "blackpink", "twice", "bts", "le sserafim", "newjeans", "rescene"}


def normalize_tag(raw: str) -> str:
    clean = re.sub(r"\s+", " ", str(raw).strip())
    clean = clean.strip(" \t\r\n\0")
    lowered = clean.lower()
    if lowered in TAG_NORMALIZATIONS:
        return TAG_NORMALIZATIONS[lowered]
    if lowered in ALLOWLIST_LOWER:
        return ALLOWLIST_LOWER[lowered]
    return clean


def classify_tag(tag: str, context: TagContext) -> TagDecision:
    original = re.sub(r"\s+", " ", str(tag).strip())
    normalized = normalize_tag(original)
    lowered = normalized.lower()
    raw_lower = original.lower()
    context_names = {value.strip().lower() for value in (context.artist, context.albumartist) if value.strip()}
    if not normalized:
        return TagDecision(original, normalized, False, "empty", "empty")
    if lowered in {name for name in context_names}:
        return TagDecision(original, normalized, False, "same-as-artist", "artist")
    if raw_lower in RELATED_ARTIST_BLOCKLIST or lowered in RELATED_ARTIST_BLOCKLIST:
        return TagDecision(original, normalized, False, "related-artist-blocklist", "artist")
    if re.fullmatch(r"19\d\d|20[0-2]\d|203[0-5]", raw_lower):
        return TagDecision(original, normalized, False, "year", "date")
    if re.fullmatch(r"(?:\d{2}|\d{4})s", raw_lower):
        return TagDecision(original, normalized, False, "decade", "date")
    if raw_lower in PERSONAL_TAGS:
        return TagDecision(original, normalized, False, "personal", "personal")
    if raw_lower in PLATFORM_TAGS:
        return TagDecision(original, normalized, False, "platform", "platform")
    if raw_lower in META_TAGS:
        return TagDecision(original, normalized, False, "lastfm-meta", "meta")
    if raw_lower in GENERIC_TAGS or lowered in GENERIC_TAGS:
        return TagDecision(original, normalized, False, "too-generic", "generic")
    if raw_lower in NOISY_PHRASES:
        return TagDecision(original, normalized, False, "long/noisy phrase", "phrase")
    if len(original.split()) > 4 and lowered not in ALLOWLIST_LOWER:
        return TagDecision(original, normalized, False, "long/noisy phrase", "phrase")
    if _looks_like_personal_phrase(original) and lowered not in ALLOWLIST_LOWER:
        return TagDecision(original, normalized, False, "personal/noisy phrase", "phrase")
    category = _tag_category(normalized)
    return TagDecision(original, normalized, True, "kept", category)


def filter_tags(raw_tags: Iterable[Any], context: TagContext, min_count: int, max_tags: int, raw_mode: bool = False) -> FilteredTags:
    by_lower: dict[str, tuple[str, int, int, str]] = {}
    removed: list[TagDecision] = []
    for index, item in enumerate(raw_tags):
        raw_name, count = _raw_name_and_count(item)
        decision = classify_tag(raw_name, context)
        if not decision.normalized:
            continue
        if raw_mode:
            decision = TagDecision(decision.original, decision.normalized, True, "raw", _tag_category(decision.normalized))
        if not decision.keep:
            removed.append(decision)
            continue
        lowered = decision.normalized.lower()
        if count < min_count and lowered not in ALLOWLIST_LOWER:
            removed.append(TagDecision(decision.original, decision.normalized, False, f"count {count} below {min_count}", "count"))
            continue
        current = by_lower.get(lowered)
        if current is None or count > current[1]:
            by_lower[lowered] = (decision.normalized, count, index, decision.category)
    ordered = sorted(by_lower.values(), key=lambda item: (_sort_rank(item[0], item[3]), -item[1], item[2], item[0].lower()))
    return FilteredTags(kept=[name for name, _count, _index, _category in ordered[:max_tags]], removed=removed)


def clean_existing_lastfm_tags(value, context: TagContext) -> str:
    parts = _split_existing(value)
    filtered = filter_tags([{"name": part, "count": 9999} for part in parts], context=context, min_count=0, max_tags=999)
    return TAG_SEPARATOR.join(filtered.kept)


def _raw_name_and_count(item: Any) -> tuple[str, int]:
    if isinstance(item, dict):
        return str(item.get("name", "")), _tag_count(item.get("count", 0))
    return str(item), 0


def _split_existing(value) -> list[str]:
    values = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for item in values:
        for part in str(item).replace(",", ";").split(";"):
            clean = part.strip()
            if clean:
                parts.append(clean)
    return parts


def _tag_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _looks_like_personal_phrase(value: str) -> bool:
    lowered = value.lower()
    if any(token in lowered for token in (" don't ", " do ya", " my ", "!!!", "???")):
        return True
    return bool(re.search(r"[!?]", value))


def _tag_category(tag: str) -> str:
    if tag in MOOD_TAGS:
        return "mood"
    if tag in ORIGIN_TAGS:
        return "origin"
    if tag in GENRE_STYLE_TAGS:
        return "genre"
    return "other"


def _sort_rank(tag: str, category: str) -> tuple[int, int]:
    if category == "genre":
        return (0 if tag in _STRONG_GENRES else 1, _GENRE_ORDER.get(tag, 999))
    if category == "mood":
        return (2, 0)
    if category == "origin":
        return (3, 0)
    return (4, 0)


_STRONG_GENRES = {"K-pop", "Pop", "R&B", "Metal", "Death Metal", "Progressive Rock"}
_GENRE_ORDER = {tag: index for index, tag in enumerate([
    "K-pop",
    "Pop",
    "R&B",
    "Metal",
    "Death Metal",
    "Progressive Rock",
    "Dance-pop",
    "Synth-pop",
    "K-R&B",
    "Korean R&B",
    "Contemporary R&B",
    "Alternative R&B",
    "UK Garage",
    "2-step",
    "Jersey Club",
    "Baltimore Club",
    "Funk Carioca",
    "Drum n Bass",
    "Footwork",
    "Future Garage",
    "Future Bass",
    "Future House",
    "Outsider House",
    "Bedroom Pop",
    "Hip Hop Soul",
    "Neo-Soul",
    "Technical Death Metal",
    "Progressive Death Metal",
    "Progressive Metal",
    "Neoclassical Metal",
    "Brutal Death Metal",
    "Melodic Death Metal",
    "Jazz Fusion",
    "Girl Group",
])}
