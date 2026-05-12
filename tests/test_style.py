from pathlib import Path

from mutagen.id3 import ID3, TXXX

from noqlen_forge.audio import Track
from noqlen_forge.style import _write_mp4_style, set_style_path, write_style


class FakeMP4:
    def __init__(self):
        self.tags = {}

    def add_tags(self):
        self.tags = {}


def test_set_style_mp3_writes_txxx_style(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    write_style(path, "K-pop; Synth-pop")

    tags = ID3(path)
    assert tags.getall("TXXX:STYLE")[0].text == ["K-pop; Synth-pop"]


def test_set_style_m4a_writes_freeform_style() -> None:
    audio = FakeMP4()

    _write_mp4_style(audio, "K-pop; Synth-pop")

    assert bytes(audio.tags["----:com.apple.iTunes:STYLE"][0]) == b"K-pop; Synth-pop"


def test_set_style_vorbis_writes_style(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.flac"
    path.touch()
    saved = {"called": False}

    class FakeVorbis(dict):
        def save(self):
            saved["called"] = True

    audio = FakeVorbis()
    monkeypatch.setattr("noqlen_forge.style.MutagenFile", lambda *args, **kwargs: audio)

    write_style(path, "K-pop; Synth-pop")

    assert audio["STYLE"] == ["K-pop; Synth-pop"]
    assert saved["called"] is True


def test_set_style_normalizes_separators(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.style.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={}))

    code, output = set_style_path(path, "K-pop, Synth-pop", apply=False)

    assert code == 0
    assert "would write STYLE=K-pop; Synth-pop" in output


def test_set_style_does_not_overwrite_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.style.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"style": ["Existing"]}))

    code, output = set_style_path(path, "K-pop; Synth-pop", apply=False)

    assert code == 0
    assert "skipped: existing STYLE=Existing" in output


def test_set_style_overwrites_with_force(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(TXXX(encoding=3, desc="STYLE", text=["Existing"]))
    tags.save(path)

    code, output = set_style_path(path, "K-pop; Synth-pop", apply=True, force=True)
    tags = ID3(path)

    assert code == 0
    assert "wrote STYLE=K-pop; Synth-pop" in output
    assert tags.getall("TXXX:STYLE")[0].text == ["K-pop; Synth-pop"]
