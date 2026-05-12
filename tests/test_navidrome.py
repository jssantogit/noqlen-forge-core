import csv
import json

from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect, upsert_album, upsert_file, upsert_track
from noqlen_forge.navidrome import (
    NavidromeClient,
    NavidromeConfig,
    NavidromeError,
    RatingItem,
    build_auth_params,
    build_player_track_identity,
    normalize_song_payload,
    normalize_playlist_payload,
    playlists_backup,
    playlists_diff,
    playlists_export,
    playlists_list,
    playlists_push,
    playlists_push_smart,
    playlists_status,
    ratings_backup,
    ratings_diff,
    ratings_export,
    ratings_restore,
    ratings_status,
)


class FakeClient:
    def __init__(self, config: NavidromeConfig, items: list[RatingItem], ping_error: str = "", playlists=None):
        self.config = config
        self.items = items
        self.ping_error = ping_error
        self.calls = 0
        self.write_calls = []
        self.playlists = playlists or []
        self.playlist_entries = {playlist["id"]: list(playlist.get("song_ids", [])) for playlist in self.playlists}

    def ping(self):
        self.calls += 1
        if self.ping_error:
            raise NavidromeError(self.ping_error)
        return {"subsonic-response": {"status": "ok"}}

    def iter_rating_items(self):
        self.calls += 1
        return self.items

    def set_rating(self, song_id, rating):
        self.write_calls.append(("setRating", song_id, rating))
        for item in self.items:
            if item.navidrome_id == song_id:
                item.rating = rating
        return {"subsonic-response": {"status": "ok"}}

    def star(self, song_id):
        self.write_calls.append(("star", song_id))
        for item in self.items:
            if item.navidrome_id == song_id:
                item.starred = True
        return {"subsonic-response": {"status": "ok"}}

    def unstar(self, song_id):
        self.write_calls.append(("unstar", song_id))
        for item in self.items:
            if item.navidrome_id == song_id:
                item.starred = False
        return {"subsonic-response": {"status": "ok"}}

    def get_playlists(self):
        self.calls += 1
        return {"subsonic-response": {"status": "ok", "playlists": {"playlist": [{"id": item["id"], "name": item["name"], "songCount": len(self.playlist_entries.get(item["id"], [])), "owner": item.get("owner", "tester")} for item in self.playlists]}}}

    def get_playlist(self, playlist_id):
        self.calls += 1
        entries = []
        for song_id in self.playlist_entries.get(playlist_id, []):
            entry = {"id": song_id}
            for item in self.items:
                if item.navidrome_id == song_id:
                    entry.update({"title": item.title, "artist": item.artist, "album": item.album, "albumArtist": item.albumartist, "duration": item.duration, "track": item.track, "musicBrainzTrackId": item.mb_track_id, "musicBrainzReleaseTrackId": item.mb_release_track_id, "acoustId": item.acoustid_id, "isrc": item.isrc, "path": item.path})
            entries.append(entry)
        playlist = next((item for item in self.playlists if item["id"] == playlist_id), {"id": playlist_id, "name": playlist_id})
        return {"subsonic-response": {"status": "ok", "playlist": {"id": playlist_id, "name": playlist.get("name", playlist_id), "owner": playlist.get("owner", "tester"), "entry": entries}}}

    def search3(self, query, *, song_count=20):
        self.calls += 1
        terms = query.casefold().split()
        songs = []
        for item in self.items:
            haystack = f"{item.artist} {item.title} {item.album}".casefold()
            if all(term in haystack for term in terms):
                songs.append({"id": item.navidrome_id, "title": item.title, "artist": item.artist, "album": item.album, "albumArtist": item.albumartist, "duration": item.duration, "track": item.track, "musicBrainzTrackId": item.mb_track_id, "musicBrainzReleaseTrackId": item.mb_release_track_id, "acoustId": item.acoustid_id, "isrc": item.isrc, "path": item.path})
        return {"subsonic-response": {"status": "ok", "searchResult3": {"song": songs[:song_count]}}}

    def get_song(self, song_id):
        self.calls += 1
        for item in self.items:
            if item.navidrome_id == song_id:
                return {"subsonic-response": {"status": "ok", "song": {"id": item.navidrome_id, "title": item.title, "artist": item.artist, "album": item.album, "albumArtist": item.albumartist, "duration": item.duration, "track": item.track, "musicBrainzTrackId": item.mb_track_id, "musicBrainzReleaseTrackId": item.mb_release_track_id, "acoustId": item.acoustid_id, "isrc": item.isrc, "path": item.path}}}
        raise NavidromeError("missing song")

    def create_playlist(self, name, song_ids):
        self.write_calls.append(("createPlaylist", name, list(song_ids)))
        playlist_id = f"pl-{len(self.playlists) + 1}"
        self.playlists.append({"id": playlist_id, "name": name})
        self.playlist_entries[playlist_id] = list(song_ids)
        return {"subsonic-response": {"status": "ok"}}

    def update_playlist(self, playlist_id, song_ids, *, name=None):
        self.write_calls.append(("updatePlaylist", playlist_id, list(song_ids), name))
        self.playlist_entries[playlist_id] = list(song_ids)
        return {"subsonic-response": {"status": "ok"}}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FallbackClient(NavidromeClient):
    def __init__(self):
        super().__init__(_nd_config())

    def get_starred2(self):
        raise NavidromeError("missing endpoint")

    def get_starred(self):
        return {"subsonic-response": {"status": "ok", "starred": {"song": [{"id": "n1", "title": "Song", "starred": "2024-01-01T00:00:00Z"}]}}}

    def get_song(self, song_id):
        return {"subsonic-response": {"status": "ok", "song": {"id": song_id, "userRating": 5}}}


def _config(tmp_path):
    config = default_config()
    config["database"]["path"] = str(tmp_path / "library.db")
    config["navidrome"].update({"base_url": "http://127.0.0.1:4533/", "username": "joao", "password": "secret-password"})
    return config


def _nd_config():
    return NavidromeConfig(base_url="http://127.0.0.1:4533", username="joao", password="secret-password")


def _seed_track(config, tmp_path, *, mb_track_id="mb-track-1", artist="Artist", title="Song", duration=123.0, filename="song.flac"):
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album": "Album", "albumartist": artist})
        track_id = upsert_track(conn, {"title": title, "artist": artist, "albumartist": artist, "mb_track_id": mb_track_id}, album_id=album_id)
        file_id = upsert_file(conn, tmp_path / filename, {"duration": duration, "format": "flac"}, track_id=track_id)
        conn.commit()
    return track_id, file_id


def test_build_auth_params_password_auth():
    params = build_auth_params(_nd_config())

    assert params["u"] == "joao"
    assert params["p"] == "secret-password"
    assert params["f"] == "json"
    assert "t" not in params


def test_build_auth_params_token_auth():
    config = NavidromeConfig(base_url="http://n", username="u", token="tok", salt="salt", auth="token")

    params = build_auth_params(config)

    assert params["t"] == "tok"
    assert params["s"] == "salt"
    assert "p" not in params


def test_base_url_normalizes_trailing_slash(tmp_path):
    config = _config(tmp_path)

    assert NavidromeConfig.from_config(config).base_url == "http://127.0.0.1:4533"


def test_ping_ok(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse({"subsonic-response": {"status": "ok"}}))

    assert NavidromeClient(_nd_config()).ping()["subsonic-response"]["status"] == "ok"


def test_ping_auth_error(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse({"subsonic-response": {"status": "failed", "error": {"message": "Wrong username or password"}}}))

    try:
        NavidromeClient(_nd_config()).ping()
    except NavidromeError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("expected auth failure")


def test_get_starred2_falls_back_to_get_starred():
    items = FallbackClient().iter_rating_items()

    assert len(items) == 1
    assert items[0].navidrome_id == "n1"
    assert items[0].starred is True
    assert items[0].rating == 5


def test_normalize_song_payload_tolerates_missing_fields():
    item = normalize_song_payload({"id": "1", "title": "Song"})

    assert item.navidrome_id == "1"
    assert item.title == "Song"
    assert item.rating is None


def test_identity_uses_mbid_first():
    key, method, confidence = build_player_track_identity(RatingItem(navidrome_id="n1", mb_track_id="MB", artist="A", title="T", duration=1))

    assert key == "mb_track:mb"
    assert method == "mb_track_id"
    assert confidence == "high"


def test_identity_uses_artist_title_duration_fallback():
    key, method, confidence = build_player_track_identity(RatingItem(navidrome_id="n1", artist="Artist", title="Song", duration=123.4))

    assert key == "artist_title_duration:artist:song:123"
    assert method == "artist_title_duration"
    assert confidence == "medium"


def test_identity_navidrome_id_alone_is_low_confidence():
    key, method, confidence = build_player_track_identity(RatingItem(navidrome_id="n1"))

    assert key == "navidrome:n1"
    assert method == "navidrome_id"
    assert confidence == "low"


def test_normalize_playlist_payload_tolerates_missing_entries():
    playlist = normalize_playlist_payload({"subsonic-response": {"playlist": {"id": "p1", "name": "Favorites"}}})

    assert playlist.navidrome_playlist_id == "p1"
    assert playlist.name == "Favorites"
    assert playlist.items == []


def test_backup_dry_run_does_not_write_db(tmp_path):
    config = _config(tmp_path)
    item = RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5, starred=True)

    code, output = ratings_backup(config, client=FakeClient(_nd_config(), [item]), apply=False)

    assert code == 0
    assert "Mode: DRY-RUN" in output
    with connect(config) as conn:
        apply_migrations(conn)
        count = conn.execute("SELECT COUNT(*) AS count FROM player_rating_backups").fetchone()["count"]
    assert count == 0


def test_backup_apply_saves_and_matches_by_mb_track_id(tmp_path):
    config = _config(tmp_path)
    track_id, file_id = _seed_track(config, tmp_path)
    item = RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5, starred=True)

    code, output = ratings_backup(config, client=FakeClient(_nd_config(), [item]), apply=True)

    assert code == 0
    assert "Status: OK" in output
    with connect(config) as conn:
        row = conn.execute("SELECT library_track_id, library_file_id, match_confidence, rating, starred FROM player_rating_backups").fetchone()
    assert row["library_track_id"] == track_id
    assert row["library_file_id"] == file_id
    assert row["match_confidence"] == "high"
    assert row["rating"] == 5
    assert row["starred"] == 1


def test_backup_apply_matches_artist_title_duration(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="", artist="Artist", title="Song", duration=123)
    item = RatingItem(navidrome_id="n1", title="Song", artist="Artist", duration=124, rating=4)

    ratings_backup(config, client=FakeClient(_nd_config(), [item]), apply=True)

    with connect(config) as conn:
        row = conn.execute("SELECT match_confidence, match_reason FROM player_rating_backups").fetchone()
    assert row["match_confidence"] == "medium"
    assert row["match_reason"] == "artist_title_duration"


def test_backup_apply_saves_unmatched_as_warn(tmp_path):
    config = _config(tmp_path)
    item = RatingItem(navidrome_id="n1", title="Lost", artist="Nobody", rating=3)

    code, output = ratings_backup(config, client=FakeClient(_nd_config(), [item]), apply=True)

    assert code == 0
    assert "Status: WARN" in output
    with connect(config) as conn:
        row = conn.execute("SELECT library_track_id, match_confidence FROM player_rating_backups").fetchone()
    assert row["library_track_id"] is None
    assert row["match_confidence"] == "none"


def test_backup_apply_is_idempotent(tmp_path):
    config = _config(tmp_path)
    item = RatingItem(navidrome_id="n1", title="Song", artist="Artist", rating=5)
    client = FakeClient(_nd_config(), [item])

    ratings_backup(config, client=client, apply=True)
    ratings_backup(config, client=client, apply=True)

    with connect(config) as conn:
        backups = conn.execute("SELECT COUNT(*) AS count FROM player_rating_backups").fetchone()["count"]
        runs = conn.execute("SELECT COUNT(*) AS count FROM player_rating_backup_runs").fetchone()["count"]
    assert backups == 1
    assert runs == 2


def test_playlists_list_uses_read_only_endpoint(tmp_path):
    config = _config(tmp_path)
    client = FakeClient(_nd_config(), [], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["n1"]}])

    code, output = playlists_list(config, client=client)

    assert code == 0
    assert "Favorites" in output
    assert client.write_calls == []


def test_playlist_push_dry_run_does_not_write(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")])

    code, output = playlists_push(config, "Artist", name="Favorites", client=client)

    assert code == 0
    assert "Mode: DRY-RUN" in output
    assert "would create playlist" in output
    assert client.write_calls == []


def test_playlist_push_apply_creates_new_playlist(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")])

    code, output = playlists_push(config, "Artist", name="Favorites", apply=True, client=client)

    assert code == 0
    assert "Mode: APPLY" in output
    assert ("createPlaylist", "Favorites", ["n1"]) in client.write_calls
    with connect(config) as conn:
        runs = conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_push_runs").fetchone()["count"]
    assert runs == 1


def test_playlist_existing_without_policy_is_review(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["old"]}])

    code, output = playlists_push(config, "Artist", name="Favorites", apply=True, client=client)

    assert code == 1
    assert "Status: REVIEW" in output
    assert client.write_calls == []


def test_playlist_replace_apply_updates_existing(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["old"]}])

    code, output = playlists_push(config, "Artist", name="Favorites", replace=True, apply=True, client=client)

    assert code == 0
    assert "Would remove: 1" in output
    assert ("updatePlaylist", "p1", ["n1"], "Favorites") in client.write_calls


def test_playlist_append_apply_keeps_existing_order(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["old"]}])

    code, _ = playlists_push(config, "Artist", name="Favorites", append=True, apply=True, client=client)

    assert code == 0
    assert ("updatePlaylist", "p1", ["old", "n1"], "Favorites") in client.write_calls


def test_playlist_unmatched_and_low_confidence_are_reported(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", path=str(tmp_path / "song.flac"))])

    code, output = playlists_push(config, "Artist", name="Favorites", apply=True, client=client)

    assert code == 0
    assert "Status: WARN" in output
    assert "Unmatched: 1" in output
    assert ("createPlaylist", "Favorites", []) in client.write_calls


def test_playlist_json_output_is_safe_and_parseable(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")])

    code, output = playlists_push(config, "Artist", name="Favorites", output_format="json", client=client)
    payload = json.loads(output)

    assert code == 0
    assert payload["mode"] == "DRY-RUN"
    assert payload["summary"]["matched"] == 1
    assert "secret-password" not in output
    assert "token" not in output.casefold()
    assert "salt" not in output.casefold()


def test_playlist_diff_is_read_only(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["old"]}])

    code, output = playlists_diff(config, "Artist", name="Favorites", client=client)

    assert code == 0
    assert "Mode: READ-ONLY" in output
    assert "Would add: 1" in output
    assert client.write_calls == []


def test_playlist_push_smart_uses_saved_query(tmp_path):
    from noqlen_forge.smart_playlists import smart_create

    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song")
    smart_create(config, "Favorites", "Artist", apply=True)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")])

    code, output = playlists_push_smart(config, "Favorites", apply=True, client=client)

    assert code == 0
    assert ("createPlaylist", "Favorites", ["n1"]) in client.write_calls
    assert "Status: OK" in output


def test_playlist_push_smart_missing_is_clear(tmp_path):
    config = _config(tmp_path)
    with connect(config) as conn:
        apply_migrations(conn)
        conn.commit()

    code, output = playlists_push_smart(config, "Missing", client=FakeClient(_nd_config(), []))

    assert code == 1
    assert "Smart playlist not found" in output


def test_playlist_backup_dry_run_does_not_write_db(tmp_path):
    config = _config(tmp_path)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["n1"]}])

    code, output = playlists_backup(config, client=client)

    assert code == 0
    assert "Mode: DRY-RUN" in output
    assert client.write_calls == []
    with connect(config) as conn:
        apply_migrations(conn)
        count = conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_backups").fetchone()["count"]
    assert count == 0


def test_playlist_backup_apply_saves_items_in_order_and_matches(tmp_path):
    config = _config(tmp_path)
    track_id, file_id = _seed_track(config, tmp_path, mb_track_id="mb-track-1", artist="Artist", title="Song", duration=123)
    _seed_track(config, tmp_path, mb_track_id="", artist="Artist", title="Fallback", duration=111, filename="fallback.flac")
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1"), RatingItem(navidrome_id="n2", title="Fallback", artist="Artist", duration=112), RatingItem(navidrome_id="lost", title="Lost", artist="Nobody")], playlists=[{"id": "p1", "name": "Favorites", "owner": "joao", "song_ids": ["n1", "n2", "lost"]}])

    code, output = playlists_backup(config, client=client, apply=True)

    assert code == 0
    assert "Status: WARN" in output
    assert client.write_calls == []
    with connect(config) as conn:
        rows = list(conn.execute("SELECT position, navidrome_song_id, library_track_id, library_file_id, match_confidence FROM navidrome_playlist_items ORDER BY position"))
    assert [row["navidrome_song_id"] for row in rows] == ["n1", "n2", "lost"]
    assert rows[0]["library_track_id"] == track_id
    assert rows[0]["library_file_id"] == file_id
    assert rows[0]["match_confidence"] == "high"
    assert rows[1]["match_confidence"] == "medium"
    assert rows[2]["match_confidence"] == "none"


def test_playlist_backup_apply_is_idempotent(tmp_path):
    config = _config(tmp_path)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["n1"]}])

    playlists_backup(config, client=client, apply=True)
    playlists_backup(config, client=client, apply=True)

    with connect(config) as conn:
        backups = conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_backups").fetchone()["count"]
        items = conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_items").fetchone()["count"]
        runs = conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_backup_runs").fetchone()["count"]
    assert backups == 1
    assert items == 1
    assert runs == 2


def test_playlist_backup_status_and_export(tmp_path):
    config = _config(tmp_path)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["n1"]}])
    playlists_backup(config, client=client, apply=True)
    json_path = tmp_path / "playlists.json"
    csv_path = tmp_path / "playlists.csv"

    status_code, status_output = playlists_status(config)
    json_code, _ = playlists_export(config, output_format="json", output=json_path)
    csv_code, _ = playlists_export(config, output_format="csv", output=csv_path)

    assert status_code == 0
    assert json_code == 0
    assert csv_code == 0
    assert "Playlists: 1" in status_output
    assert json.loads(json_path.read_text(encoding="utf-8"))["playlists"][0]["items"][0]["navidrome_song_id"] == "n1"
    assert list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))[0]["playlist_name"] == "Favorites"


def test_playlist_backup_filters_and_output_are_safe(tmp_path):
    config = _config(tmp_path)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist")], playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["n1"]}, {"id": "p2", "name": "Other", "song_ids": []}])

    code, output = playlists_backup(config, client=client, playlist_id="p1", output_format="json")
    payload = json.loads(output)

    assert code == 0
    assert payload["summary"]["total_playlists"] == 1
    assert payload["playlists"][0]["name"] == "Favorites"
    assert client.write_calls == []
    assert "secret-password" not in output
    assert "password" not in output.lower()
    assert "token" not in output.lower()
    assert "salt" not in output.lower()


def test_status_and_diff_show_saved_backup(tmp_path):
    config = _config(tmp_path)
    item = RatingItem(navidrome_id="n1", title="Lost", artist="Nobody", rating=3)
    ratings_backup(config, client=FakeClient(_nd_config(), [item]), apply=True)

    status_code, status_output = ratings_status(config)
    diff_code, diff_output = ratings_diff(config)

    assert status_code == 0
    assert diff_code == 0
    assert "Backups: 1" in status_output
    assert "Unmatched backup: 1" in diff_output


def test_diff_without_backup_returns_clear_message(tmp_path):
    config = _config(tmp_path)
    with connect(config) as conn:
        apply_migrations(conn)
        conn.commit()

    code, output = ratings_diff(config)

    assert code == 0
    assert "No backup runs found" in output


def test_diff_backup_only_does_not_call_api(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path)
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5)]), apply=True)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", rating=1)])

    code, output = ratings_diff(config, backup_only=True, server=True, client=client)

    assert code == 0
    assert "Fetch server      SKIP" in output
    assert client.calls == 0


def test_diff_server_detects_changed_new_and_missing(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1")
    ratings_backup(
        config,
        client=FakeClient(
            _nd_config(),
            [
                RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=4, starred=True),
                RatingItem(navidrome_id="n2", title="Gone", artist="Artist", rating=3),
            ],
        ),
        apply=True,
    )

    code, output = ratings_diff(config, server=True, output_format="json", client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5, starred=False), RatingItem(navidrome_id="n3", title="New", artist="Artist", rating=2, starred=True)]))

    payload = json.loads(output)
    assert code == 0
    assert payload["summary"]["changed_ratings"] == 1
    assert payload["summary"]["new_on_server"] == 1
    assert payload["summary"]["missing_on_server"] == 1
    assert {item["type"] for item in payload["items"]} >= {"changed_rating", "changed_starred", "new_on_server", "missing_on_server"}


def test_diff_detects_unmatched_library_without_rating_and_moved_path(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1")
    _seed_track(config, tmp_path, mb_track_id="mb-track-2", artist="Other", title="No Backup", duration=99, filename="other.flac")
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5, path=str(tmp_path / "old.flac")), RatingItem(navidrome_id="lost", title="Lost", artist="Nobody", rating=3)]), apply=True)
    with connect(config) as conn:
        conn.execute("UPDATE files SET path = ? WHERE path LIKE ?", (str(tmp_path / "moved.flac"), "%song.flac"))
        conn.commit()

    payload = json.loads(ratings_diff(config, backup_only=True, output_format="json")[1])

    assert payload["summary"]["unmatched_backup"] == 1
    assert payload["summary"]["library_without_rating"] == 1
    assert payload["summary"]["moved_paths_matched"] == 1


def test_diff_csv_output_has_expected_columns(tmp_path):
    config = _config(tmp_path)
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Lost", artist="Nobody", rating=3)]), apply=True)

    code, output = ratings_diff(config, output_format="csv")

    rows = list(csv.DictReader(output.splitlines()))
    assert code == 0
    assert rows[0]["diff_type"] == "unmatched_backup"
    assert set(rows[0]) >= {"diff_type", "title", "artist", "album", "identity_key", "identity_method", "match_confidence", "backup_rating", "server_rating", "backup_starred", "server_starred", "navidrome_id", "library_track_id", "path", "reason"}


def test_diff_output_file_and_secrets_are_safe(tmp_path):
    config = _config(tmp_path)
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", rating=5)]), apply=True)
    output_path = tmp_path / "diff.json"

    code, output = ratings_diff(config, server=True, output_format="json", output=output_path, client=FakeClient(_nd_config(), [], ping_error="password token salt failed"))

    assert code == 0
    assert output_path.exists()
    combined = output + output_path.read_text(encoding="utf-8")
    assert "secret-password" not in combined
    assert "password" not in combined.lower()
    assert "token" not in combined.lower()
    assert "salt" not in combined.lower()


def test_diff_does_not_alter_database(tmp_path):
    config = _config(tmp_path)
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", rating=5)]), apply=True)
    db_path = tmp_path / "library.db"
    before = db_path.read_bytes()

    ratings_diff(config, backup_only=True, output_format="json")

    assert db_path.read_bytes() == before


def test_export_json_and_csv(tmp_path):
    config = _config(tmp_path)
    item = RatingItem(navidrome_id="n1", title="Song", artist="Artist", rating=5, starred=True)
    ratings_backup(config, client=FakeClient(_nd_config(), [item]), apply=True)
    json_path = tmp_path / "ratings.json"
    csv_path = tmp_path / "ratings.csv"

    assert ratings_export(config, output_format="json", output=json_path)[0] == 0
    assert ratings_export(config, output_format="csv", output=csv_path)[0] == 0

    assert json.loads(json_path.read_text(encoding="utf-8"))[0]["title"] == "Song"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["title"] == "Song"


def test_restore_dry_run_does_not_call_write_endpoints(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path)
    backup_item = RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5, starred=True)
    ratings_backup(config, client=FakeClient(_nd_config(), [backup_item]), apply=True)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")])

    code, output = ratings_restore(config, client=client)

    assert code == 0
    assert "Mode: DRY-RUN" in output
    assert "would set 1 ratings" in output
    assert client.write_calls == []


def test_restore_apply_calls_rating_star_and_unstar(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="mb-track-1")
    _seed_track(config, tmp_path, mb_track_id="mb-track-2", artist="Other", title="Other", filename="other.flac")
    ratings_backup(
        config,
        client=FakeClient(
            _nd_config(),
            [
                RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5, starred=True),
                RatingItem(navidrome_id="n2", title="Other", artist="Other", mb_track_id="mb-track-2", starred=False),
            ],
        ),
        apply=True,
    )
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=None, starred=False), RatingItem(navidrome_id="n2", title="Other", artist="Other", mb_track_id="mb-track-2", starred=True)])

    code, output = ratings_restore(config, client=client, apply=True)

    assert code == 0
    assert ("setRating", "n1", 5) in client.write_calls
    assert ("star", "n1") in client.write_calls
    assert ("unstar", "n2") in client.write_calls
    assert "Status: OK" in output
    with connect(config) as conn:
        runs = conn.execute("SELECT COUNT(*) AS count FROM player_rating_restore_runs").fetchone()["count"]
        actions = conn.execute("SELECT COUNT(*) AS count FROM player_rating_restore_actions").fetchone()["count"]
    assert runs == 1
    assert actions == 3


def test_restore_medium_confidence_requires_flag_and_low_requires_force(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path, mb_track_id="", artist="Artist", title="Song", duration=123)
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", duration=123, rating=4), RatingItem(navidrome_id="low", title="Low", artist="Artist", rating=3)]), apply=True)
    medium_client = FakeClient(_nd_config(), [RatingItem(navidrome_id="new1", title="Song", artist="Artist", duration=123)])

    blocked = json.loads(ratings_restore(config, client=medium_client, output_format="json")[1])
    allowed = json.loads(ratings_restore(config, client=medium_client, allow_medium_confidence=True, output_format="json")[1])
    low_force_client = FakeClient(_nd_config(), [RatingItem(navidrome_id="low", title="Different")])
    low_forced = json.loads(ratings_restore(config, client=low_force_client, force=True, output_format="json")[1])

    assert blocked["status"] == "REVIEW"
    assert blocked["summary"]["would_set_ratings"] == 0
    assert allowed["summary"]["would_set_ratings"] == 1
    assert all(item["match_confidence"] != "low" or item["status"] != "planned" for item in allowed["items"])
    assert low_forced["status"] == "REVIEW"
    assert low_forced["summary"]["would_set_ratings"] == 1


def test_restore_conflict_review_preserve_and_idempotent(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path)
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5, starred=True)]), apply=True)
    dry_client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=2, starred=True)])

    payload = json.loads(ratings_restore(config, client=dry_client, output_format="json")[1])
    preserved = json.loads(ratings_restore(config, client=dry_client, preserve_server=True, output_format="json")[1])
    apply_client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=None, starred=False)])
    ratings_restore(config, client=apply_client, apply=True)
    second = json.loads(ratings_restore(config, client=apply_client, apply=True, output_format="json")[1])

    assert payload["status"] == "REVIEW"
    assert payload["summary"]["conflicts"] == 1
    assert preserved["summary"]["would_set_ratings"] == 0
    assert second["summary"]["would_set_ratings"] == 0
    assert second["summary"]["would_star"] == 0


def test_restore_without_backup_and_server_error_are_clear(tmp_path):
    config = _config(tmp_path)
    with connect(config) as conn:
        apply_migrations(conn)
        conn.commit()

    no_backup_code, no_backup_output = ratings_restore(config, client=FakeClient(_nd_config(), []))

    assert no_backup_code == 0
    assert "No backup runs found" in no_backup_output
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5)]), apply=True)
    error_code, error_output = ratings_restore(config, client=FakeClient(_nd_config(), [], ping_error="password token salt failed"))
    assert error_code == 1
    assert "Status: FAIL" in error_output
    assert "password" not in error_output.lower()
    assert "token" not in error_output.lower()
    assert "salt" not in error_output.lower()


def test_restore_json_and_csv_output(tmp_path):
    config = _config(tmp_path)
    _seed_track(config, tmp_path)
    ratings_backup(config, client=FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1", rating=5)]), apply=True)
    client = FakeClient(_nd_config(), [RatingItem(navidrome_id="n1", title="Song", artist="Artist", mb_track_id="mb-track-1")])

    payload = json.loads(ratings_restore(config, client=client, output_format="json")[1])
    rows = list(csv.DictReader(ratings_restore(config, client=client, output_format="csv")[1].splitlines()))

    assert payload["summary"]["would_set_ratings"] == 1
    assert rows[0]["action"] == "set_rating"
    assert "secret-password" not in json.dumps(payload)


def test_secrets_do_not_appear_in_output(tmp_path):
    config = _config(tmp_path)
    item = RatingItem(navidrome_id="n1", title="Song", artist="Artist", rating=5)

    _code, output = ratings_backup(config, client=FakeClient(_nd_config(), [item], ping_error="password token salt failed"), apply=True)

    assert "secret-password" not in output
    assert "password" not in output.lower()
    assert "token" not in output.lower()
    assert "salt" not in output.lower()
