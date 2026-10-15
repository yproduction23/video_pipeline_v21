from pathlib import Path

from app.models.article_evidence import ArticleCandidate, ArticleCapture, NormalizedBBox
from app.services.article.evidence_planner import (
    ArticleBodyResolver,
    ArticleEvidencePlanner,
    _query_for,
    narration_hash,
)


class FakeDiscovery:
    def discover(self, query, terms, limit):
        return [
            ArticleCandidate(
                title="코스피 반도체 상승",
                url="https://www.yna.co.kr/test",
                publisher="연합뉴스",
                published_at="2026-07-22",
                summary="코스피가 반도체 투톱 상승으로 12.5% 올랐다.",
            )
        ]


class FakeResolver:
    def __init__(self, sentences):
        self._sentences = sentences

    def sentences(self, candidate):
        return list(self._sentences)


class FakeCapture:
    def capture_dom(self, request):
        bbox = NormalizedBBox(x=.1, y=.3, width=.7, height=.1)
        key = NormalizedBBox(x=.42, y=.3, width=.18, height=.1)
        return ArticleCapture(
            source_url=request.source_url,
            source_title=request.source_title,
            publisher=request.publisher,
            published_at=request.published_at,
            captured_at="2026-07-23T00:00:00Z",
            capture_mode="dom",
            quote=request.quote,
            key_phrase=request.key_phrase,
            image_sha256="a" * 64,
            target_bbox=bbox,
            quote_bboxes=[bbox],
            key_phrase_bboxes=[key],
            local_path="/tmp/article.png",
        )


def _default_llm_call(system, messages, max_tokens):
    """단일 후보 문장을 가정한 테스트 기본값 — 항상 0번을 확인 문장으로 고른다."""
    return '{"index": 0}'


def _planner(tmp_path, sentences, llm_call=None):
    planner = ArticleEvidencePlanner(
        discovery=FakeDiscovery(),
        capture=FakeCapture(),
        sentence_resolver=FakeResolver(sentences),
        audit_dir=tmp_path,
        llm_call=llm_call or _default_llm_call,
    )
    planner._redis = staticmethod(lambda: None)
    return planner


def test_planner_attaches_exact_article_without_changing_narration(tmp_path):
    scene = {
        "scene_id": "scene-3",
        "phase": "twist",
        "content": "코스피가 반도체 투톱 상승으로 12.5퍼센트 올랐습니다.",
        "source_refs": ["facts[0]"],
    }
    before = narration_hash(scene)
    result = _planner(
        tmp_path,
        ["한국거래소에 따르면 코스피가 반도체 투톱 상승으로 12.5% 올랐다."],
    ).attach(
        job_id=77,
        scenes=[scene],
        verified_facts=[{
            "fact": "코스피가 반도체 투톱 상승으로 12.5% 올랐다.",
            "figure": "12.5%",
        }],
    )
    planned = result.scenes[0]
    assert planned["content"] == scene["content"]
    assert narration_hash(planned) == before
    assert planned["visual_kind"] == "article_scene"
    assert planned["emphasis_plan"]["body"] == "highlight_underline"
    assert planned["article_capture"]["quote"].endswith("12.5% 올랐다.")
    assert result.audit["selected_count"] == 1
    assert Path(tmp_path / "jobs/77/evidence/evidence_plan.json").is_file()


def test_unverified_article_keeps_original_scene(tmp_path):
    scene = {"scene_id": "scene-1", "content": "코스피가 12.5퍼센트 올랐습니다."}
    result = _planner(
        tmp_path,
        ["전혀 다른 회사의 매출이 3% 감소했다."],
    ).attach(
        job_id=78,
        scenes=[scene],
        verified_facts=[{"fact": "코스피가 12.5% 올랐다.", "figure": "12.5%"}],
    )
    assert "article_capture" not in result.scenes[0]
    assert result.audit["selected_count"] == 0


def test_query_for_does_not_pollute_search_with_a_raw_datetime_location_figure():
    """2026-10-14 job 18 재현: figure가 "2026-10-13 13:49 / 인천시 남동구
    홈플러스 구월점" 같은 "일시 / 장소" 복합 문자열일 때, 과거 코드는 이걸
    토큰화 없이 그대로 검색어 끝에 붙여 슬래시·콜론·ISO 날짜가 섞인 검색어를
    만들었다. 실제 네이버 뉴스 검색에 이 쿼리를 그대로 썼더니 0건이 나와,
    confidence=1·cross_verified=true인 사실조차 증거 검증 전부가
    EVIDENCE_SENTENCE_UNVERIFIED로 건너뛰어졌다. 쿼리에 "/"나 ":" 같은
    검색엔진에 무의미한 구두점이 들어가면 안 된다."""
    scene = {"scene_id": "script_scene_001", "content": "양손에 흉기를 직접 묶어 고정한 채 마트에 들어섰습니다."}
    fact = {
        "fact": "2026년 10월 13일 오후 1시 49분경 인천시 남동구 홈플러스 구월점에서 흉기 난동 사건이 발생했으며 가해자는 현장에서 체포됐다",
        "figure": "2026-10-13 13:49 / 인천시 남동구 홈플러스 구월점",
        "source_field": "keyword_news_evidence + youtube_topic_context",
        "source_ref": ["연합뉴스TV", "연합뉴스", "경북신문", "매일신문"],
        "confidence": 1,
        "cross_verified": True,
    }

    query, terms = _query_for(scene, fact)

    assert "/" not in query
    assert ":" not in query
    assert "2026-10-13" not in query
    assert "인천시" in query and "홈플러스" in query


def test_paraphrased_sentence_with_low_string_similarity_is_still_confirmed_by_llm(tmp_path):
    """2026-10-14 job 18 재현: 실제 중앙일보 기사 문장 "이날 흉기 난동은 오후
    1시49분쯤 홈플러스 인천구월점이 입점한 대형 복합상가에서 벌어졌다"는
    cross_verified=true인 사실을 정확히 확인해주는데도, 문자열 유사도가
    0.484(옛 기준 0.85 미달)라 전부 탈락했었다. 이제는 Claude가 "같은
    사건을 가리키는가"를 의미로 판단하므로, 어순이 다른 이 문장도
    채택되어야 한다."""
    scene = {
        "scene_id": "script_scene_001",
        "content": "양손에 흉기를 직접 묶어 고정한 채 마트에 들어섰습니다.",
        "source_refs": ["facts[0]"],
    }
    paraphrased = "이날 흉기 난동은 오후 1시49분쯤 홈플러스 인천구월점이 입점한 대형 복합상가에서 벌어졌다."

    def llm_call(system, messages, max_tokens):
        assert paraphrased in messages[0]["content"]
        return '{"index": 0}'

    result = _planner(tmp_path, [paraphrased], llm_call=llm_call).attach(
        job_id=18,
        scenes=[scene],
        verified_facts=[{
            "fact": "2026년 10월 13일 오후 1시 49분경 인천시 남동구 홈플러스 구월점에서 흉기 난동 사건이 발생했으며 가해자는 현장에서 체포됐다",
            "figure": "2026-10-13 13:49 / 인천시 남동구 홈플러스 구월점",
        }],
    )
    assert result.audit["selected_count"] == 1
    assert result.scenes[0]["article_capture"]["quote"] == paraphrased


def test_llm_declining_to_confirm_any_candidate_keeps_scene_unverified(tmp_path):
    """후보 문장이 숫자·용어 사전 필터를 통과해도, Claude가 실제로는 다른
    사건/수치를 가리킨다고 판단하면(null 반환) 증거를 채택하지 않는다 —
    틀린 인용보다 캡처 없이 넘어가는 게 항상 안전하다."""
    scene = {"scene_id": "scene-9", "content": "코스피가 12.5퍼센트 올랐습니다."}

    def llm_call(system, messages, max_tokens):
        return '{"index": null}'

    result = _planner(
        tmp_path,
        ["코스피가 12.5% 상승했지만 반도체가 아닌 2차전지 업종이 주도했다."],
        llm_call=llm_call,
    ).attach(
        job_id=79,
        scenes=[scene],
        verified_facts=[{"fact": "코스피가 12.5% 올랐다.", "figure": "12.5%"}],
    )
    assert "article_capture" not in result.scenes[0]
    assert result.audit["selected_count"] == 0


def test_attaches_a_numeric_series_found_near_the_confirmed_sentence(tmp_path):
    """2026-10-14 사용자 결정: 확인된 기사 안에 실제 연도별 숫자 계열이
    있으면(예: "△2023년 46건 △2024년 42건 △2025년 39건") article_capture에
    함께 붙여, 호출부가 텍스트 인용 대신 그래프를 그릴 수 있게 한다."""
    scene = {
        "scene_id": "scene-1",
        "content": "불특정 다수를 겨냥한 범죄가 반복되고 있습니다.",
        "source_refs": ["facts[0]"],
    }

    def llm_call(system, messages, max_tokens):
        return '{"index": 0}'

    result = _planner(
        tmp_path,
        [
            "불특정 다수를 겨냥한 이상동기 범죄가 반복되고 있다는 점도 문제다.",
            "경찰청에 따르면 이상동기 범죄는 △2023년 46건 △2024년 42건 △2025년 39건으로 매년 40건 안팎 발생했다.",
        ],
        llm_call=llm_call,
    ).attach(
        job_id=80,
        scenes=[scene],
        verified_facts=[{"fact": "불특정 다수를 겨냥한 이상동기 범죄가 반복되고 있다.", "figure": ""}],
    )

    series = result.scenes[0]["article_capture"]["numeric_series"]
    assert [item["label"] for item in series["items"]] == ["2023년", "2024년", "2025년"]
    assert [item["value"] for item in series["items"]] == [46.0, 42.0, 39.0]


class _FakeHttpxResponse:
    def __init__(self, url, text):
        self.url = url
        self.text = text

    def raise_for_status(self):
        pass


def test_follows_a_redirect_that_stays_within_the_same_approved_publisher(monkeypatch):
    """2026-10-14 job 18 재현: "의자로 가해자를 제압한 시민들" 기사가
    매일경제 URL 구조 변경(https://www.mk.co.kr/article/... ->
    https://www.mk.co.kr/news/society/...)으로 301 리다이렉트됐는데,
    follow_redirects=False라서 그냥 예외로 버려졌다. 같은 승인 매체
    안에서의 리다이렉트는 따라가야 한다."""
    html = "<html lang='ko'><article>현장에 있던 시민들이 의자를 이용해 가해자를 막으며 제압에 가담한 것으로 전해졌다.</article></html>"

    def fake_get(url, **kwargs):
        assert kwargs.get("follow_redirects") is True
        return _FakeHttpxResponse("https://www.mk.co.kr/news/society/12176066", html)

    monkeypatch.setattr("app.services.article.evidence_planner.httpx.get", fake_get)

    sentences = ArticleBodyResolver().sentences(
        ArticleCandidate(title="t", url="https://www.mk.co.kr/article/12176066", publisher="매일경제")
    )

    assert any("의자" in s for s in sentences)


def test_rejects_a_redirect_that_leaves_the_approved_publisher_allowlist(monkeypatch):
    """2026-10-14 job 18 재현: mbn.mk.co.kr 기사가 mbn.co.kr(허용 목록에
    없는 도메인)로 302 리다이렉트됐다. 승인 매체 밖으로 벗어난 리다이렉트는
    따라간 뒤에도 거부해야 한다 — 보안 경계는 그대로 유지한다."""
    html = "<html lang='ko'><article>의자 하나로 흉기범을 제압했다.</article></html>"

    def fake_get(url, **kwargs):
        return _FakeHttpxResponse("https://www.mbn.co.kr/pages/news/newsView.php", html)

    monkeypatch.setattr("app.services.article.evidence_planner.httpx.get", fake_get)

    sentences = ArticleBodyResolver().sentences(
        ArticleCandidate(title="t", url="http://mbn.mk.co.kr/pages/news/newsView.php", publisher="")
    )

    assert sentences == []


def test_tokens_drops_fragments_glued_to_a_preceding_digit():
    """2026-10-14 job 18 재현: "49분경"에서 과거 토크나이저는 "분경"이라는
    실제 단어가 아닌 조각을 뽑아냈고, 이 조각 하나가 검색어에 섞이자 실제로
    기사가 존재하는데도(live Naver 검색 라이브 재현으로 확인) 검색 결과가
    0건이 됐다. 숫자에 바로 이어 붙어 시작하는 매치는 토큰에서 제외해야
    한다."""
    from app.services.article.evidence_planner import _tokens

    tokens = _tokens("2026년 10월 13일 오후 1시 49분경 인천시 남동구 홈플러스 구월점에서 발생")

    assert "분경" not in tokens
    assert "인천시" in tokens
    assert "홈플러스" in tokens
