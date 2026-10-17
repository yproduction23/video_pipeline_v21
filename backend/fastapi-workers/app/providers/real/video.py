"""
Kling AI Video Provider — Official Kling 3.0 API + FFmpeg fallback

1. 공식 API 연동 (Kling 3.0):
   - KLING_ACCESS_KEY / KLING_SECRET_KEY (또는 KLING_API_KEY) 설정 시 공식 API 호출
   - 오프닝(8초) 및 씬 전환(5초) 고품질 AI 모션 영상 클립 생성
   
2. 무료 폴백 엔진:
   - API 키 미설정 시 정적 PNG를 5초짜리 MP4 모션 클립(줌인 효과)으로 변환하는 FFmpeg 폴백 사용 ($0)
"""
import os
import time
import logging
import requests
import tempfile
import base64
import hashlib
import hmac
import json
from pathlib import Path

from app.providers.base import VideoProvider, GeneratedAsset

logger = logging.getLogger(__name__)

FAL_POLL_TIMEOUT_SEC = 180


class KlingProvider(VideoProvider):
    """
    Kling 3.0 (Official API) 및 FFmpeg 기반 비디오 클립 생성 프로바이더.
    """

    def __init__(self):
        self.base_url = "https://api-singapore.klingai.com"
        self.resolved_token = None

    def _generate_jwt(self, access_key: str, secret_key: str) -> str:
        """
        Kling Access Key와 Secret Key를 사용하여 HS256 JWT 인증 토큰을 생성한다.
        (외부 라이브러리 의존성 없이 Python 내장 라이브러리 사용)
        """
        # Header
        header = {"alg": "HS256", "typ": "JWT"}
        header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode('utf-8')).decode('utf-8').rstrip('=')
        
        # Payload
        now = int(time.time())
        payload = {
            "iss": access_key,
            "exp": now + 1800,  # 30분 유효
            "nbf": now - 5      # 5초 전부터 유효
        }
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).decode('utf-8').rstrip('=')
        
        # Signature
        signature_input = f"{header_b64}.{payload_b64}".encode('utf-8')
        signature = hmac.new(
            secret_key.encode('utf-8'),
            signature_input,
            hashlib.sha256
        ).digest()
        signature_b64 = base64.urlsafe_b64encode(signature).decode('utf-8').rstrip('=')
        
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def _request_kling_with_fallback(self, method: str, endpoint_path: str, payload: dict, api_key: str) -> requests.Response:
        """
        Kling API 요청 시 글로벌/중국 도메인 및 JWT/Bearer 인증 방식을 교차 시도하는 하이브리드 폴백 메커니즘.
        """
        # 4가지 퍼뮤테이션 정의: (base_url, use_jwt)
        permutations = [
            ("https://api-singapore.klingai.com", True),
            ("https://api.klingai.com", True),
            ("https://api-singapore.klingai.com", False),
            ("https://api.klingai.com", False),
        ]
        
        last_resp = None
        for base_url, use_jwt in permutations:
            # 토큰 결정
            if use_jwt:
                if not api_key or "_" not in api_key:
                    continue  # JWT 발급 불가
                try:
                    ak, sk = api_key.split("_", 1)
                    token = self._generate_jwt(ak, sk)
                except Exception as e:
                    logger.warning(f"JWT 토큰 생성 실패 (도메인 {base_url} 시도 중): {e}")
                    continue
            else:
                token = api_key
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            url = f"{base_url}{endpoint_path}"
            logger.info(f"Kling API 요청 시도: url={url}, use_jwt={use_jwt}")
            
            try:
                if method.upper() == "POST":
                    resp = requests.post(url, json=payload, headers=headers, timeout=30)
                else:
                    resp = requests.get(url, headers=headers, timeout=30)
                
                # 성공 응답 판별 (HTTP 200 이고 Kling 내부 응답 코드 code == 0 이거나 정상 결과인 경우)
                if resp.status_code == 200:
                    resp_json = resp.json()
                    code = resp_json.get("code")
                    if code == 0 or code is None:
                        # 성공 시 이 base_url과 token 유형을 이 인스턴스에 고정하여 다음 폴링 시 재사용하도록 함
                        self.base_url = base_url
                        self.resolved_token = token
                        logger.info(f"Kling API 요청 성공: url={url}, use_jwt={use_jwt}")
                        return resp
                    else:
                        logger.warning(f"Kling API 응답 코드 오류 ({code}): {resp_json.get('message')}")
                else:
                    logger.warning(f"Kling API HTTP 오류 ({resp.status_code}): {resp.text}")
                
                last_resp = resp
            except Exception as e:
                logger.warning(f"Kling API 요청 예외 ({url}): {e}")
                
        # 모든 시도가 실패한 경우 마지막 응답 반환
        if last_resp is not None:
            return last_resp
        raise ValueError("모든 Kling API 인증 및 도메인 조합 시도가 실패했습니다.")

    def generate(self, prompt: str, duration: int = 5, **kwargs) -> GeneratedAsset:
        """
        텍스트 프롬프트 또는 이미지를 기반으로 5~8초 길이의 비디오 클립 생성.
        image_url이 제공되면 image-to-video를 우선 시도.
        """
        output_path = kwargs.get("output_path")
        if not output_path:
            output_path = tempfile.mktemp(suffix=".mp4")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        image_url = kwargs.get("image_url")

        logger.info(f"Kling 비디오 클립 생성 요청: duration={duration}s, prompt_len={len(prompt)}, image_url={'있음' if image_url else '없음'}")

        api_key = os.getenv("KLING_API_KEY") or os.getenv("KLING_ACCESS_KEY")
        fal_key = os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        fal_only = bool(kwargs.get("fal_only"))

        # 1. Fal.ai 연동이 있으면 최우선 실행 (image-to-video 및 text-to-video 모두 지원)
        if fal_key:
            try:
                negative_prompt = kwargs.get("negative_prompt")
                success, req_id = self._generate_fal_api(
                    prompt, output_path, duration, fal_key, image_url, negative_prompt=negative_prompt,
                    poll_timeout_sec=int(kwargs.get("poll_timeout_sec") or FAL_POLL_TIMEOUT_SEC),
                )
                if success:
                    logger.info(f"Fal.ai Kling 비디오 생성 성공: {output_path}")
                    return GeneratedAsset(
                        asset_type="video",
                        local_path=output_path,
                        duration=duration,
                        meta={"fal_request_id": req_id}
                    )
            except Exception as e:
                logger.warning(f"Fal.ai Kling 비디오 생성 실패, 공식 API 폴백 시도: {e}")

        if fal_only:
            raise RuntimeError("Fal image-to-video was unavailable; use static Pro image motion")

        # 2. 공식 Kling API
        # 2-1. image_url이 있으면 image-to-video API 우선 시도
        if image_url and api_key:
            try:
                if self._generate_kling_image2video(image_url, prompt, output_path, duration, api_key):
                    logger.info(f"Kling image-to-video API 비디오 생성 성공: {output_path}")
                    return GeneratedAsset(asset_type="video", local_path=output_path, duration=duration)
            except Exception as e:
                logger.warning(f"Kling image-to-video API 호출 실패, text2video 폴백 시도: {e}")

        # 2-2. 공식 Kling 3.0 text-to-video API 시도
        if api_key:
            try:
                if self._generate_kling_api(prompt, output_path, duration, api_key):
                    logger.info(f"공식 Kling 3.0 text2video API 비디오 생성 성공: {output_path}")
                    return GeneratedAsset(asset_type="video", local_path=output_path, duration=duration)
            except Exception as e:
                logger.warning(f"Kling text2video API 호출 실패, FFmpeg 모션 폴백: {e}")

        # 3. Safe static-image fallback.  No FFmpeg zoom/pan/transition is
        # allowed because numerical cards and subtitles must not jitter.
        image_path = kwargs.get("image_path")
        self._generate_ffmpeg_fallback(output_path, duration, image_path)
        return GeneratedAsset(asset_type="video", local_path=output_path, duration=duration)

    def _generate_fal_api(self, prompt: str, output_path: str, duration: int, fal_key: str, image_url: str = None, negative_prompt: str = None,
                          poll_timeout_sec: int = 180) -> tuple[bool, str | None]:
        """
        Fal.ai HTTP Queue API를 통해 Kling 비디오 클립 생성 (비동기 폴링).

        [리서치 반영 - 모델 변경] 기존 kling-video/v3는 멀티샷/네이티브오디오가
        강점인 고가 모델인데, 우리는 "고정 캐릭터의 미니멀한 손짓/표정 + 배경
        가벼운 움직임" 정도만 필요해서 과한 스펙이었습니다. Fal.ai 공식 문서와
        커뮤어티 벤치마크 기준 kling-video/v2.6/pro가 캐릭터 일관성 대비 비용이
        가장 좋아서 (image-to-video $0.07/초, audio off) 이걸로 교체합니다.
        generate_audio는 우리가 TTS를 별도로 입히므로 명시적으로 꺼서 불필요한
        2배 과금(오디오 켜면 $0.14/초)을 방지합니다.
        """
        model_id = "fal-ai/kling-video/v2.6/pro/image-to-video" if image_url else "fal-ai/kling-video/v1.6/pro/text-to-video"

        headers = {
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json"
        }
        submit_url = f"https://queue.fal.run/{model_id}"
        payload = {
            "prompt": prompt,
            # Fal v2.6 accepts an enum represented as a JSON string, not an
            # arbitrary integer duration.
            "duration": "10" if int(duration) >= 10 else "5",
            "generate_audio": False,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if image_url:
            # v2.6/v3 계열은 start_image_url 기준으로 aspect_ratio를 자동 추론하므로
            # 별도 지정 시 UI에서 무시됨 (공식 문서 기준) — 생략
            payload["start_image_url"] = image_url
        else:
            payload["aspect_ratio"] = "16:9"

        masked_payload = {
            **payload,
            "prompt": f"<masked:{len(prompt)} chars>",
            "start_image_url": (
                f"{payload['start_image_url'].split('?', 1)[0][:96]}<masked>"
                if payload.get("start_image_url") else None
            ),
        }
        logger.info(
            "Fal.ai request endpoint=%s payload=%s",
            model_id,
            json.dumps(masked_payload, ensure_ascii=False, sort_keys=True),
        )
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"Fal.ai 제출 실패 ({resp.status_code}): {resp.text}")
                return False, None
                
            resp_json = resp.json()
            request_id = resp_json.get("request_id")
            logger.info(
                "Fal.ai submit response=%s",
                json.dumps(
                    {"status_code": resp.status_code, "request_id": request_id},
                    ensure_ascii=False,
                ),
            )
            if not request_id:
                logger.warning(f"Fal.ai 응답에서 request_id 못찾음: {resp_json}")
                return False, None
                
            logger.info(f"Fal.ai 요청 성공: request_id={request_id}. 폴링을 시작합니다.")
            
            status_url = resp_json.get("status_url") or f"https://queue.fal.run/{model_id}/requests/{request_id}/status"
            result_url = resp_json.get("response_url") or f"https://queue.fal.run/{model_id}/requests/{request_id}"
            
            # 기본 180초(36회) 폴링. 슬라이드 Kling 후작업처럼 대기열이 긴 호출은 더 길게 기다린다.
            polls = max(1, int(poll_timeout_sec) // 5)
            for i in range(polls):
                time.sleep(5)
                status_resp = requests.get(status_url, headers=headers, timeout=30)
                if status_resp.status_code not in (200, 202):
                    logger.warning(f"Fal.ai 상태 조회 실패 ({status_resp.status_code}), 계속 시도...")
                    continue
                    
                status_data = status_resp.json()
                status = status_data.get("status")
                logger.info(f"Fal.ai 폴링 [{i+1}/{polls}] 상태: {status}")
                
                if status == "COMPLETED":
                    res_resp = requests.get(result_url, headers=headers, timeout=30)
                    if res_resp.status_code == 200:
                        res_data = res_resp.json()
                        video_url = res_data.get("video", {}).get("url")
                        if video_url:
                            logger.info(f"Fal.ai 비디오 생성 완료: {video_url}. 다운로드 중...")
                            video_bytes = requests.get(video_url, timeout=60).content
                            with open(output_path, "wb") as f:
                                f.write(video_bytes)
                            return True, request_id
                    logger.warning(f"Fal.ai 결과 조회 실패 ({res_resp.status_code})")
                    return False, request_id
                elif status in ("FAILED", "CANCELLED"):
                    logger.error(f"Fal.ai 생성 실패 상태: {status_data}")
                    return False, request_id
            logger.warning("Fal.ai 폴링 시간 초과 (%d초) request_id=%s — fal에서 계속 진행·과금될 수 있다", poll_timeout_sec, request_id)
            return False, request_id
        except Exception as e:
            logger.error(f"Fal.ai API 예외 발생: {e}")
            return False, None

    def _generate_kling_api(self, prompt: str, output_path: str, duration: int, api_key: str) -> bool:
        """
        Kling AI v1/videos/text2video API 호출 (비동기 폴링).
        """
        payload = {
            "model_name": "kling-v3",
            "prompt": prompt,
            "negative_prompt": "blurry, distorted, text artifacts, low quality, watermark, fast motion, camera movement, motion blur, extra limbs, face distortion, character deformation, walking, running, background people",
            "duration": str(duration),
            "aspect_ratio": "16:9",
            "mode": "pro"
        }

        # 1. 생성 요청 (Task 생성)
        resp = self._request_kling_with_fallback("POST", "/v1/videos/text2video", payload, api_key)
        if resp.status_code != 200 or resp.json().get("code") != 0:
            logger.warning(f"Kling text2video API 요청 실패: {resp.status_code} {resp.text}")
            return False

        task_id = resp.json().get("data", {}).get("task_id")
        if not task_id:
            return False

        # 2. 상태 폴링 (최대 120초 대기)
        poll_url = f"{self.base_url}/v1/videos/text2video/{task_id}"
        headers = {
            "Authorization": f"Bearer {self.resolved_token}",
            "Content-Type": "application/json"
        }
        for _ in range(24):
            time.sleep(5)
            poll_resp = requests.get(poll_url, headers=headers, timeout=30)
            if poll_resp.status_code == 200:
                data = poll_resp.json().get("data", {})
                status = data.get("task_status")
                if status == "succeed":
                    video_url = data.get("task_result", {}).get("videos", [{}])[0].get("url")
                    if video_url:
                        video_bytes = requests.get(video_url, timeout=60).content
                        with open(output_path, "wb") as f:
                            f.write(video_bytes)
                        return True
                elif status == "failed":
                    logger.warning(f"Kling text2video 생성 Task 실패: {data}")
                    return False
        return False

    def _generate_kling_image2video(self, image_url: str, prompt: str, output_path: str, duration: int, api_key: str) -> bool:
        """
        Kling AI v1/videos/image2video API 호출 (비동기 폴링).
        이미지 URL을 기반으로 고품질 AI 모션 비디오를 생성.
        """
        payload = {
            "model_name": "kling-v3",
            "image": image_url,
            "prompt": prompt,
            "negative_prompt": "blurry, distorted, text artifacts, low quality, watermark, fast motion, camera movement, motion blur, extra limbs, face distortion, character deformation, walking, running, background people",
            "duration": "5",
            "aspect_ratio": "16:9",
            "mode": "pro"
        }

        # 1. image2video 생성 요청 (Task 생성)
        logger.info(f"Kling image2video API 요청: image_url={image_url[:80]}...")
        resp = self._request_kling_with_fallback("POST", "/v1/videos/image2video", payload, api_key)
        if resp.status_code != 200 or resp.json().get("code") != 0:
            logger.warning(f"Kling image2video API 요청 실패: {resp.status_code} {resp.text}")
            return False

        task_id = resp.json().get("data", {}).get("task_id")
        if not task_id:
            logger.warning("Kling image2video 응답에서 task_id를 찾을 수 없음")
            return False

        # 2. 상태 폴링 (최대 120초 대기)
        poll_url = f"{self.base_url}/v1/videos/text2video/{task_id}"
        headers = {
            "Authorization": f"Bearer {self.resolved_token}",
            "Content-Type": "application/json"
        }
        logger.info(f"Kling image2video 폴링 시작: task_id={task_id}")
        for _ in range(24):
            time.sleep(5)
            poll_resp = requests.get(poll_url, headers=headers, timeout=30)
            if poll_resp.status_code == 200:
                data = poll_resp.json().get("data", {})
                status = data.get("task_status")
                if status == "succeed":
                    video_url = data.get("task_result", {}).get("videos", [{}])[0].get("url")
                    if video_url:
                        logger.info(f"Kling image2video 생성 완료, 다운로드 중: {video_url[:80]}...")
                        video_bytes = requests.get(video_url, timeout=60).content
                        with open(output_path, "wb") as f:
                            f.write(video_bytes)
                        return True
                elif status == "failed":
                    logger.warning(f"Kling image2video 생성 Task 실패: {data}")
                    return False
        logger.warning("Kling image2video 폴링 타임아웃 (120초 초과)")
        return False

    def _generate_ffmpeg_fallback(self, output_path: str, duration: int, image_path: str = None):
        """
        Render a fixed image (or plain background) without camera motion.
        """
        if image_path and os.path.exists(image_path):
            # Keep source pixels fixed: this is the safe fallback after an
            # image-to-video failure, not a synthetic motion effect.
            cmd = (
                f'ffmpeg -loop 1 -i "{image_path}" -f lavfi -i anullsrc=r=44100:cl=stereo '
                f'-vf "scale=1920:1080:force_original_aspect_ratio=decrease,'
                f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30" '
                f'-t {duration} -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k '
                f'-y "{output_path}" -loglevel error'
            )
        else:
            # 기본 검정 배경 모션
            cmd = (
                f'ffmpeg -f lavfi -i color=c=#0d1b2a:s=1920x1080:d={duration} '
                f'-f lavfi -i anullsrc=r=44100:cl=stereo '
                f'-t {duration} -c:v libx264 -c:a aac "{output_path}" -y -loglevel quiet'
            )
        os.system(cmd)
        logger.info(f"FFmpeg 정지 이미지 클립 폴백 생성 완료: {output_path}")
