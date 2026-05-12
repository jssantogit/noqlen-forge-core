from noqlen_forge.lastfm_filter import TagContext, clean_existing_lastfm_tags, classify_tag, filter_tags, normalize_tag


def test_normalize_common_lastfm_tags() -> None:
    cases = {
        "kpop": "K-pop",
        "k-pop": "K-pop",
        "K-Pop": "K-pop",
        "rnb": "R&B",
        "r&b": "R&B",
        "contemporary rnb": "Contemporary R&B",
        "uk garage": "UK Garage",
        "drum and bass": "Drum n Bass",
        "dnb": "Drum n Bass",
        "girl group": "Girl Group",
        "Girl Groups": "Girl Group",
        "future bass": "Future Bass",
        "bedroom pop": "Bedroom Pop",
        "hip hop soul": "Hip Hop Soul",
        "neo-soul": "Neo-Soul",
    }

    for raw, expected in cases.items():
        assert normalize_tag(raw) == expected


def test_classify_removes_noise_tags() -> None:
    context = TagContext(artist="NewJeans", albumartist="ADOR")
    removed = [
        "2023",
        "2010s",
        "favs",
        "favorite",
        "spotify",
        "youtube",
        "seen live",
        "hit",
        "vocal",
        "One time flamengo",
        "maris song",
        "you don't even know my name do ya",
        "NewJeans",
        "ADOR",
        "aespa",
        "ive",
    ]

    for raw in removed:
        decision = classify_tag(raw, context)
        assert decision.keep is False, raw


def test_filter_preserves_useful_music_tags() -> None:
    raw_tags = [
        {"name": "K-pop", "count": "1"},
        {"name": "UK Garage", "count": "1"},
        {"name": "Jersey Club", "count": "1"},
        {"name": "Technical Death Metal", "count": "1"},
        {"name": "Progressive Death Metal", "count": "1"},
        {"name": "Dreamy", "count": "1"},
        {"name": "Aggressive", "count": "1"},
        {"name": "Bedroom Pop", "count": "1"},
        {"name": "Hip Hop Soul", "count": "1"},
        {"name": "Neo-Soul", "count": "1"},
    ]

    result = filter_tags(raw_tags, TagContext(), min_count=3, max_tags=20)

    assert set(result.kept) == {item["name"] for item in raw_tags}


def test_filter_sorts_dedupes_and_reports_removals() -> None:
    raw_tags = [
        {"name": "Korean", "count": "10"},
        {"name": "K-pop", "count": "8"},
        {"name": "2023", "count": "99"},
        {"name": "favs", "count": "99"},
        {"name": "contemporary rnb", "count": "1"},
    ]

    result = filter_tags(raw_tags, TagContext(source="track"), min_count=3, max_tags=10)

    assert result.kept == ["K-pop", "Contemporary R&B", "Korean"]
    assert [(decision.original, decision.reason) for decision in result.removed] == [("2023", "year"), ("favs", "personal")]


def test_clean_existing_lastfm_tags_normalizes_and_prunes() -> None:
    value = "K-pop; Pop; girl group; Girl Groups; RESCENE; Korean"

    assert clean_existing_lastfm_tags(value, TagContext(artist="RESCENE")) == "K-pop; Pop; Girl Group; Korean"


def test_clean_existing_lastfm_tags_returns_empty_when_all_noise() -> None:
    assert clean_existing_lastfm_tags("hit; vocal; 2023", TagContext()) == ""
