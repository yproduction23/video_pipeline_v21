"""Public article capture with DOM-grounded quote coordinates.

This service deliberately does not authenticate, solve challenges, or bypass a
paywall.  It creates a disposable browser context for each request so cookies,
storage and login state cannot leak between jobs, while reusing Chromium for
the worker's lifetime.
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import logging
import os
import socket
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from app.config import S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT, S3_SECRET_KEY
from app.models.article_evidence import (
    ArticleCapture,
    EvidenceCaptureRequest,
    NormalizedBBox,
    QuoteCardRequest,
    UserImageEvidenceRequest,
)
from app.services.article.source_policy import assert_article_source, korean_mirror_url
from app.services.annotate import render_annotations

logger = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
VIEWPORT = {"width": 1920, "height": 1080}
DEVICE_SCALE_FACTOR = 2


class EvidenceCaptureError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _is_public_host(hostname: str) -> bool:
    """Resolve every address and reject loopback/private/link-local targets."""
    if not hostname or hostname.lower() == "localhost":
        return False
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


def validate_public_http_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EvidenceCaptureError("only public http(s) article URLs are supported", 400)
    if parsed.username or parsed.password:
        raise EvidenceCaptureError("URLs with embedded credentials are not accepted", 400)
    if parsed.port not in {None, 80, 443}:
        raise EvidenceCaptureError("non-standard article ports are not accepted", 400)
    if not _is_public_host(parsed.hostname):
        raise EvidenceCaptureError("private, loopback, or unresolved hosts are not accepted", 400)
    return raw_url


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/app/assets/fonts/GmarketSansTTFBold.ttf",
        "/app/assets/fonts/Jalnan2TTF.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
        "DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


_DOM_QUOTE_RECTS = r"""
(payload) => {
  const request = typeof payload === 'string' ? {needle: payload} : payload;
  const quote = request.needle || '';
  const within = typeof request.within === 'string' ? request.within.trim() : '';
  const skipped = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG']);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim() || skipped.has(node.parentElement?.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  const nodes = []; let joined = ''; let node;
  while ((node = walker.nextNode())) { nodes.push({node, start: joined.length, end: joined.length + node.nodeValue.length}); joined += node.nodeValue; }
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Prefer an exact Unicode match. This preserves Korean text exactly and
  // avoids a browser-specific RegExp edge case observed for multi-line Hangul
  // captures. Whitespace-flexible matching remains the fallback for article
  // DOMs which insert line breaks between words.
  const exactQuote = quote.trim();
  const pattern = exactQuote.split(/\s+/).map(escape).join('\\s+');
  const offsetToNode = (offset, preferNextAtBoundary = false) => {
    // 시작 위치가 두 텍스트 노드의 경계와 정확히 겹치면 다음 노드를
    // 선택한다. 이전 노드의 끝을 시작점으로 잡으면 숫자만 감싼 Range가
    // 빈 Range로 해석될 수 있다.
    const item = nodes.find(entry => offset >= entry.start && (
      offset < entry.end || (offset === entry.end && !preferNextAtBoundary)
    ));
    if (!item) return null;
    return {node: item.node, offset: Math.min(item.node.nodeValue.length, Math.max(0, offset - item.start))};
  };
  const findExactMatches = value => {
    const found = [];
    let exactIndex = joined.indexOf(value);
    while (exactIndex >= 0) {
      found.push({index: exactIndex, length: value.length});
      exactIndex = joined.indexOf(value, exactIndex + Math.max(1, value.length));
    }
    return found;
  };
  const matches = findExactMatches(exactQuote);
  const contextMatches = within ? findExactMatches(within) : [];
  if (!matches.length) {
    const fallback = new RegExp(pattern).exec(joined);
    if (fallback) matches.push({index: fallback.index, length: fallback[0].length});
  }
  for (const match of matches) {
    // 숫자처럼 짧은 구절은 이미지 캡션·관련 기사에도 반복된다. 검증자가
    // 지정한 원문 인용 안에 있는 동일 구절만 좌표화한다.
    if (contextMatches.length && !contextMatches.some(context => (
      match.index >= context.index && match.index + match.length <= context.index + context.length
    ))) continue;
    const start = offsetToNode(match.index, true), end = offsetToNode(match.index + match.length);
    if (!start || !end) continue;
    const range = document.createRange(); range.setStart(start.node, start.offset); range.setEnd(end.node, end.offset);
    const rects = Array.from(range.getClientRects()).filter(rect => rect.width > 1 && rect.height > 1).map(rect => ({x: rect.x, y: rect.y, width: rect.width, height: rect.height}));
    if (!rects.length) continue;
    const container = range.commonAncestorContainer.parentElement?.closest('article, [itemprop="articleBody"], .article-body, .article-content') || range.commonAncestorContainer.parentElement;
    const containerBox = container?.getBoundingClientRect();
    const published = document.querySelector('meta[property="article:published_time"], meta[name="date"], time')?.getAttribute('content') || document.querySelector('time')?.dateTime || '';
    const publisher = document.querySelector('meta[property="og:site_name"], meta[name="author"]')?.getAttribute('content') || '';
    return {
      found: true, rects, title: document.title, published, publisher,
      containerRect: containerBox ? {x: containerBox.x, y: containerBox.y, width: containerBox.width, height: containerBox.height} : null,
      viewport: {width: innerWidth, height: innerHeight}
    };
  }
  return {found: false, title: document.title, bodyText: document.body.innerText.slice(0, 4000)};
}
"""


class EvidenceCaptureService:
    _lock = threading.RLock()
    _playwright = None
    _browser = None
    _executor: ThreadPoolExecutor | None = None

    @classmethod
    def _browser_thread(cls) -> ThreadPoolExecutor:
        """Playwright sync API는 브라우저를 만든 스레드에서만 쓸 수 있다.

        요청 스레드(FastAPI 스레드풀)는 수시로 끝나므로, 브라우저를 만들고 쓰는 모든 일을 전용 스레드 하나에서 한다."""
        with cls._lock:
            if cls._executor is None:
                cls._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright-capture")
            return cls._executor

    @classmethod
    def _browser_instance(cls):
        with cls._lock:
            if cls._browser is not None:
                return cls._browser
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise EvidenceCaptureError("Playwright is not installed in this worker image", 503) from exc
            cls._playwright = sync_playwright().start()
            cls._browser = cls._playwright.chromium.launch(headless=True)
            return cls._browser

    @classmethod
    def shutdown(cls) -> None:
        executor = cls._executor
        if executor is not None:
            try:
                executor.submit(cls._shutdown_browser).result(timeout=30)
            finally:
                executor.shutdown(wait=False)
                cls._executor = None
            return
        cls._shutdown_browser()

    @classmethod
    def _shutdown_browser(cls) -> None:
        with cls._lock:
            if cls._browser is not None:
                cls._browser.close()
                cls._browser = None
            if cls._playwright is not None:
                cls._playwright.stop()
                cls._playwright = None

    def _route_public_only(self, route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.scheme in {"data", "blob", "about"}:
            route.continue_()
            return
        if parsed.scheme not in {"http", "https"} or not _is_public_host(parsed.hostname or ""):
            route.abort()
            return
        route.continue_()

    def capture_dom(self, request: EvidenceCaptureRequest) -> ArticleCapture:
        return self._browser_thread().submit(self._capture_dom_sync, request).result()

    def _capture_dom_sync(self, request: EvidenceCaptureRequest) -> ArticleCapture:
        # Legacy callers may still send source_url alone.  New v2 callers send
        # ArticleSource and therefore enter the domestic-Korean source policy.
        source_url = request.source.url if request.source else request.source_url
        source_url = korean_mirror_url(source_url)
        source_url = validate_public_http_url(source_url)
        publisher_rule = assert_article_source(request) if request.source else None
        browser = self._browser_instance()
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="ko-KR",
            color_scheme="light",
            user_agent="Mozilla/5.0 (compatible; VideoPipelineEvidence/1.0; public-source-capture)",
        )
        try:
            context.route("**/*", self._route_public_only)
            page = context.new_page()
            page.set_default_timeout(15_000)
            response = page.goto(source_url, wait_until="domcontentloaded", timeout=20_000)
            if response is None or response.status >= 400:
                raise EvidenceCaptureError(f"article could not be loaded (HTTP {getattr(response, 'status', 'unknown')})")
            validate_public_http_url(page.url)
            try:
                page.wait_for_load_state("networkidle", timeout=4_000)
            except Exception:
                pass
            page_text = page.locator("body").inner_text(timeout=4_000)
            if page.locator("input[type=password]").count() or any(marker in page_text for marker in ("로그인 후 이용", "구독 후 이용", "결제 후 이용", "회원 전용")):
                raise EvidenceCaptureError("login- or subscription-restricted pages are not captured", 403)
            if request.source:
                try:
                    assert_article_source(
                        request,
                        html=page.locator("html").evaluate("node => node.outerHTML.slice(0, 2000)"),
                        title=page.title(),
                        body_sample=page_text[:4000],
                    )
                except ValueError as exc:
                    raise EvidenceCaptureError(str(exc), 422) from exc
            if publisher_rule:
                # Editorial-frame mode is intentionally confined to reviewed
                # publishers.  Reflow the article body before measuring the
                # Range, yielding legible source pixels rather than a generic
                # browser-page crop.  The quote itself is never rewritten.
                selectors = ",".join(publisher_rule.containers)
                excluded = ",".join(publisher_rule.excludes)
                page.add_style_tag(content=(
                    f"{excluded}{{display:none!important}}"
                    f"{selectors}{{max-width:1180px!important;margin:42px auto!important;padding:34px 44px!important;"
                    "font-size:46px!important;line-height:1.58!important;background:#fff!important;color:#111!important}}"
                    f"{selectors} p,{selectors} div{{font-size:46px!important;line-height:1.58!important;color:#111!important}}"
                ))
                page.wait_for_timeout(150)
            found = page.evaluate(_DOM_QUOTE_RECTS, {"needle": request.quote})
            if not found.get("found"):
                raise EvidenceCaptureError("the requested quote was not found in the public article DOM")
            rects = found["rects"]
            min_x = min(rect["x"] for rect in rects); max_x = max(rect["x"] + rect["width"] for rect in rects)
            min_y = min(rect["y"] for rect in rects); max_y = max(rect["y"] + rect["height"] for rect in rects)
            page.evaluate("([x, y]) => window.scrollTo(Math.max(0, x), Math.max(0, y))", [0, max(0, min_y - 180)])
            # The range geometry changes after scrolling, so obtain final viewport coordinates.
            found = page.evaluate(_DOM_QUOTE_RECTS, {"needle": request.quote})
            rects = found["rects"]
            key_found = (
                page.evaluate(_DOM_QUOTE_RECTS, {"needle": request.key_phrase, "within": request.quote})
                if request.key_phrase
                else {"found": False, "rects": []}
            )
            png = page.screenshot(animations="disabled", caret="hide", type="png")
            image = Image.open(io.BytesIO(png)).convert("RGBA")
            viewport = found.get("viewport") or VIEWPORT
            sx, sy = image.width / viewport["width"], image.height / viewport["height"]
            min_x = min(rect["x"] for rect in rects); max_x = max(rect["x"] + rect["width"] for rect in rects)
            min_y = min(rect["y"] for rect in rects); max_y = max(rect["y"] + rect["height"] for rect in rects)
            left = max(0, round((min_x - 110) * sx)); top = max(0, round((min_y - 120) * sy))
            right = min(image.width, round((max_x + 110) * sx)); bottom = min(image.height, round((max_y + 130) * sy))
            cropped = image.crop((left, top, right, bottom))
            def normalize_rect(rect: dict) -> NormalizedBBox | None:
                raw_left = rect["x"] * sx - left
                raw_top = rect["y"] * sy - top
                raw_right = raw_left + rect["width"] * sx
                raw_bottom = raw_top + rect["height"] * sy
                clipped_left = max(0.0, raw_left)
                clipped_top = max(0.0, raw_top)
                clipped_right = min(float(cropped.width), raw_right)
                clipped_bottom = min(float(cropped.height), raw_bottom)
                if clipped_right - clipped_left <= 1 or clipped_bottom - clipped_top <= 1:
                    return None
                return NormalizedBBox(
                    x=clipped_left / cropped.width,
                    y=clipped_top / cropped.height,
                    width=(clipped_right - clipped_left) / cropped.width,
                    height=(clipped_bottom - clipped_top) / cropped.height,
                )

            quote_bboxes = [box for rect in rects if (box := normalize_rect(rect))]
            key_phrase_bboxes = [
                box for rect in key_found.get("rects", [])
                if (box := normalize_rect(rect))
            ]
            union = NormalizedBBox(
                x=min(item.x for item in quote_bboxes), y=min(item.y for item in quote_bboxes),
                width=max(item.x + item.width for item in quote_bboxes) - min(item.x for item in quote_bboxes),
                height=max(item.y + item.height for item in quote_bboxes) - min(item.y for item in quote_bboxes),
            )
            image_bytes = io.BytesIO(); cropped.save(image_bytes, "PNG")
            return self._persist_capture(
                request.job_id,
                image_bytes.getvalue(),
                request,
                found,
                union,
                quote_bboxes,
                key_phrase_bboxes,
            )
        except EvidenceCaptureError:
            raise
        except Exception as exc:
            logger.exception("DOM article capture failed")
            raise EvidenceCaptureError(f"DOM article capture failed: {exc}") from exc
        finally:
            context.close()

    def capture_user_image(self, request: UserImageEvidenceRequest, image_bytes: bytes) -> ArticleCapture:
        """업로드된 원본을 보존하고, 사람이 검증한 좌표 계약만 저장한다."""
        if not image_bytes or len(image_bytes) > 25 * 1024 * 1024:
            raise EvidenceCaptureError("uploaded evidence image must be between 1 byte and 25 MiB", 400)
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                source.load()
                if source.width < 320 or source.height < 180:
                    raise EvidenceCaptureError("uploaded evidence image is too small", 400)
                if source.width > 7680 or source.height > 7680:
                    raise EvidenceCaptureError("uploaded evidence image exceeds 7680 pixels on one edge", 400)
                normalized = source.convert("RGBA")
        except EvidenceCaptureError:
            raise
        except Exception as exc:
            raise EvidenceCaptureError("uploaded evidence file is not a readable image", 400) from exc

        # 원본 포맷 차이와 무관하게 이후 합성 입력을 PNG로 고정한다.
        data = io.BytesIO()
        normalized.save(data, "PNG")
        canonical_png = data.getvalue()
        sha256 = hashlib.sha256(canonical_png).hexdigest()
        evidence_dir = DATA_DIR / "jobs" / str(request.job_id) / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        image_path = evidence_dir / f"user_upload_{sha256[:16]}.png"
        preview_path = evidence_dir / f"user_upload_{sha256[:16]}_annotation_preview.png"
        metadata_path = evidence_dir / f"user_upload_{sha256[:16]}.json"
        with tempfile.NamedTemporaryFile(dir=evidence_dir, delete=False) as temp:
            temp.write(canonical_png)
            temp_path = Path(temp.name)
        temp_path.replace(image_path)
        # 미리보기는 사람이 준 좌표가 의도한 기사 문구를 가리키는지 즉시 확인하는 용도다.
        annotations = [
            {"type": "highlighter", "bbox": box.model_dump(), "color": "#FFE146"}
            for box in request.quote_bboxes
        ]
        annotations.append({"type": "underline", "bboxes": [box.model_dump() for box in request.quote_bboxes]})
        annotations.extend(
            {"type": "ellipse", "bbox": box.model_dump()}
            for box in request.key_phrase_bboxes
        )
        preview = Image.alpha_composite(normalized, render_annotations(normalized.size, annotations))
        preview.save(preview_path, "PNG")
        capture = ArticleCapture(
            source_url=request.source_url,
            source_title=request.source_title,
            publisher=request.publisher,
            published_at=request.published_at,
            captured_at=datetime.now(timezone.utc),
            capture_mode="user_image",
            quote=request.quote,
            image_sha256=sha256,
            target_bbox=request.target_bbox,
            quote_bboxes=request.quote_bboxes,
            key_phrase=request.key_phrase,
            key_phrase_bboxes=request.key_phrase_bboxes,
            bbox_source="verified_input",
            local_path=str(image_path),
            annotation_preview_path=str(preview_path),
            object_key=f"jobs/{request.job_id}/evidence/{image_path.name}",
        )
        self._upload_minio(image_path, capture.object_key)
        metadata_path.write_text(capture.model_dump_json(indent=2), encoding="utf-8")
        return capture

    def _persist_capture(
        self,
        job_id: int,
        image_bytes: bytes,
        request: EvidenceCaptureRequest,
        dom: dict,
        target_bbox: NormalizedBBox,
        quote_bboxes: list[NormalizedBBox],
        key_phrase_bboxes: list[NormalizedBBox] | None = None,
    ) -> ArticleCapture:
        sha256 = hashlib.sha256(image_bytes).hexdigest()
        evidence_dir = DATA_DIR / "jobs" / str(job_id) / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        image_path = evidence_dir / f"article_{sha256[:16]}.png"
        metadata_path = evidence_dir / f"article_{sha256[:16]}.json"
        with tempfile.NamedTemporaryFile(dir=evidence_dir, delete=False) as temp:
            temp.write(image_bytes)
            temp_path = Path(temp.name)
        temp_path.replace(image_path)
        capture = ArticleCapture(
            source_url=request.source.url if request.source else request.source_url,
            source_title=request.source_title or str(dom.get("title") or "").strip(),
            publisher=(request.source.publisher if request.source else request.publisher) or str(dom.get("publisher") or "").strip(),
            published_at=request.published_at or str(dom.get("published") or "").strip() or None,
            captured_at=datetime.now(timezone.utc),
            capture_mode="dom",
            quote=request.quote,
            image_sha256=sha256,
            target_bbox=target_bbox,
            quote_bboxes=quote_bboxes,
            key_phrase=request.key_phrase,
            key_phrase_bboxes=key_phrase_bboxes or [],
            bbox_source="dom_range",
            local_path=str(image_path),
            object_key=f"jobs/{job_id}/evidence/{image_path.name}",
        )
        self._upload_minio(image_path, capture.object_key)
        metadata_path.write_text(capture.model_dump_json(indent=2), encoding="utf-8")
        return capture

    @staticmethod
    def _upload_minio(local_path: Path, object_key: str | None) -> None:
        if not object_key:
            return
        try:
            import boto3
            client = boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_ACCESS_KEY, aws_secret_access_key=S3_SECRET_KEY)
            client.upload_file(str(local_path), S3_BUCKET, object_key, ExtraArgs={"ContentType": "image/png"})
        except Exception as exc:
            # Local artifact is authoritative; MinIO availability must not erase evidence.
            logger.warning("evidence MinIO upload deferred for %s: %s", local_path.name, exc)

    def render_quote_card(self, request: QuoteCardRequest) -> ArticleCapture:
        width, height = request.canvas_size
        if width < 320 or height < 180:
            raise EvidenceCaptureError("quote-card canvas is too small", 400)
        image = Image.new("RGBA", (width, height), "white")
        draw = ImageDraw.Draw(image)
        red = (230, 0, 35, 255)
        margin = round(width * 0.055)
        draw.rectangle((margin, round(height * .16), width - margin, round(height * .84)), outline=red, width=max(8, round(width * .006)))
        label_font = _font(max(28, round(width * .026)))
        quote_font = _font(max(34, round(width * .04)))
        credit_font = _font(max(22, round(width * .018)))
        label_box = (margin + 26, round(height * .11), margin + round(width * .19), round(height * .18))
        draw.rounded_rectangle(label_box, radius=12, fill=red)
        draw.text((label_box[0] + 16, label_box[1] + 8), "기사 인용", font=label_font, fill="white")
        max_text_width = width - margin * 2 - round(width * .08)
        words = request.quote.split()
        lines: list[str] = []; current = ""
        for word in words or [request.quote]:
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=quote_font) > max_text_width:
                lines.append(current); current = word
            else:
                current = candidate
        if current: lines.append(current)
        while len(lines) > 4 and quote_font.size > 24:
            quote_font = _font(quote_font.size - 2); lines = []; current = ""
            for word in words or [request.quote]:
                candidate = f"{current} {word}".strip()
                if current and draw.textlength(candidate, font=quote_font) > max_text_width:
                    lines.append(current); current = word
                else: current = candidate
            if current: lines.append(current)
        text = "\n".join(lines[:4])
        draw.multiline_text((margin + round(width * .04), round(height * .28)), text, font=quote_font, fill=(20, 24, 28), spacing=18)
        credit = f"출처: {request.publisher} · {request.published_at}"
        draw.text((margin + round(width * .04), round(height * .75)), credit, font=credit_font, fill=(85, 95, 110))
        data = io.BytesIO(); image.save(data, "PNG")
        dummy = EvidenceCaptureRequest(job_id=request.job_id, source_url=request.source_url or "https://example.invalid", quote=request.quote, publisher=request.publisher, published_at=request.published_at)
        target = NormalizedBBox(x=.08, y=.25, width=.84, height=.46)
        return self._persist_capture(request.job_id, data.getvalue(), dummy, {"title": "기사 인용", "publisher": request.publisher, "published": request.published_at}, target, [target])
