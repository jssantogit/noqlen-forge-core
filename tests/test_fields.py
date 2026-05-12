from noqlen_forge.fields import (
    FieldCategory,
    FieldScope,
    fields_by_category,
    fields_by_scope,
    get_field,
    get_missing_group,
    get_queryable_fields,
    get_syncable_fields,
    is_asset_field,
    is_audio_field,
    is_protected_field,
    is_writable_field,
    list_fields,
    resolve_field_alias,
)


def test_registry_contains_core_fields() -> None:
    names = {field.name for field in list_fields()}

    assert {"lyrics", "cover", "key", "replaygain", "mb_album_id", "style", "mood", "label", "originaldate"}.issubset(names)


def test_aliases_resolve_to_canonical_fields() -> None:
    assert resolve_field_alias("rg") == ("replaygain",)
    assert resolve_field_alias("art") == ("cover",)
    assert resolve_field_alias("mbids") == ("mb_album_id", "mb_track_id", "mb_release_group_id")
    assert resolve_field_alias("album_artist") == ("albumartist",)


def test_field_traits_are_centralized() -> None:
    assert is_protected_field("mb_album_id")
    assert not is_protected_field("genre")
    assert is_writable_field("genre")
    assert not is_writable_field("cover")
    assert is_asset_field("lyrics")
    assert is_audio_field("replaygain")


def test_missing_groups_cover_composites() -> None:
    assert get_missing_group("replaygain_track_gain") == "replaygain"
    assert get_missing_group("replaygain") == "replaygain"


def test_registry_filters_by_category_scope_and_capability() -> None:
    assert any(field.name == "key" for field in fields_by_category(FieldCategory.AUDIO))
    assert any(field.name == "path" for field in fields_by_scope(FieldScope.FILE))
    assert any(field.name == "mb_album_id" for field in get_queryable_fields())
    assert any(field.name == "title" for field in get_syncable_fields())


def test_unknown_field_is_not_registered() -> None:
    assert get_field("does_not_exist") is None
