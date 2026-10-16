import hashlib
import io
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from app.models.article_evidence import EvidenceCaptureRequest, NormalizedBBox, QuoteCardRequest, UserImageEvidenceRequest
from app.services import evidence_capture
from app.services.evidence_capture import EvidenceCaptureService


@pytest.fixture()
def public_article_server(tmp_path):
    quote = "조사 대상이 된 한국 등 나머지 45개국에 대해서는 12.5%의 추가 관세를 부과하겠다고 밝혔습니다."
    (tmp_path / "article.html").write_text(
        "<html><head><meta charset='utf-8'><title>한글 기사 테스트</title><meta property='og:site_name' content='테스트 언론사'>"
        "<meta property='article:published_time' content='2026-07-22'></head>"
        f"<body><p style='display:none'>{quote}</p><article><p style='font-size:32px;width:360px;line-height:1.8'>{quote}</p></article></body></html>",
        encoding="utf-8",
    )
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=str(tmp_path), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/article.html", quote
    finally:
        server.shutdown(); thread.join()


@pytest.mark.integration
def test_dom_capture_preserves_korean_and_multi_line_bboxes(monkeypatch, tmp_path, public_article_server):
    pytest.importorskip("playwright")
    try:
        EvidenceCaptureService._browser_thread().submit(EvidenceCaptureService._browser_instance).result()
    except Exception as exc:
        pytest.skip(f"Playwright Chromium is not installed: {exc}")
    monkeypatch.setattr(evidence_capture, "DATA_DIR", tmp_path)
    monkeypatch.setattr(evidence_capture, "validate_public_http_url", lambda url: url)
    monkeypatch.setattr(evidence_capture, "_is_public_host", lambda host: True)
    monkeypatch.setattr(EvidenceCaptureService, "_upload_minio", staticmethod(lambda *_: None))
    url, quote = public_article_server
    key_phrase = "12.5%의 추가 관세"
    result = EvidenceCaptureService().capture_dom(EvidenceCaptureRequest(
        job_id=11,
        source_url=url,
        quote=quote,
        key_phrase=key_phrase,
    ))
    assert Path(result.local_path).exists()
    assert result.publisher == "테스트 언론사"
    assert len(result.quote_bboxes) >= 2
    assert result.key_phrase == key_phrase
    assert result.key_phrase_bboxes
    assert result.bbox_source == "dom_range"
    assert result.image_sha256 == hashlib.sha256(Path(result.local_path).read_bytes()).hexdigest()


@pytest.mark.integration
def test_dom_capture_skips_hidden_duplicate_and_measures_visible_quote(monkeypatch, tmp_path, public_article_server):
    pytest.importorskip("playwright")
    try:
        EvidenceCaptureService._browser_thread().submit(EvidenceCaptureService._browser_instance).result()
    except Exception as exc:
        pytest.skip(f"Playwright Chromium is not installed: {exc}")
    monkeypatch.setattr(evidence_capture, "DATA_DIR", tmp_path)
    monkeypatch.setattr(evidence_capture, "validate_public_http_url", lambda url: url)
    monkeypatch.setattr(evidence_capture, "_is_public_host", lambda host: True)
    monkeypatch.setattr(EvidenceCaptureService, "_upload_minio", staticmethod(lambda *_: None))
    url, quote = public_article_server
    result = EvidenceCaptureService().capture_dom(EvidenceCaptureRequest(
        job_id=13,
        source_url=url,
        quote=quote,
        key_phrase=quote,
    ))
    assert result.quote_bboxes
    assert result.key_phrase_bboxes


def test_quote_card_is_labelled_editorial_asset(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence_capture, "DATA_DIR", tmp_path)
    monkeypatch.setattr(EvidenceCaptureService, "_upload_minio", staticmethod(lambda *_: None))
    result = EvidenceCaptureService().render_quote_card(QuoteCardRequest(
        job_id=12, quote="12.5%의 추가 관세를 부과한다.", publisher="테스트 언론사", published_at="2026.07.22",
    ))
    assert result.source_title == "기사 인용"
    assert result.capture_mode == "dom"
    assert Path(result.local_path).exists()


def test_private_url_is_rejected():
    with pytest.raises(evidence_capture.EvidenceCaptureError):
        evidence_capture.validate_public_http_url("http://127.0.0.1/internal")


def test_user_uploaded_capture_preserves_verified_coordinates(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence_capture, "DATA_DIR", tmp_path)
    monkeypatch.setattr(EvidenceCaptureService, "_upload_minio", staticmethod(lambda *_: None))
    source = Image.new("RGB", (1280, 720), "white")
    raw = io.BytesIO(); source.save(raw, "PNG")
    quote_box = NormalizedBBox(x=.12, y=.42, width=.54, height=.09)
    key_box = NormalizedBBox(x=.45, y=.42, width=.12, height=.09)
    result = EvidenceCaptureService().capture_user_image(UserImageEvidenceRequest(
        job_id=14,
        source_url="https://www.yna.co.kr/view/example",
        source_title="검증된 기사 화면",
        publisher="연합뉴스",
        quote="검증 완료된 기사 인용문",
        target_bbox=quote_box,
        quote_bboxes=[quote_box],
        key_phrase="검증",
        key_phrase_bboxes=[key_box],
    ), raw.getvalue())
    assert result.capture_mode == "user_image"
    assert result.bbox_source == "verified_input"
    assert result.quote_bboxes == [quote_box]
    assert result.key_phrase_bboxes == [key_box]
    assert result.image_sha256 == hashlib.sha256(Path(result.local_path).read_bytes()).hexdigest()
    assert Path(result.annotation_preview_path).exists()
    with Image.open(result.local_path) as original, Image.open(result.annotation_preview_path) as preview:
        assert ImageChops.difference(original.convert("RGB"), preview.convert("RGB")).getbbox() is not None


def test_dom_capture_always_runs_on_one_dedicated_thread(monkeypatch):
    """Playwright sync 브라우저는 만든 스레드에서만 쓸 수 있다.

    요청 스레드가 달라져도(끝난 스레드 포함) 캡처는 항상 같은 전용 스레드에서 실행돼야 한다.
    2026-10-16 재현: 스레드가 바뀐 뒤 모든 기사 캡처가 "cannot switch to a different thread"로 거절돼 기사 카드가 한 장도 안 나왔다."""
    seen = []
    monkeypatch.setattr(
        EvidenceCaptureService, "_capture_dom_sync",
        lambda self, request: seen.append(threading.get_ident()) or "ok",
    )
    callers = []

    def call():
        callers.append(threading.get_ident())
        assert EvidenceCaptureService().capture_dom(object()) == "ok"

    for _ in range(3):
        worker = threading.Thread(target=call)
        worker.start(); worker.join()

    assert len(set(seen)) == 1
    assert seen[0] not in callers
