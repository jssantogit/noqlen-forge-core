from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldCategory(StrEnum):
    IDENTITY = "identity"
    CATALOG = "catalog"
    ENRICHMENT = "enrichment"
    AUDIO = "audio"
    ASSET = "asset"
    PROVIDER = "provider"
    FILESYSTEM = "filesystem"


class FieldScope(StrEnum):
    ALBUM = "album"
    TRACK = "track"
    FILE = "file"
    LIBRARY = "library"


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    name: str
    label: str
    aliases: tuple[str, ...] = ()
    category: FieldCategory = FieldCategory.ENRICHMENT
    scope: FieldScope = FieldScope.TRACK
    value_type: str = "text"
    required: bool = False
    optional: bool = True
    protected: bool = False
    writable: bool = True
    multi_value: bool = False
    db_table: str | None = None
    db_column: str | None = None
    tag_names: tuple[str, ...] = ()
    missing_group: str | None = None
    description: str = ""
    queryable: bool = True
    syncable: bool = True
    auditable: bool = True

    def normalize_value(self, value: Any) -> Any:
        if value is None:
            return None
        if self.value_type in {"integer", "float"}:
            return value
        if self.multi_value and isinstance(value, (list, tuple)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()


@dataclass(slots=True)
class FieldRegistry:
    fields: dict[str, FieldDefinition] = field(default_factory=dict)
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def register(self, definition: FieldDefinition) -> None:
        key = _clean(definition.name)
        self.fields[key] = definition
        for alias in definition.aliases:
            self.aliases[_clean(alias)] = (key,)

    def alias(self, alias: str, fields: str | tuple[str, ...] | list[str]) -> None:
        names = (fields,) if isinstance(fields, str) else tuple(fields)
        self.aliases[_clean(alias)] = tuple(_clean(name) for name in names)

    def resolve(self, name_or_alias: str) -> tuple[str, ...]:
        key = _clean(name_or_alias)
        if key in self.fields:
            return (key,)
        return self.aliases.get(key, (key,))

    def get(self, name_or_alias: str) -> FieldDefinition | None:
        names = self.resolve(name_or_alias)
        if len(names) != 1:
            return None
        return self.fields.get(names[0])

    def list(self) -> list[FieldDefinition]:
        return sorted(self.fields.values(), key=lambda item: (item.category.value, item.name))


def _clean(value: str) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _field(name: str, label: str, category: FieldCategory, scope: FieldScope, **kwargs: Any) -> FieldDefinition:
    return FieldDefinition(name=name, label=label, category=category, scope=scope, missing_group=kwargs.pop("missing_group", name), **kwargs)


def _build_registry() -> FieldRegistry:
    registry = FieldRegistry()
    identity = FieldCategory.IDENTITY
    catalog = FieldCategory.CATALOG
    enrichment = FieldCategory.ENRICHMENT
    audio = FieldCategory.AUDIO
    asset = FieldCategory.ASSET
    filesystem = FieldCategory.FILESYSTEM
    album = FieldScope.ALBUM
    track = FieldScope.TRACK
    file_scope = FieldScope.FILE

    for definition in [
        _field("album", "Album", identity, album, required=True, optional=False, db_table="albums", db_column="album", tag_names=("album", "Album")),
        _field("albumartist", "Album Artist", identity, album, aliases=("album_artist",), required=True, optional=False, db_table="albums", db_column="albumartist", tag_names=("albumartist", "Album Artist")),
        _field("artist", "Artist", identity, track, required=True, optional=False, db_table="tracks", db_column="artist", tag_names=("artist", "Artist")),
        _field("title", "Title", identity, track, required=True, optional=False, db_table="tracks", db_column="title", tag_names=("title", "Title")),
        _field("track", "Track Number", identity, track, value_type="integer", db_table="tracks", db_column="track", tag_names=("track", "Track Number")),
        _field("tracktotal", "Track Total", identity, track, value_type="integer", db_table="tracks", db_column="tracktotal", tag_names=("tracktotal", "Track Total")),
        _field("disc", "Disc Number", identity, track, value_type="integer", db_table="tracks", db_column="disc", tag_names=("disc", "Disc Number")),
        _field("disctotal", "Disc Total", identity, track, value_type="integer", db_table="tracks", db_column="disctotal", tag_names=("disctotal", "Disc Total")),
        _field("date", "Date", identity, album, aliases=("year",), db_table="albums", db_column="date", tag_names=("date", "Date")),
        _field("originaldate", "Original Date", identity, album, aliases=("original_year",), db_table="albums", db_column="originaldate", tag_names=("originaldate", "Original Date")),
        _field("mb_album_id", "MusicBrainz Album Id", identity, album, aliases=("mbid", "albumid"), required=True, optional=False, protected=True, db_table="albums", db_column="mb_album_id", tag_names=("musicbrainz album id", "MusicBrainz Album Id")),
        _field("mb_release_group_id", "MusicBrainz Release Group Id", identity, album, aliases=("releasegroup",), required=True, optional=False, protected=True, db_table="albums", db_column="mb_release_group_id", tag_names=("musicbrainz release group id", "MusicBrainz Release Group Id")),
        _field("mb_track_id", "MusicBrainz Track Id", identity, track, aliases=("trackid",), required=True, optional=False, protected=True, db_table="tracks", db_column="mb_track_id", tag_names=("musicbrainz track id", "MusicBrainz Track Id")),
        _field("mb_release_track_id", "MusicBrainz Release Track Id", identity, track, protected=True, db_table="tracks", db_column="mb_release_track_id", tag_names=("musicbrainz release track id", "MusicBrainz Release Track Id")),
        _field("acoustid_id", "AcoustID Id", identity, track, protected=True, db_table="tracks", db_column="acoustid_id", tag_names=("acoustid_id", "AcoustID Id")),
        _field("isrc", "ISRC", identity, track, protected=True, db_table="tracks", db_column="isrc", tag_names=("isrc", "ISRC")),
        _field("label", "Label", catalog, album, db_table="albums", db_column="label", tag_names=("label", "Label")),
        _field("catalog_number", "Catalog Number", catalog, album, db_table="albums", db_column="catalog_number", tag_names=("catalog_number", "Catalog Number")),
        _field("barcode", "Barcode", catalog, album, db_table="albums", db_column="barcode", tag_names=("barcode", "Barcode")),
        _field("country", "Release Country", catalog, album, db_table="albums", db_column="country", tag_names=("country", "Release Country")),
        _field("media", "Media", catalog, album, db_table="albums", db_column="release_format", tag_names=("media", "Media")),
        _field("release_format", "Release Format", catalog, album, db_table="albums", db_column="release_format", tag_names=("release_format", "Release Format"), syncable=False),
        _field("release_type", "Release Type", catalog, album, db_table="albums", db_column="release_type", tag_names=("release_type", "Release Type")),
        _field("edition", "Edition", catalog, album, db_table="albums", db_column="edition", tag_names=("edition", "Edition")),
        _field("genre", "Genre", enrichment, track, multi_value=True, db_table="track_tags", db_column="genre", tag_names=("genre", "Genre")),
        _field("style", "Style", enrichment, track, multi_value=True, db_table="track_tags", db_column="style", tag_names=("style", "Style")),
        _field("mood", "Mood", enrichment, track, db_table="tracks", db_column="mood", tag_names=("mood", "Mood")),
        _field("lastfm_tags", "Last.fm Tags", enrichment, track, multi_value=True, db_table="track_tags", db_column="lastfm_tags", tag_names=("lastfm_tags", "LASTFM_TAGS")),
        _field("bpm", "BPM", audio, track, value_type="float", db_table="audio_features", db_column="bpm", tag_names=("bpm", "BPM")),
        _field("key", "Key", audio, track, db_table="audio_features", db_column="key", tag_names=("key", "Key")),
        _field("energy", "Energy", audio, track, value_type="integer", db_table="audio_features", db_column="energy", tag_names=("energy", "Energy")),
        _field("danceability", "Danceability", audio, track, value_type="integer", db_table="audio_features", db_column="danceability", tag_names=("danceability", "Danceability")),
        _field("loudness", "Loudness", audio, track, value_type="float", db_table="audio_features", db_column="loudness", tag_names=("loudness", "Loudness")),
        _field("replaygain", "ReplayGain", audio, track, aliases=("rg",), value_type="composite", writable=False, syncable=False, db_table="audio_features", missing_group="replaygain"),
        _field("replaygain_track_gain", "ReplayGain Track Gain", audio, track, value_type="float", db_table="audio_features", db_column="replaygain_track_gain", tag_names=("replaygain_track_gain", "ReplayGain Track Gain"), missing_group="replaygain"),
        _field("replaygain_track_peak", "ReplayGain Track Peak", audio, track, value_type="float", db_table="audio_features", db_column="replaygain_track_peak", tag_names=("replaygain_track_peak", "ReplayGain Track Peak"), missing_group="replaygain"),
        _field("replaygain_album_gain", "ReplayGain Album Gain", audio, track, value_type="float", db_table="audio_features", db_column="replaygain_album_gain", tag_names=("replaygain_album_gain", "ReplayGain Album Gain"), missing_group="replaygain"),
        _field("replaygain_album_peak", "ReplayGain Album Peak", audio, track, value_type="float", db_table="audio_features", db_column="replaygain_album_peak", tag_names=("replaygain_album_peak", "ReplayGain Album Peak"), missing_group="replaygain"),
        _field("cover", "Cover", asset, file_scope, aliases=("art",), value_type="presence", writable=False, syncable=True, db_table="files", db_column="has_cover", tag_names=("cover", "Cover")),
        _field("lyrics", "Lyrics", asset, track, value_type="presence", writable=False, syncable=True, db_table="lyrics", tag_names=("lyrics", "Lyrics")),
        _field("synced_lyrics", "Synced Lyrics", asset, track, aliases=("lrc",), value_type="presence", writable=False, syncable=False, db_table="lyrics", tag_names=("synced_lyrics", "Synced Lyrics")),
        _field("sidecar_lrc", "Sidecar LRC", asset, file_scope, value_type="presence", writable=False, syncable=False, db_table="lyrics", db_column="sidecar_path"),
        _field("path", "Path", filesystem, file_scope, writable=False, syncable=False, db_table="files", db_column="path"),
        _field("format", "Format", filesystem, file_scope, writable=False, syncable=False, db_table="files", db_column="format"),
        _field("codec", "Codec", filesystem, file_scope, writable=False, syncable=False, db_table="files", db_column="codec"),
        _field("bitrate", "Bitrate", filesystem, file_scope, value_type="integer", writable=False, syncable=False, db_table="files", db_column="bitrate"),
        _field("sample_rate", "Sample Rate", filesystem, file_scope, value_type="integer", writable=False, syncable=False, db_table="files", db_column="sample_rate"),
        _field("duration", "Duration", filesystem, file_scope, value_type="float", writable=False, syncable=False, db_table="files", db_column="duration"),
    ]:
        registry.register(definition)

    registry.alias("mbids", ("mb_album_id", "mb_track_id", "mb_release_group_id"))
    registry.alias("lrc", ("synced_lyrics", "sidecar_lrc"))
    registry.alias("year", ("date", "originaldate"))
    return registry


REGISTRY = _build_registry()


def get_field(name_or_alias: str) -> FieldDefinition | None:
    return REGISTRY.get(name_or_alias)


def resolve_field_alias(name_or_alias: str) -> tuple[str, ...]:
    return REGISTRY.resolve(name_or_alias)


def list_fields() -> list[FieldDefinition]:
    return REGISTRY.list()


def fields_by_category(category: str | FieldCategory) -> list[FieldDefinition]:
    selected = FieldCategory(str(category).casefold())
    return [item for item in list_fields() if item.category == selected]


def fields_by_scope(scope: str | FieldScope) -> list[FieldDefinition]:
    selected = FieldScope(str(scope).casefold())
    return [item for item in list_fields() if item.scope == selected]


def is_protected_field(field: str | FieldDefinition) -> bool:
    definition = field if isinstance(field, FieldDefinition) else get_field(field)
    return bool(definition and definition.protected)


def is_writable_field(field: str | FieldDefinition) -> bool:
    definition = field if isinstance(field, FieldDefinition) else get_field(field)
    return bool(definition and definition.writable)


def is_asset_field(field: str | FieldDefinition) -> bool:
    definition = field if isinstance(field, FieldDefinition) else get_field(field)
    return bool(definition and definition.category == FieldCategory.ASSET)


def is_audio_field(field: str | FieldDefinition) -> bool:
    definition = field if isinstance(field, FieldDefinition) else get_field(field)
    return bool(definition and definition.category == FieldCategory.AUDIO)


def get_missing_group(field: str | FieldDefinition) -> str | None:
    definition = field if isinstance(field, FieldDefinition) else get_field(field)
    return definition.missing_group if definition else None


def get_queryable_fields() -> list[FieldDefinition]:
    return [item for item in list_fields() if item.queryable]


def get_syncable_fields() -> list[FieldDefinition]:
    return [item for item in list_fields() if item.syncable]


def get_auditable_fields() -> list[FieldDefinition]:
    return [item for item in list_fields() if item.auditable]


def supported_field_names() -> set[str]:
    return set(REGISTRY.fields)


def render_fields(category: str | None = None, scope: str | None = None) -> str:
    fields = list_fields()
    if category:
        fields = [item for item in fields if item.category == FieldCategory(category.casefold())]
    if scope:
        fields = [item for item in fields if item.scope == FieldScope(scope.casefold())]
    if not fields:
        return "Supported fields: none"
    lines = ["Supported fields:", ""]
    for field_category in FieldCategory:
        grouped = [item for item in fields if item.category == field_category]
        if not grouped:
            continue
        lines.append(f"{field_category.value.title()}:")
        lines.extend(f"- {item.name}" for item in grouped)
        lines.append("")
    return "\n".join(lines).rstrip()
