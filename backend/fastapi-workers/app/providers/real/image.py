"""
Nana Banana AI Image Provider — Fal.ai Flux + Gemini API + pollinations.ai fallback

■ 우선순위 (Sprint 3 업데이트)
  1. Fal.ai Flux (채널에 LoRA 모델 있을 시): fal-ai/flux-lora — 캐릭터 일관성 최강
  2. Fal.ai Flux (LoRA 없을 시): fal-ai/flux/schnell — 기본 고품질 생성
  3. Gemini API: gemini-3.1-flash-image — 무료 Tier 활용
  4. pollinations.ai: 무료 무인증 폴백 ($0)

■ 배경 전용 모드 (S2-1):
   - character_style_prompt="background_only" 전달 시 캐릭터 묘사 미주입
   - 캐릭터 라이브러리 overlay 합성용 순수 배경 생성

■ LoRA 캐릭터 일관성 (Sprint 3):
   - lora_model_id (safetensors CDN URL) 지정 시 fal-ai/flux-lora 엔드포인트 사용
   - loras=[{"path": lora_model_id, "scale": lora_scale}] 파라미터로 전달
   - 프롬프트 앞에 trigger_word 자동 삽입

■ Fal.ai 서킷 브레이커:
   - 계정 잠김/잔액 부족(403) 감지 시 즉시 Gemini로 폴백
   - 이후 모든 요청은 Gemini/Pollinations로 직행

v3.0 변경사항 (Sprint 3 LoRA 통합):
   [신규] fal-ai/flux-lora LoRA 추론 지원 — lora_model_id 파라미터
   [수정] Gemini 모델명 → gemini-3.1-flash-image (2026 라이브 API 확인 완료)
   [유지] background_only 모드, Fal.ai 서킷 브레이커
"""
import os
import json
import base64
import logging
import urllib.parse
import urllib.request
from pathlib import Path

from app.providers.base import ImageProvider
from app.providers.real.prompt_builder import STYLE_LOCK
from app.utils.budget import (
    ProviderRequestBudgetExceeded,
    can_charge_openai_image,
    record_openai_image_cost,
)
from app.utils.image_request_control import ImageRequestHeld, payload_evidence, digest

logger = logging.getLogger(__name__)


class GeminiImageGenerationError(RuntimeError):
    """A Gemini image request failed after its same-quality retry policy."""


# 계정 자체가 막힌 오류(권한 없음/쿼터 완전 소진)만 여기 해당한다. 5xx 같은
# 일시적 오류는 제외해 기존 Gemini 예산/쿼터 재시도 계약을 그대로 신뢰한다.
_ACCOUNT_LEVEL_GEMINI_ERROR_MARKERS = ("PERMISSION_DENIED", "RESOURCE_EXHAUSTED")


def _is_account_level_gemini_error(exc: BaseException) -> bool:
    message = str(exc)
    return any(marker in message for marker in _ACCOUNT_LEVEL_GEMINI_ERROR_MARKERS)

# Legacy fallback only.  Jobs with a selected character pass either a channel
# style, a reference asset, a pose library, or a LoRA and never receive this
# description.  Keeping it isolated prevents the old mint mascot from leaking
# into selected-character scenes.
CHARACTER_STYLE = (
    "featuring the 2D gold coin mascot character (Goldie), a round shiny gold coin body, pink cheeks, expressive eyes, white gloved hands, "
)

# 금융 테마 프롬프트 스타일 수식어
FINANCE_STYLE = (
    "original 2D Korean finance editorial comic illustration, thick variable black ink outlines, "
    "two-to-three tone cel shading, saturated controlled palette, subtle print texture, layered foreground midground and background, "
    "expressive readable faces, dynamic perspective, no photorealism, no glossy 3D toy render, "
    "no text, no letters, no words, no watermark, no UI elements"
)

# [S2-1] 배경 전용 모드 스타일 수식어 (캐릭터 없는 순수 배경용)
# 캐릭터 라이브러리 포즈 이미지와 FFmpeg overlay 합성될 배경 생성에 사용
BACKGROUND_ONLY_STYLE = (
    "NON-NEGOTIABLE ART DIRECTION: no people, no characters, no mascots, no figures; "
    "wide 2D Korean editorial-cartoon establishing shot, bold variable ink outlines on every background object, two-tone cel shading, "
    "specific real-world business props and layered industrial environment, colorful controlled scene palette, "
    "not a dark empty studio, no photorealism, no realistic photographic textures, no glossy 3D render, "
    "no text, no letters, no words, no watermark, no UI elements"
)

# [S2-1] 배경 전용 모드 트리거 키워드
BACKGROUND_ONLY_TRIGGER = "background_only"


class NanaBananaProvider(ImageProvider):
    """
    Nano Banana Pro (Google Gemini API) 및 pollinations.ai 기반 이미지 생성 프로바이더.
    """
    _gemini_disabled = False
    # [신규] Fal.ai 계정 잠김/잔액 부족 감지 시 켜지는 서킷 브레이커.
    # 기존에는 매 씬마다 Fal.ai를 먼저 시도했다가 403(잔액 부족)을 받고서야
    # Gemini/Pollinations로 넘어갔는데, 잔액 부족은 그 Job이 끝날 때까지
    # (혹은 사람이 충전할 때까지) 절대 저절로 풀리지 않는 상태이므로, 매번
    # 다시 시도하는 건 순전히 시간 낭비였습니다. 한 번 감지되면 이후 요청은
    # Fal.ai를 건너뛰고 바로 Gemini/Pollinations로 갑니다.
    _fal_disabled = False

    def __init__(self):
        self.fallback_url = "https://image.pollinations.ai/prompt"
        self.width = 1920
        self.height = 1080

    def generate_image(self, prompt: str, output_path: str, **kwargs) -> str:
        """images_worker.py에서 호출하는 메서드 별칭"""
        return self.generate(prompt=prompt, output_path=output_path, **kwargs)

    def generate_with_lora(self, prompt: str, output_path: str,
                           lora_model_id: str, trigger_word: str = "",
                           lora_scale: float = 1.0, **kwargs) -> str:
        """[Sprint 3] LoRA 모델을 명시적으로 지정하여 이미지 생성"""
        return self.generate(
            prompt=prompt,
            output_path=output_path,
            lora_model_id=lora_model_id,
            lora_trigger_word=trigger_word,
            lora_scale=lora_scale,
            **kwargs
        )

    def generate(self, prompt: str, output_path: str, **kwargs) -> str:
        """
        프롬프트를 기반으로 AI 이미지를 생성하여 output_path에 저장.

        [Sprint 3] LoRA 파라미터:
          - lora_model_id: safetensors CDN URL (fal-ai/flux-lora 사용)
          - lora_trigger_word: LoRA 활성화 트리거 단어 (프롬프트 앞에 자동 삽입)
          - lora_scale: LoRA 적용 강도 (0.8~1.2, 기본 1.0)

        [S2-1] character_style_prompt="background_only" 전달 시:
          - 캐릭터 묘사 일절 미주입
          - BACKGROUND_ONLY_STYLE 수식어만 추가
          - 캐릭터 라이브러리 overlay 합성에 사용되는 순수 배경 이미지 생성
        """
        char_style = kwargs.get("character_style_prompt", "")
        is_background_only = (char_style == BACKGROUND_ONLY_TRIGGER)

        # [Sprint 3] LoRA 파라미터 추출
        lora_model_id = kwargs.get("lora_model_id")
        lora_trigger_word = kwargs.get("lora_trigger_word", "")
        lora_scale = float(kwargs.get("lora_scale", 1.0))

        # [S2-1] 배경 전용 모드
        if is_background_only:
            is_english = all(ord(c) < 128 for c in prompt.replace(" ", "").replace(",", "").replace(".", ""))
            if not is_english or len(prompt) < 20:
                section = kwargs.get("section", "financial")
                keyword = kwargs.get("keyword", "stock market")
                base_prompt = f"Financial news background scene for {keyword}, {section} theme. " + BACKGROUND_ONLY_STYLE
            else:
                base_prompt = prompt + ", " + BACKGROUND_ONLY_STYLE
            logger.info(f"[배경전용] 이미지 생성 요청: prompt_len={len(base_prompt)}")

        # 기존 캐릭터 포함 모드
        else:
            # LoRA 모델이 있으면 CHARACTER_STYLE 프롬프트 주입 불필요
            # (LoRA 자체가 캐릭터 외형 정보를 보유)
            if lora_model_id:
                char_prompt = ""  # LoRA가 캐릭터를 담당
            elif char_style == "none" or char_style == "disable":
                char_prompt = ""
            elif char_style:
                char_prompt = char_style
            else:
                char_prompt = CHARACTER_STYLE

            # SceneSpec owns the mascot and medium. Never prepend the legacy
            # teal-card mascot to a locked Goldie scene: it creates the exact
            # mixed-character, pasted-together look this pipeline replaces.
            is_directed_editorial_prompt = bool(kwargs.get("style_locked")) or "Editorial scene family:" in prompt or "original 2D Korean finance comic" in prompt
            is_english = all(ord(c) < 128 for c in prompt.replace(" ", "").replace(",", "").replace(".", ""))
            if is_directed_editorial_prompt:
                # The scene director already specified the visual language. Do
                # not overwrite it with the old generic dark-blue 3D template.
                base_prompt = (char_prompt + prompt) if char_prompt and char_prompt not in prompt else prompt
            elif not is_english or len(prompt) < 30:
                section = kwargs.get("section", "default")
                keyword = kwargs.get("keyword", "stock market KOSPI")
                base_prompt = f"A scene representing {keyword} and {section}. " + char_prompt + FINANCE_STYLE
            else:
                base_prompt = prompt
                if char_prompt and "banknote" not in base_prompt.lower() and "coin" not in base_prompt.lower():
                    base_prompt = char_prompt + base_prompt
                if "vector" not in base_prompt.lower() and "cartoon" not in base_prompt.lower():
                    base_prompt = base_prompt + ", " + FINANCE_STYLE

            # [Sprint 3] LoRA trigger_word 프롬프트 앞에 삽입
            if lora_model_id and lora_trigger_word:
                base_prompt = f"{lora_trigger_word}, " + base_prompt
                logger.info(f"[LoRA] trigger_word='{lora_trigger_word}' 프롬프트에 삽입")

            logger.info(f"NanaBanana 이미지 생성 요청: prompt_len={len(base_prompt)}, lora={bool(lora_model_id)}")

        # 디렉토리 생성
        if (
            not is_background_only
            and not kwargs.get("suppress_legacy_style_lock", False)
            and STYLE_LOCK not in base_prompt
        ):
            base_prompt = STYLE_LOCK + "\n" + base_prompt
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 공급자 선택. 2026-09-29: 회사 Gemini 계정이 무기명 카드라 결제 등록이
        # Google 정책상 구조적으로 불가능해, OpenAI를 메인으로 하고 Fal/Gemini를
        # fallback으로 쓰기로 했었다(사용자 승인, 채널 캐릭터 참조 이미지 파일럿 통과).
        # 2026-10-01: 회사가 본인 명의 카드로 Gemini 결제 등록을 다시 완료해
        # 쿼터가 복구됐다. Gemini를 메인으로 되돌리고, OpenAI는 삭제하지 않고
        # 보류(fallback) 기능으로 유지한다(사용자 결정).
        provider_preference = str(kwargs.get("image_provider", "gemini")).lower()

        openai_key = os.getenv("OPENAI_API_KEY")
        fal_key = os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        gemini_model = str(kwargs.get("gemini_model") or "gemini-3-pro-image")
        gemini_image_size = str(kwargs.get("gemini_image_size") or "2K")
        gemini_service_tier = str(kwargs.get("gemini_service_tier") or "priority").lower()
        gemini_thinking_level = kwargs.get("gemini_thinking_level")

        def try_openai() -> bool:
            if not openai_key:
                return False
            try:
                character_image_paths = kwargs.get("character_image_paths") or []
                if not character_image_paths and kwargs.get("character_image_path"):
                    character_image_paths = [kwargs.get("character_image_path")]
                if self._generate_openai_image(
                    base_prompt, output_path, openai_key, character_image_paths,
                    job_id=kwargs.get("openai_job_id"),
                    scene_key=kwargs.get("openai_scene_key"),
                ):
                    logger.info(f"OpenAI 이미지 생성 성공: {output_path}")
                    return True
            except ProviderRequestBudgetExceeded:
                raise
            except Exception as e:
                logger.warning(f"OpenAI 이미지 생성 실패: {e}")
            return False

        def try_fal() -> bool:
            if not fal_key or self.__class__._fal_disabled:
                return False
            try:
                if lora_model_id:
                    if self._generate_fal_flux_lora(
                        base_prompt, output_path, fal_key,
                        lora_model_id, lora_scale
                    ):
                        logger.info(f"Fal.ai Flux-LoRA 이미지 생성 성공: {output_path}")
                        return True
                else:
                    if self._generate_fal_flux(base_prompt, output_path, fal_key):
                        logger.info(f"Fal.ai Flux 이미지 생성 성공: {output_path}")
                        return True
            except Exception as e:
                logger.warning(f"Fal.ai 이미지 생성 실패: {e}")
            return False

        def try_gemini() -> bool:
            if not gemini_key or self.__class__._gemini_disabled:
                return False
            try:
                character_image_paths = kwargs.get("character_image_paths") or []
                if not character_image_paths and kwargs.get("character_image_path"):
                    character_image_paths = [kwargs.get("character_image_path")]
                if not character_image_paths:
                    try:
                        from app.v5.providers.gemini_provider import _load_default_references
                        character_image_paths = _load_default_references()
                    except Exception as e:
                        logger.warning(f"기본 참조 자산 로드 실패: {e}")
                if self._generate_gemini_api(
                    base_prompt, output_path, gemini_key, character_image_paths,
                    model=gemini_model, image_size=gemini_image_size,
                    service_tier=gemini_service_tier,
                    thinking_level=gemini_thinking_level,
                    max_attempts=kwargs.get("gemini_max_attempts"),
                    retry_base_seconds=kwargs.get("gemini_retry_base_seconds"),
                    request_audit=kwargs.get("gemini_request_audit"),
                    reference_contract_declared=bool(kwargs.get("gemini_reference_contract_declared", False)),
                ):
                    logger.info(f"공식 Gemini API 이미지 생성 성공: model={gemini_model}, size={gemini_image_size}, path={output_path}")
                    return True
            except GeminiImageGenerationError as e:
                # 승인된 Gemini 모델은 공급자 오류를 숨기거나 무료 모델로
                # 우회하지 않고 호출자에게 그대로 전달한다.
                if provider_preference == "gemini" and gemini_model in {
                    "gemini-3-pro-image", "gemini-3.1-flash-image",
                }:
                    raise
                logger.warning(f"공식 Gemini API 호출 실패 (GeminiImageGenerationError): {e}")
            except (ProviderRequestBudgetExceeded, ImageRequestHeld):
                # Gemini가 명시적으로 선택된 공급자일 때는 예산 게이트가 막은
                # 경우 다른 제공자로 조용히 우회하거나 같은 장면을 재시도하지
                # 않는다(호출 자체가 승인되지 않은 상태). 반면 OpenAI/Fal이
                # 먼저 실패해 Gemini가 마지막 fallback으로 시도된 경우엔, 이
                # 예산 게이트 오류도 다음 폴백(무료 폴백)으로 넘어갈 실패
                # 신호일 뿐이라 그대로 재발생시키지 않는다.
                if provider_preference == "gemini":
                    raise
                logger.warning("Gemini 예산/요청 게이트로 이 fallback 시도를 건너뜁니다.")
            except Exception as e:
                logger.warning(f"공식 Gemini API 호출 실패: {e}")
            return False

        # Gemini는 캐릭터 참조/일관성 씬의 기본값이다. Fal은 배경, LoRA 또는
        # 명시적 선택 시 우선 사용한다. 실패하면 반대 제공자로만 폴백한다.
        # A Pro-quality run must not silently downgrade to a different model.
        # The caller will fail the job instead of rendering blank/text fallback
        # scenes when Gemini Pro cannot return an image.
        #
        # 2026-09-29 예외: Gemini 계정 자체가 막혀 있으면(PERMISSION_DENIED/
        # RESOURCE_EXHAUSTED — 결제 등록 불가 등 구조적 계정 문제) 재시도해도
        # 절대 풀리지 않는다. 이런 계정 수준 오류일 때만, 사용자가 명시적으로
        # 승인한 대로 Fal.ai로 대체 생성한다. 5xx 같은 일시적 오류는 기존
        # 예산/쿼터 재시도 계약을 그대로 따르며 이 대체 대상이 아니다.
        if provider_preference == "gemini" and gemini_model in {
            "gemini-3-pro-image", "gemini-3.1-flash-image",
        }:
            try:
                if try_gemini():
                    return output_path
            except ImageRequestHeld as e:
                if _is_account_level_gemini_error(e) and try_fal():
                    logger.warning(
                        f"Gemini 계정 오류({e})로 이 장면만 Fal.ai로 대체 생성: {output_path}"
                    )
                    return output_path
                raise
            raise RuntimeError(
                f"Gemini image generation returned no image after retries: {gemini_model}; "
                "refusing untracked fallback"
            )

        # 2026-10-01: Gemini 결제 복구로 다시 메인이다. Fal → OpenAI 순으로
        # 대체한다(OpenAI는 삭제하지 않고 보류 fallback으로 유지).
        # provider_preference로 다른 공급자를 먼저 요청하면 그 공급자를
        # 앞세우고 나머지를 같은 상대 순서로 뒤에 붙인다.
        _DEFAULT_ORDER = ("gemini", "fal", "openai")
        if provider_preference in _DEFAULT_ORDER:
            order = (provider_preference, *(p for p in _DEFAULT_ORDER if p != provider_preference))
        else:
            order = _DEFAULT_ORDER
        logger.info(f"이미지 공급자 선택: requested={provider_preference}, order={order}")
        for provider_name in order:
            try:
                if provider_name == "openai" and try_openai():
                    return output_path
                if provider_name == "fal" and try_fal():
                    return output_path
                if provider_name == "gemini" and try_gemini():
                    return output_path
            except (ProviderRequestBudgetExceeded, ImageRequestHeld) as e:
                logger.warning(f"{provider_name} 예산/요청 게이트로 건너뜀: {e}")
                continue

        # 최후의 무료 폴백은 생성 방법을 메타데이터로 남겨 검수 화면에서
        # AI 고품질 결과와 혼동되지 않게 한다.
        return self._generate_pollinations(base_prompt, output_path)

    def _generate_fal_flux_lora(self, prompt: str, output_path: str,
                                fal_key: str, lora_model_id: str,
                                lora_scale: float = 1.0) -> bool:
        """
        [Sprint 3] Fal.ai flux-lora 엔드포인트로 LoRA 적용 이미지 생성.

        검증된 파라미터 (2025-2026 스펙):
          - model: "fal-ai/flux-lora"
          - loras: [{"path": safetensors_url, "scale": 0.8~1.2}]
          - prompt: trigger_word가 앞에 삽입된 완성 프롬프트
        """
        import requests
        model_id = "fal-ai/flux-lora"
        submit_url = f"https://queue.fal.run/{model_id}"
        headers = {
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "prompt": prompt,
            "image_size": "landscape_16_9",
            "num_inference_steps": 28,  # flux-lora 권장값
            "guidance_scale": 3.5,
            "loras": [
                {
                    "path": lora_model_id,
                    "scale": lora_scale,
                }
            ],
            "sync_mode": True,
        }

        logger.info(
            f"[LoRA 추론] fal-ai/flux-lora 요청: "
            f"lora_scale={lora_scale}, prompt_len={len(prompt)}"
        )
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=60)

            if resp.status_code == 403:
                logger.error(f"Fal.ai 계정 잠김/잔액 부족 (403): {resp.text[:200]}")
                self.__class__._fal_disabled = True
                return False

            if resp.status_code == 200:
                resp_json = resp.json()
                images = resp_json.get("images", [])
                if images:
                    img_url = images[0].get("url")
                    if img_url:
                        img_bytes = requests.get(img_url, timeout=30).content
                        with open(output_path, "wb") as f:
                            f.write(img_bytes)
                        logger.info(f"[LoRA 추론] 이미지 생성 완료: {output_path}")
                        return True

            # 비동기 폴백
            payload["sync_mode"] = False
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 403:
                self.__class__._fal_disabled = True
                return False
            if resp.status_code != 200:
                logger.warning(f"flux-lora 제출 실패 ({resp.status_code}): {resp.text[:200]}")
                return False

            resp_json = resp.json()
            request_id = resp_json.get("request_id")
            if not request_id:
                return False

            import time
            status_url = f"https://queue.fal.run/{model_id}/requests/{request_id}/status"
            result_url = f"https://queue.fal.run/{model_id}/requests/{request_id}"
            for _ in range(20):
                time.sleep(2)
                st = requests.get(status_url, headers=headers, timeout=15)
                if st.status_code not in (200, 202):
                    continue
                status = st.json().get("status")
                if status == "COMPLETED":
                    res = requests.get(result_url, headers=headers, timeout=15)
                    if res.status_code == 200:
                        images = res.json().get("images", [])
                        if images:
                            img_url = images[0].get("url")
                            if img_url:
                                img_bytes = requests.get(img_url, timeout=30).content
                                with open(output_path, "wb") as f:
                                    f.write(img_bytes)
                                return True
                    return False
                elif status in ("FAILED", "CANCELLED"):
                    return False
            return False
        except Exception as e:
            logger.error(f"[LoRA 추론] Fal.ai flux-lora 예외: {e}")
            return False

    def _generate_fal_flux(self, prompt: str, output_path: str, fal_key: str) -> bool:
        """
        Fal.ai HTTP Queue API를 통해 Flux Schnell 이미지 생성.
        """
        import requests
        import time
        model_id = "fal-ai/flux/schnell"
        submit_url = f"https://queue.fal.run/{model_id}"
        headers = {
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "prompt": prompt,
            "image_size": "landscape_16_9",
            "sync_mode": True
        }
        
        logger.info(f"Fal.ai Flux 이미지 생성 요청 시작: prompt_len={len(prompt)}")
        try:
            # 동기 모드로 즉시 생성 시도
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=20)

            # [신규] 계정 잠김/잔액 부족 감지 → 서킷 브레이커 즉시 작동
            if resp.status_code == 403:
                logger.error(f"Fal.ai 계정 잠김/잔액 부족 감지 (403): {resp.text[:200]}")
                self.__class__._fal_disabled = True
                logger.warning(
                    "Fal.ai 서킷 브레이커 작동 — 이 프로세스가 살아있는 동안 "
                    "이후 이미지 생성은 Fal.ai를 건너뛰고 Gemini/Pollinations로 바로 진행합니다."
                )
                return False

            if resp.status_code == 200:
                resp_json = resp.json()
                images = resp_json.get("images", [])
                if images:
                    img_url = images[0].get("url")
                    if img_url:
                        img_bytes = requests.get(img_url, timeout=30).content
                        with open(output_path, "wb") as f:
                            f.write(img_bytes)
                        return True
            
            # 비동기 대기 모드로 재시도
            payload["sync_mode"] = False
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=30)

            if resp.status_code == 403:
                logger.error(f"Fal.ai 계정 잠김/잔액 부족 감지 (403, 비동기 모드): {resp.text[:200]}")
                self.__class__._fal_disabled = True
                logger.warning("Fal.ai 서킷 브레이커 작동 — 이후 이미지 생성은 Gemini/Pollinations로 바로 진행합니다.")
                return False

            if resp.status_code != 200:
                logger.warning(f"Fal.ai Flux 제출 실패 ({resp.status_code}): {resp.text}")
                return False
                
            resp_json = resp.json()
            request_id = resp_json.get("request_id")
            if not request_id:
                return False
                
            status_url = resp_json.get("status_url") or f"https://queue.fal.run/{model_id}/requests/{request_id}/status"
            result_url = resp_json.get("response_url") or f"https://queue.fal.run/{model_id}/requests/{request_id}"
            
            for i in range(10):
                time.sleep(1.5)
                status_resp = requests.get(status_url, headers=headers, timeout=15)
                if status_resp.status_code not in (200, 202):
                    continue
                status_data = status_resp.json()
                status = status_data.get("status")
                if status == "COMPLETED":
                    res_resp = requests.get(result_url, headers=headers, timeout=15)
                    if res_resp.status_code == 200:
                        res_data = res_resp.json()
                        images = res_data.get("images", [])
                        if images:
                            img_url = images[0].get("url")
                            if img_url:
                                img_bytes = requests.get(img_url, timeout=30).content
                                with open(output_path, "wb") as f:
                                    f.write(img_bytes)
                                return True
                    return False
                elif status in ("FAILED", "CANCELLED"):
                    return False
            return False
        except Exception as e:
            logger.error(f"Fal.ai Flux API 예외 발생: {e}")
            return False

    # 2026-09-29: OpenAI 공식 문서 기준 gpt-image-1의 1536x1024 장당 단가는
    # low $0.016 / medium $0.063 / high $0.25다. quality를 명시하지 않아 실제
    # 등급을 예측할 수 없으므로, 사전 예산 체크는 안전하게 high 상한을 쓴다.
    # (검증 안 된 추정치이므로 실제 청구액은 응답 usage 기준으로 별도 기록한다.)
    _OPENAI_IMAGE_ESTIMATED_USD = 0.25
    # 2026-09-29: 실측 청구 근거. gpt-image-2.5 계열 공식 문서는 출력 토큰당
    # $30/백만으로 명시하지만, gpt-image-1 자체의 토큰 단가는 공식 문서에서
    # 확인하지 못했다 — 같은 값을 쓴 최선의 추정치이며, 실제 결제 내역과
    # 다를 수 있다는 점을 명시적으로 남긴다(AGENTS.md: 추측 금지 원칙).
    _OPENAI_OUTPUT_TOKEN_USD_PER_MILLION = 30.0

    def _generate_openai_image(self, prompt: str, output_path: str, api_key: str,
                               character_image_paths: list[str] | None = None,
                               model: str = "gpt-image-1", size: str = "1536x1024",
                               job_id: int | None = None, scene_key: str | None = None) -> bool:
        """OpenAI Images API로 이미지를 생성한다.

        참조 이미지가 있으면 /v1/images/edits(이미지 기반 수정)를, 없으면
        /v1/images/generations을 쓴다. 2026-09-29 파일럿에서 채널 캐릭터
        참조 이미지 기반 결과가 얼굴·배경 모두 사용자 승인을 받았다.

        2026-09-29: 예산 게이트가 없어 1분 테스트 job 하나에 $10 넘게
        청구된 사고가 있었다(크레딧이 바닥날 때까지 계속 재시도). job_id가
        주어지면 요청 직전 영상 전체 예산(₩40,000/₩70,000)을 확인하고,
        성공 후 실측 비용을 같은 원장에 기록한다.
        """
        import requests

        if job_id is not None and not can_charge_openai_image(job_id, self._OPENAI_IMAGE_ESTIMATED_USD):
            raise ProviderRequestBudgetExceeded(
                f"OpenAI 이미지 예산 초과 예상(추정 ${self._OPENAI_IMAGE_ESTIMATED_USD}/장): job={job_id}"
            )

        headers = {"Authorization": f"Bearer {api_key}"}
        reference_paths = [p for p in (character_image_paths or []) if p and os.path.exists(p)]

        for attempt in range(2):
            try:
                if reference_paths:
                    with open(reference_paths[0], "rb") as f:
                        files = {"image": ("reference.png", f, "image/png")}
                        data = {"model": model, "prompt": prompt, "size": size, "n": "1"}
                        resp = requests.post(
                            "https://api.openai.com/v1/images/edits",
                            headers=headers, files=files, data=data, timeout=120,
                        )
                else:
                    resp = requests.post(
                        "https://api.openai.com/v1/images/generations",
                        headers=headers, json={"model": model, "prompt": prompt, "size": size, "n": 1},
                        timeout=120,
                    )
            except requests.RequestException as e:
                logger.warning(f"OpenAI Images API 네트워크 오류: {e}")
                return False

            if resp.status_code == 200:
                body = resp.json()
                item = (body.get("data") or [{}])[0]
                b64 = item.get("b64_json")
                if not b64:
                    logger.warning("OpenAI Images API 응답에 이미지 데이터 없음")
                    return False
                with open(output_path, "wb") as out:
                    out.write(base64.b64decode(b64))
                if job_id is not None:
                    usage = body.get("usage") or {}
                    output_tokens = int(usage.get("output_tokens") or 0)
                    actual_usd = (output_tokens / 1_000_000) * self._OPENAI_OUTPUT_TOKEN_USD_PER_MILLION
                    try:
                        record_openai_image_cost(job_id, actual_usd, scene_key=scene_key)
                    except Exception as e:
                        logger.warning(f"OpenAI 이미지 비용 기록 실패(생성은 성공): {e}")
                return True

            # 401/403: 계정·키 문제. 재시도해도 풀리지 않으므로 즉시 다음
            # 공급자로 넘어간다.
            if resp.status_code in (401, 403):
                logger.error(f"OpenAI 계정/권한 오류 ({resp.status_code}): {resp.text[:200]}")
                return False
            # 429: 요청 한도/쿼터. 이 공급자만의 문제로 기록하고 다음 공급자로.
            if resp.status_code == 429:
                logger.warning(f"OpenAI 요청 한도 초과 (429): {resp.text[:200]}")
                return False
            # 5xx: 일시적 오류. 한 번만 짧게 재시도한 뒤 다음 공급자로 넘어간다.
            if resp.status_code >= 500 and attempt == 0:
                logger.warning(f"OpenAI 일시적 오류 ({resp.status_code}), 재시도: {resp.text[:200]}")
                continue
            logger.warning(f"OpenAI Images API 실패 ({resp.status_code}): {resp.text[:200]}")
            return False
        return False

    @staticmethod
    def _extract_interaction_image(response: dict) -> str | None:
        """Read image data from either GenerateContent or Interactions responses."""
        for candidate in response.get("candidates") or []:
            for block in (candidate.get("content") or {}).get("parts") or []:
                inline = block.get("inlineData") or block.get("inline_data") or {}
                if inline.get("data"):
                    return inline["data"]
        output_image = response.get("output_image") or response.get("outputImage") or {}
        if isinstance(output_image, dict) and output_image.get("data"):
            return output_image["data"]
        for step in response.get("steps") or []:
            for block in step.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "image" and block.get("data"):
                    return block["data"]
        return None

    def _generate_gemini_api(
        self, prompt: str, output_path: str, api_key: str,
        character_image_paths: list[str] | None = None, *, model: str, image_size: str,
        service_tier: str = "standard",
        thinking_level: str | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        request_audit=None,
        reference_contract_declared: bool = False,
    ) -> bool:
        """한 번의 GenerateContent POST만 수행한다. 재시도 시각/한도는 영속 감사 객체가 소유한다."""
        import requests
        import time

        if request_audit is None:
            raise ImageRequestHeld("영속 요청 감사 객체 없음; 유료 POST 차단")

        if model not in {"gemini-3-pro-image", "gemini-3.1-flash-image"}:
            raise ValueError(f"Unsupported Gemini image model: {model}")
        if image_size not in {"1K", "2K", "4K"}:
            image_size = "1K"
        if thinking_level is not None:
            thinking_level = str(thinking_level).strip().lower()
        if model == "gemini-3.1-flash-image":
            if thinking_level not in {None, "minimal", "high"}:
                raise ValueError("Flash thinking level은 minimal/high만 허용합니다.")
        elif thinking_level is not None:
            raise ValueError("thinking level 명시는 gemini-3.1-flash-image에서만 허용합니다.")

        input_parts: list[dict] = []
        # V5 벤치마크는 캐릭터·스타일·구도 가이드 3장을 의도적으로 함께 쓴다.
        # Gemini Pro가 허용하는 참조 이미지 범위 내에서 기존 2장 제한을 확장한다.
        reference_paths = [path for path in (character_image_paths or []) if path and os.path.exists(path)][:3]
        has_channel_reference_contract = any(
            Path(path).name == "channel_character_face_range_v2.png"
            or Path(path).name.startswith("channel_character_face_scene")
            or Path(path).name.startswith("channel_style_")
            for path in reference_paths
        )
        if reference_paths and (reference_contract_declared or has_channel_reference_contract):
            # 테스트용 Gemini 래퍼와 영상 생성 버튼의 운영 경로가 동일한
            # 얼굴·화풍·참조 순서 계약을 사용해야 한다. 전송 직전 한 곳에서
            # 보강하므로 병렬/직렬/국소 재생성뿐 아니라 구형 장면의 재개
            # 경로도 서로 달라지지 않는다.
            from app.v5.providers.gemini_provider import ensure_gemini_reference_contract
            prompt = ensure_gemini_reference_contract(prompt, reference_paths)
        # 호출 payload의 이미지 순서는 V5 계약과 동일하게 보존한다.
        # 이전 구현은 style/layout을 먼저 넣어 프롬프트의 character→style→layout
        # 설명과 실제 이미지 번호가 서로 달랐다.
        for reference_path in reference_paths:
            try:
                with open(reference_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode()
                mime = "image/png" if reference_path.lower().endswith(".png") else "image/jpeg"
                input_parts.append({"inlineData": {"mimeType": mime, "data": encoded}})
            except Exception as exc:
                logger.warning("캐릭터 참조 이미지 로드/인코딩 실패: %s", exc)
        if reference_paths and not reference_contract_declared:
            prompt = (
                "Use the first attached image as the fixed channel character identity. Preserve its face, "
                "silhouette, color palette and line style. Do not add a second mascot.\n\n" + prompt
            )
        input_parts.append({"text": prompt})

        payload = {
            "contents": [{"parts": input_parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "16:9", "imageSize": image_size},
            },
        }
        if model == "gemini-3.1-flash-image" and thinking_level is not None:
            payload["generationConfig"]["thinkingConfig"] = {
                # GenerateContent REST 공식 예시는 enum 표기를 High/Minimal로
                # 보낸다. 내부 계약은 비교하기 쉬운 소문자로 유지하되 실제
                # 공급자 payload만 공식 표기로 정규화한다.
                "thinkingLevel": {"minimal": "Minimal", "high": "High"}[thinking_level],
            }
        # Priority is an explicit caller choice for urgent Pro renders. Keep
        # standard as the default because it carries a premium price.
        if service_tier in {"priority", "flex"}:
            # GenerateContent의 serviceTier는 generationConfig 내부가 아니라
            # 요청 본문의 최상위 필드다. 잘못 중첩하면 우선 처리 요청이 조용히
            # 무시되거나 잘못된 요청으로 거절될 수 있다.
            payload["serviceTier"] = service_tier
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        is_pro = model == "gemini-3-pro-image"
        # 기존 호출 시그니처는 유지하지만 공급자 내부 attempt/base 카운터는 사용하지 않는다.
        timeout_name = "GEMINI_PRO_REQUEST_TIMEOUT_SECONDS" if is_pro else "GEMINI_IMAGE_REQUEST_TIMEOUT_SECONDS"
        try:
            request_timeout_seconds = float(os.getenv(timeout_name, "300" if is_pro else "180"))
        except ValueError as exc:
            raise ValueError(f"{timeout_name}는 초 단위 양의 숫자여야 합니다.") from exc
        request_timeout_seconds = min(900.0, max(30.0, request_timeout_seconds))
        server_timeout_name = (
            "GEMINI_PRO_SERVER_TIMEOUT_SECONDS"
            if is_pro else "GEMINI_IMAGE_SERVER_TIMEOUT_SECONDS"
        )
        try:
            # Pro 2K 요청은 참조 이미지와 장면 계약을 함께 보낼 때 240초를
            # 넘겨 완료되는 사례가 있다. 클라이언트 기본 제한(300초)보다
            # 15초만 짧게 두어 서버가 정상 결과를 만들 시간을 보장한다.
            server_timeout_seconds = float(os.getenv(server_timeout_name, "285" if is_pro else "150"))
        except ValueError as exc:
            raise ValueError(f"{server_timeout_name}는 초 단위 양의 숫자여야 합니다.") from exc
        server_timeout_seconds = min(
            max(30.0, server_timeout_seconds),
            max(30.0, request_timeout_seconds - 5.0),
        )
        # 서버 기본 대기 시간보다 클라이언트 제한이 먼저 끝나면 응답 코드와
        # 요청 ID 없이 ReadTimeout만 남는다. 공식 REST 계약의 서버 제한 힌트를
        # 함께 보내 504/503을 구조적으로 분류하고 중첩 재시도를 막는다.
        headers["X-Server-Timeout"] = str(int(server_timeout_seconds))
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        evidence = payload_evidence(payload, endpoint=endpoint, model=model, tier=service_tier)
        token = request_audit.before_attempt(attempt=1, model=model, evidence=evidence)
        started = time.monotonic()
        try:
            request_audit.assert_dispatchable(token)
        except ImageRequestHeld:
            request_audit.after_attempt(token, outcome="not_dispatched")
            raise
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=(20.0, request_timeout_seconds))
        except requests.RequestException as exc:
            state = request_audit.after_attempt(token, outcome="network_error", retryable=True,
                error_code=type(exc).__name__, duration_seconds=time.monotonic() - started)
            raise ImageRequestHeld("네트워크 오류", status=state["status"], next_allowed_at=state["next_allowed_at"]) from exc

        request_id = response.headers.get("x-goog-request-id") or response.headers.get("x-request-id") or response.headers.get("request-id")
        # 이미지 디코딩 실패/비정상 응답이어도 도착한 사용량은 해당 시도에 남긴다.
        # 전체 응답 대신 사용량 객체만 보존하고, 필드 누락을 사용량 0으로 해석하지 않는다.
        try:
            response_body = response.json()
        except ValueError:
            response_body = None
        usage_present = isinstance(response_body, dict) and "usageMetadata" in response_body
        usage_metadata = response_body.get("usageMetadata") if usage_present else None
        usage_status = "present" if isinstance(usage_metadata, dict) else "invalid" if usage_present else "absent"
        common = {"status_code": response.status_code, "request_id": request_id,
                  "service_tier_observed": response.headers.get("x-gemini-service-tier"),
                  "duration_seconds": time.monotonic() - started,
                  "usage_metadata": usage_metadata if isinstance(usage_metadata, dict) else None,
                  "usage_metadata_status": usage_status}
        if response.status_code == 200:
            try:
                encoded = self._extract_interaction_image(response_body)
                if not encoded:
                    raise ValueError("이미지 출력 없음")
                image_bytes = base64.b64decode(encoded)
                from io import BytesIO
                from PIL import Image
                Image.open(BytesIO(image_bytes)).convert("RGB").save(output_path, "PNG")
            except Exception as exc:
                state = request_audit.after_attempt(token, outcome="invalid_image", retryable=True,
                    error_code=type(exc).__name__, **common)
                raise ImageRequestHeld("HTTP 200 이미지 해석 실패", status=state["status"], next_allowed_at=state["next_allowed_at"]) from exc
            request_audit.after_attempt(token, outcome="http_200", image_sha256=digest(Path(output_path).read_bytes()), **common)
            return True

        # 공급자 메시지 원문은 민감정보를 되비출 수 있으므로 오류 코드만 감사한다.
        try:
            error_code = str(response_body.get("error", {}).get("status", ""))[:80]
        except Exception:
            error_code = ""
        lower = response.text.lower()
        quota = response.status_code == 429 and any(term in lower for term in ("spending cap", "daily quota", "prepayment credits", "depleted"))
        retryable = response.status_code in {429, 500, 502, 503, 504} and not quota
        state = request_audit.after_attempt(token, outcome=f"http_{response.status_code}",
            retryable=retryable, permanent=not retryable, retry_after=response.headers.get("Retry-After"),
            error_code=error_code, **common)
        raise ImageRequestHeld(f"HTTP {response.status_code} {error_code}", status=state["status"], next_allowed_at=state["next_allowed_at"])

    def _generate_pollinations(self, prompt: str, output_path: str) -> str:
        """
        Pollinations.ai 기반 무료 AI 이미지 생성 (폴백).
        """
        encoded = urllib.parse.quote(prompt)
        url = f"{self.fallback_url}/{encoded}?width={self.width}&height={self.height}&nologo=true&seed={hash(prompt) % 100000}"

        req = urllib.request.Request(url, headers={
            "User-Agent": "VideoPipeline/1.0"
        })
        with urllib.request.urlopen(req, timeout=30) as response:
            image_data = response.read()

        if len(image_data) < 1000:
            raise ValueError(f"이미지 크기 비정상: {len(image_data)} bytes")

        with open(output_path, "wb") as f:
            f.write(image_data)

        logger.info(f"NanaBanana(Pollinations) 이미지 저장 완료: {output_path} ({len(image_data)/1024:.1f}KB)")
        return output_path
