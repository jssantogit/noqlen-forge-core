from pathlib import Path

from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp4 import MP4Cover

from noqlen_forge.audit import AuditResult, render_audit
from noqlen_forge.audio import Track
from noqlen_forge.cover import (
    _write_flac_cover,
    _write_mp4_cover,
    cover_path,
    detect_embedded_cover,
    detect_local_cover,
    fetch_cover_from_musicbrainz,
    process_cover,
    validate_image_bytes,
    write_cover,
)

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_detects_missing_cover(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    assert detect_embedded_cover(path) is False
    assert detect_local_cover(tmp_path) is None


def test_detects_local_cover_jpg(tmp_path) -> None:
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(JPEG)

    assert detect_local_cover(tmp_path) == cover


def test_detects_embedded_mp3_cover(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=JPEG))
    tags.save(path)

    assert detect_embedded_cover(path) is True


def test_dry_run_does_not_write_embedded_or_folder_cover(tmp_path, monkeypatch) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    track = Track(path=path, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["release"]})
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (validate_image_bytes(JPEG), []))

    result = process_cover(path, tracks=[track], apply=False)

    assert result.written == 0
    assert not (tmp_path / "cover.jpg").exists()
    assert detect_embedded_cover(path) is False


def test_apply_embeds_cover_without_saving_folder_cover_by_default(tmp_path, monkeypatch) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    track = Track(path=path, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["release"]})
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (validate_image_bytes(JPEG), []))

    result = process_cover(path, tracks=[track], apply=True)

    assert result.written == 1
    assert result.saved_path is None
    assert not (tmp_path / "cover.jpg").exists()
    assert detect_embedded_cover(path) is True


def test_save_folder_cover_flag_writes_cover_jpg(tmp_path, monkeypatch) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    track = Track(path=path, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["release"]})
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (validate_image_bytes(JPEG), []))

    result = process_cover(path, tracks=[track], apply=True, save_folder_cover=True)

    assert result.saved_path == tmp_path / "cover.jpg"
    assert (tmp_path / "cover.jpg").read_bytes() == JPEG
    assert detect_embedded_cover(path) is True


def test_apply_writes_embedded_mp3_cover(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    write_cover(path, JPEG, "image/jpeg")

    assert detect_embedded_cover(path) is True


class FakeMP4:
    def __init__(self) -> None:
        self.tags = {}

    def add_tags(self) -> None:
        self.tags = {}


def test_mp4_cover_helper_writes_covr() -> None:
    audio = FakeMP4()

    _write_mp4_cover(audio, PNG, "image/png")

    assert audio.tags["covr"] == [MP4Cover(PNG, imageformat=MP4Cover.FORMAT_PNG)]


class FakeFLAC:
    def __init__(self) -> None:
        self.pictures = [Picture()]

    def clear_pictures(self) -> None:
        self.pictures = []

    def add_picture(self, picture: Picture) -> None:
        self.pictures.append(picture)


def test_flac_cover_helper_writes_picture() -> None:
    audio = FakeFLAC()

    _write_flac_cover(audio, JPEG, "image/jpeg")

    assert len(audio.pictures) == 1
    assert audio.pictures[0].mime == "image/jpeg"
    assert audio.pictures[0].data == JPEG


def test_does_not_overwrite_without_force(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=JPEG))
    tags.save(path)

    write_cover(path, PNG, "image/png", force=False)

    assert ID3(path).getall("APIC")[0].data == JPEG


def test_overwrites_with_force(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=JPEG))
    tags.save(path)

    write_cover(path, PNG, "image/png", force=True)

    frame = ID3(path).getall("APIC")[0]
    assert frame.data == PNG
    assert frame.mime == "image/png"


def test_existing_embedded_cover_skips_without_fetch(tmp_path, monkeypatch) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=JPEG))
    tags.save(path)

    def fail_fetch(*args, **kwargs):
        raise AssertionError("fetch should not run")

    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", fail_fetch)

    code, output = cover_path(path)

    assert code == 0
    assert "Apply cover               SKIP" in output
    assert "embedded cover already present 1/1" in output
    assert "Status: OK" in output


def test_cover_standard_output_does_not_print_audit_sections(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=JPEG))
    tags.save(path)

    code, output = cover_path(path)

    assert code == 0
    assert "Required:" not in output
    assert "Enrichment:" not in output


def test_cover_standard_output_prints_cover_final_fields(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=JPEG))
    tags.save(path)

    code, output = cover_path(path)

    assert code == 0
    assert "Final:" in output
    assert "Cover: 1/1 embedded" in output
    assert "Folder Cover: skipped" in output
    assert "Status: OK" in output


def test_rejects_invalid_bytes() -> None:
    assert validate_image_bytes(b"not image") is None


def test_rejects_html_as_image() -> None:
    assert validate_image_bytes(b"<!doctype html><html>error</html>") is None


def test_uses_mb_album_id_to_fetch_cover(monkeypatch) -> None:
    calls = []

    def fake_get(url, accept):
        calls.append(url)
        if url.endswith("/release-id"):
            return b'{"images":[{"front":true,"image":"https://img.example/cover.jpg"}]}'
        return JPEG

    monkeypatch.setattr("noqlen_forge.cover._get_bytes", fake_get)

    image, _debug = fetch_cover_from_musicbrainz("release-id")

    assert image is not None
    assert calls[0].endswith("/release-id")
    assert calls[1] == "https://img.example/cover.jpg"


def test_audit_counts_cover_and_local_cover(tmp_path) -> None:
    track = Track(path=tmp_path / "song.mp3", format="mp3", tags={"cover": ["1"]})
    (tmp_path / "cover.jpg").write_bytes(JPEG)

    output = render_audit(AuditResult(tracks=[track], bad_fields=[]))

    assert "Cover: 1/1" in output
    assert "Folder Cover: found" in output


def test_audit_skips_missing_folder_cover_by_default(tmp_path) -> None:
    track = Track(path=tmp_path / "song.mp3", format="mp3", tags={"cover": ["1"]})

    output = render_audit(AuditResult(tracks=[track], bad_fields=[]))

    assert "Folder Cover: skipped" in output
    assert "Folder Cover missing" not in output


def test_cover_missing_mbid_warning_is_contextual_without_full_audit(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    code, output = cover_path(path)

    assert code == 0
    assert "Warnings:" in output
    assert "- MusicBrainz Album Id missing: cannot fetch cover from Cover Art Archive" in output
    assert "Required:" not in output
    assert "Enrichment:" not in output
    assert "MB Track Id" not in output
    assert "Release Group Id" not in output


def test_missing_cover_is_warn_not_review() -> None:
    track = Track(
        path=Path("song.mp3"),
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tags={
            "mb_album_id": ["album"],
            "mb_track_id": ["track"],
            "mb_release_group_id": ["group"],
            "label": ["Label"],
            "style": ["Pop"],
            "originaldate": ["2024"],
            "bpm": ["120"],
            "key": ["C Major"],
            "energy": ["80"],
            "danceability": ["70"],
            "lastfm_tags": ["Pop"],
            "mood": ["Happy"],
        },
    )

    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "WARN"


def test_standard_output_avoids_full_paths(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    (tmp_path / "cover.jpg").write_bytes(JPEG)

    code, output = cover_path(path)

    assert code == 0
    assert str(tmp_path) not in output


def test_verbose_output_prints_details(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    (tmp_path / "cover.jpg").write_bytes(JPEG)

    code, output = cover_path(path, apply=True, save_folder_cover=True, verbose=True)

    assert code == 0
    assert "Image: image/jpeg" in output
    assert f"Saved: {tmp_path / 'cover.jpg'}" in output


def test_local_cover_can_be_used_as_embed_source_without_recreating(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    (tmp_path / "cover.jpg").write_bytes(JPEG)
    track = Track(path=path, format="mp3", album="Album", artist="Artist", title="Song")

    result = process_cover(path, tracks=[track], apply=True)

    assert result.written == 1
    assert result.saved_path is None
    assert (tmp_path / "cover.jpg").read_bytes() == JPEG
    assert detect_embedded_cover(path) is True


def test_remove_folder_cover_dry_run_does_not_remove(tmp_path) -> None:
    (tmp_path / "cover.jpg").write_bytes(JPEG)
    path = tmp_path / "song.mp3"
    ID3().save(path)
    track = Track(path=path, format="mp3", album="Album", artist="Artist", title="Song")

    result = process_cover(path, tracks=[track], apply=False, remove_folder_cover=True)

    assert result.remove_candidates == [tmp_path / "cover.jpg"]
    assert result.removed_paths == []
    assert (tmp_path / "cover.jpg").exists()


def test_remove_folder_cover_apply_removes_only_known_names(tmp_path) -> None:
    for name in ("cover.jpg", "folder.png", "front.jpeg", "back.jpg", "artist.png"):
        (tmp_path / name).write_bytes(JPEG)
    path = tmp_path / "song.mp3"
    ID3().save(path)
    track = Track(path=path, format="mp3", album="Album", artist="Artist", title="Song")

    result = process_cover(path, tracks=[track], apply=True, remove_folder_cover=True)

    assert sorted(path.name for path in result.removed_paths) == ["cover.jpg", "folder.png", "front.jpeg"]
    assert not (tmp_path / "cover.jpg").exists()
    assert not (tmp_path / "folder.png").exists()
    assert not (tmp_path / "front.jpeg").exists()
    assert (tmp_path / "back.jpg").exists()
    assert (tmp_path / "artist.png").exists()


def test_cover_command_does_not_create_nomedia(tmp_path, monkeypatch) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    track = Track(path=path, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["release"]})
    monkeypatch.setattr("noqlen_forge.cover.read_tracks", lambda path: [track])
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (validate_image_bytes(JPEG), []))

    code, output = cover_path(path, apply=True)

    assert code == 0
    assert "Cover: 1/1 embedded" in output
    assert "Status: OK" in output
    assert not (tmp_path / ".nomedia").exists()
