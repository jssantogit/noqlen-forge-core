from mutagen.id3 import TXXX
from mutagen.mp4 import MP4FreeForm

from noqlen_forge.audio import _tag_values


def test_mp4_standard_atoms_map_to_logical_names() -> None:
    assert _tag_values("\xa9alb", ["Get Up"]) == [("album", "Get Up")]
    assert _tag_values("aART", ["NewJeans"]) == [("albumartist", "NewJeans")]
    assert _tag_values("\xa9nam", ["Super Shy"]) == [("title", "Super Shy")]
    assert _tag_values("\xa9ART", ["NewJeans"]) == [("artist", "NewJeans")]
    assert _tag_values("\xa9gen", ["K-Pop"]) == [("genre", "K-Pop")]


def test_mp4_track_number_tuple_maps_to_tracknumber() -> None:
    assert _tag_values("trkn", [(2, 6)]) == [("tracknumber", "2")]


def test_mp4_freeform_label_style_originaldate_map_to_logical_names() -> None:
    assert _tag_values("----:com.apple.iTunes:LABEL", [MP4FreeForm(b"ADOR")]) == [("label", "ADOR")]
    assert _tag_values("----:com.apple.iTunes:STYLE", [MP4FreeForm(b"K-Pop")]) == [("style", "K-Pop")]
    assert _tag_values("----:com.apple.iTunes:ORIGINALDATE", [MP4FreeForm(b"2023")]) == [("originaldate", "2023")]


def test_mp3_txxx_label_style_originaldate_map_to_logical_names() -> None:
    assert _tag_values("TXXX:LABEL", TXXX(encoding=3, desc="LABEL", text=["ADOR"])) == [("label", "ADOR")]
    assert _tag_values("TXXX:STYLE", TXXX(encoding=3, desc="STYLE", text=["K-Pop"])) == [("style", "K-Pop")]
    assert _tag_values("TXXX:ORIGINALDATE", TXXX(encoding=3, desc="ORIGINALDATE", text=["2023"])) == [("originaldate", "2023")]


def test_replaygain_tags_map_to_logical_names() -> None:
    assert _tag_values("REPLAYGAIN_TRACK_GAIN", ["-2.00 dB"]) == [("replaygain_track_gain", "-2.00 dB")]
    assert _tag_values("TXXX:REPLAYGAIN_ALBUM_PEAK", TXXX(encoding=3, desc="REPLAYGAIN_ALBUM_PEAK", text=["0.900000"])) == [("replaygain_album_peak", "0.900000")]
    assert _tag_values("----:com.apple.iTunes:LOUDNESS", [MP4FreeForm(b"-16.00 LUFS")]) == [("loudness", "-16.00 LUFS")]
