from pathlib import Path

from noqlen_forge.audio import Track
from noqlen_forge.replaygain import LoudnessAnalysis, integrated_album_lufs, parse_ebur128_summary, parse_loudnorm_json, replaygain_path


def _track(path: Path, tags: dict[str, list[str]] | None = None) -> Track:
    return Track(path=path, format=path.suffix.lower().lstrip("."), album="Album", albumartist="Artist", artist="Artist", title=path.stem, tracknumber=1, duration=10.0, tags=tags or {})


def test_parse_loudnorm_json() -> None:
    output = '{"input_i":"-15.40","input_tp":"-1.20","input_lra":"4.0"}'

    assert parse_loudnorm_json(output) == (-15.4, -1.2)


def test_parse_ebur128_summary() -> None:
    output = "Integrated loudness:\n    I:         -17.1 LUFS\nTrue peak:\n    Peak:      -0.4 dBFS"

    assert parse_ebur128_summary(output) == (-17.1, -0.4)


def test_album_lufs_is_duration_weighted() -> None:
    analyses = [LoudnessAnalysis(Path("a.flac"), -18.0, -1.0, 0.9, duration=10), LoudnessAnalysis(Path("b.flac"), -12.0, -1.0, 0.9, duration=10)]

    assert round(integrated_album_lufs(analyses), 1) == -14.0


def test_replaygain_dry_run_does_not_write(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.flac"
    calls = []
    monkeypatch.setattr("noqlen_forge.replaygain.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("noqlen_forge.replaygain.audio_files", lambda target: [path])
    monkeypatch.setattr("noqlen_forge.replaygain.read_track", lambda file_path: _track(file_path))
    monkeypatch.setattr("noqlen_forge.replaygain.analyze_loudness_ffmpeg", lambda file_path: (LoudnessAnalysis(file_path, -16.0, -1.0, 0.891, 10.0), ""))
    monkeypatch.setattr("noqlen_forge.replaygain.write_replaygain_tags", lambda file_path, changes: calls.append((file_path, changes)))

    code, output = replaygain_path(tmp_path, apply=False)

    assert code == 0
    assert "Mode: DRY-RUN" in output
    assert "would write ReplayGain 1/1" in output
    assert calls == []


def test_replaygain_apply_writes_track_and_album(monkeypatch, tmp_path) -> None:
    paths = [tmp_path / "a.flac", tmp_path / "b.flac"]
    calls = []
    monkeypatch.setattr("noqlen_forge.replaygain.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("noqlen_forge.replaygain.audio_files", lambda target: paths)
    monkeypatch.setattr("noqlen_forge.replaygain.read_track", lambda file_path: _track(file_path))
    monkeypatch.setattr("noqlen_forge.replaygain.analyze_loudness_ffmpeg", lambda file_path: (LoudnessAnalysis(file_path, -16.0, -1.0, 0.891, 10.0), ""))
    monkeypatch.setattr("noqlen_forge.replaygain.write_replaygain_tags", lambda file_path, changes: calls.append((file_path, changes)))

    code, output = replaygain_path(tmp_path, apply=True)

    assert code == 0
    assert "ReplayGain Album: 2/2" in output
    assert len(calls) == 2
    assert all(changes["REPLAYGAIN_TRACK_GAIN"] == "-2.00 dB" for _, changes in calls)
    assert all("REPLAYGAIN_ALBUM_GAIN" in changes for _, changes in calls)


def test_replaygain_skip_existing_does_not_analyze(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.flac"
    existing = {
        "replaygain_track_gain": ["+1.00 dB"],
        "replaygain_track_peak": ["0.900000"],
        "replaygain_album_gain": ["+1.00 dB"],
        "replaygain_album_peak": ["0.900000"],
        "loudness": ["-19.00 LUFS"],
    }
    monkeypatch.setattr("noqlen_forge.replaygain.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("noqlen_forge.replaygain.audio_files", lambda target: [path])
    monkeypatch.setattr("noqlen_forge.replaygain.read_track", lambda file_path: _track(file_path, tags=existing))

    def fail_analyze(file_path):
        raise AssertionError("should not analyze existing ReplayGain")

    monkeypatch.setattr("noqlen_forge.replaygain.analyze_loudness_ffmpeg", fail_analyze)

    code, output = replaygain_path(tmp_path, apply=True)

    assert code == 0
    assert "skipped 1" in output


def test_replaygain_force_rewrites_equal_values(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.flac"
    calls = []
    existing = {"replaygain_track_gain": ["-2.00 dB"], "replaygain_track_peak": ["0.891000"], "replaygain_album_gain": ["-2.00 dB"], "replaygain_album_peak": ["0.891000"], "loudness": ["-16.00 LUFS"]}
    monkeypatch.setattr("noqlen_forge.replaygain.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("noqlen_forge.replaygain.audio_files", lambda target: [path])
    monkeypatch.setattr("noqlen_forge.replaygain.read_track", lambda file_path: _track(file_path, tags=existing))
    monkeypatch.setattr("noqlen_forge.replaygain.analyze_loudness_ffmpeg", lambda file_path: (LoudnessAnalysis(file_path, -16.0, -1.0, 0.891, 10.0), ""))
    monkeypatch.setattr("noqlen_forge.replaygain.write_replaygain_tags", lambda file_path, changes: calls.append((file_path, changes)))

    code, _ = replaygain_path(tmp_path, apply=True, force=True)

    assert code == 0
    assert calls and "REPLAYGAIN_TRACK_GAIN" in calls[0][1]


def test_replaygain_does_not_rewrite_equal_values_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.flac"
    calls = []
    existing = {
        "replaygain_track_gain": ["-2.00 dB"],
        "replaygain_track_peak": ["0.891000"],
        "replaygain_album_gain": ["-2.00 dB"],
        "replaygain_album_peak": ["0.891000"],
        "loudness": ["-16.00 LUFS"],
    }
    monkeypatch.setattr("noqlen_forge.replaygain.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("noqlen_forge.replaygain.audio_files", lambda target: [path])
    monkeypatch.setattr("noqlen_forge.replaygain.read_track", lambda file_path: _track(file_path, tags=existing))
    monkeypatch.setattr("noqlen_forge.replaygain.analyze_loudness_ffmpeg", lambda file_path: (LoudnessAnalysis(file_path, -16.0, -1.0, 0.891, 10.0), ""))
    monkeypatch.setattr("noqlen_forge.replaygain.write_replaygain_tags", lambda file_path, changes: calls.append((file_path, changes)))

    code, output = replaygain_path(tmp_path, apply=True, skip_existing=False)

    assert code == 0
    assert "wrote ReplayGain 0/1" in output
    assert calls == []


def test_replaygain_ffmpeg_missing_is_skip(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("noqlen_forge.replaygain.audio_files", lambda target: [tmp_path / "song.flac"])
    monkeypatch.setattr("noqlen_forge.replaygain.shutil.which", lambda name: None)

    code, output = replaygain_path(tmp_path)

    assert code == 0
    assert "ffmpeg not found" in output
