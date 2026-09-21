"""핫키워드 집계: 기간 창, 시간당 조회수, 지속성 판정, 불용어 제외."""
import pytest

from app.utils.hot_keywords import aggregate_hot_keywords


def split(text):
    return text.split()


def video(video_id, title, hours, views, tags=None):
    return {"videoId": video_id, "title": title, "hoursSincePublish": hours, "views": views, "tags": tags or []}


def test_48h_window_excludes_older_videos():
    videos = [video("a", "부산상어 챌린지", 10, 10000), video("b", "환율 전망", 100, 50000)]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert [v["videoId"] for v in result["videos"]] == ["a"]
    assert {k["keyword"] for k in result["keywords"]} == {"부산상어", "챌린지"}


def test_views_per_hour_clamps_very_fresh_videos_to_one_hour():
    result = aggregate_hot_keywords([video("a", "부산상어", 0.2, 500)], "48h", split)

    assert result["videos"][0]["viewsPerHour"] == 500.0


def test_persistence_labels_in_7d_window():
    videos = [
        video("a", "부산상어 신곡", 10, 10000),
        video("b", "부산상어 리믹스", 100, 20000),
        video("c", "애프터마켓 오픈", 5, 8000),
        video("d", "환율 급등", 120, 9000),
    ]

    result = aggregate_hot_keywords(videos, "7d", split)
    labels = {k["keyword"]: k["persistence"] for k in result["keywords"]}

    assert labels["부산상어"] == "sustained"
    assert labels["애프터마켓"] == "spike"
    assert labels["환율"] == "fading"


def test_48h_window_marks_keyword_sustained_when_older_videos_share_it():
    videos = [video("a", "부산상어 신곡", 10, 10000), video("b", "부산상어 리믹스", 100, 20000)]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert {k["keyword"]: k["persistence"] for k in result["keywords"]}["부산상어"] == "sustained"


def test_stop_words_filler_and_short_tokens_are_dropped():
    videos = [video("a", "영상 시장 부산상어 a 2026", 5, 1000)]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert [k["keyword"] for k in result["keywords"]] == ["부산상어"]


def test_keywords_sorted_by_score_and_limited():
    videos = [video("a", "상어", 10, 1000), video("b", "환율", 10, 9000)]

    result = aggregate_hot_keywords(videos, "48h", split, limit=1)

    assert [k["keyword"] for k in result["keywords"]] == ["환율"]


def test_tags_contribute_keywords():
    videos = [video("a", "제목", 10, 1000, tags=["부산상어"])]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert "부산상어" in {k["keyword"] for k in result["keywords"]}


def test_invalid_window_raises():
    with pytest.raises(ValueError):
        aggregate_hot_keywords([], "30d", split)
