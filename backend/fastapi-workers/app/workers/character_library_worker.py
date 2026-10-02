"""
캐릭터 포즈 라이브러리 워커 (S2-2)

역할:
  - 채널별 마스코트 캐릭터의 포즈/감정별 이미지를 배치 생성
  - 배경 제거(rembg) 후 투명 PNG 형태로 /app/data/characters/<channel_id>/poses/ 에 저장
  - 이후 images_worker.py에서 배경 이미지 위에 FFmpeg overlay 합성

지원 포즈:
  neutral, happy, surprised, worried, thinking, explaining, pointing

사용법:
  POST /workers/character-library/generate
  {
    "channel_id": "finance_hunter",
    "character_description": "cute gold coin mascot, chibi cartoon, ...",
    "regenerate": false
  }
"""
import os
import logging
import json
import re
import hashlib
from pathlib import Path

from app import runtime_config
from app.utils.budget import ProviderRequestAudit

logger = logging.getLogger(__name__)

IDENTITY_MANIFEST_VERSION = "3.0"
IDENTITY_STYLE_LOCK = {
    "medium": "original_2d_editorial_cartoon",
    "body": "single round yellow coin mascot",
    "face": "large oval eyes with black ink features",
    "linework": "thick dark-brown variable-width ink outline",
    "rendering": "two-to-three tone cel shading with subtle printed texture",
}

# 지원 포즈 목록 및 각 포즈별 프롬프트 접미사
POSE_CONFIGS = {
    "neutral": {
        "desc": "standing upright, calm expression, arms relaxed at sides, professional analyst pose",
        "ko": "기본 중립 포즈, 차분한 표정"
    },
    "happy": {
        "desc": "cheering with both arms raised, big smile, eyes sparkling, celebrating good news",
        "ko": "양팔을 들고 기뻐하는 포즈, 활짝 웃는 표정"
    },
    "surprised": {
        "desc": "wide eyes, mouth open in surprise, both hands on cheeks, shocked expression",
        "ko": "놀란 표정, 양손으로 볼을 감싸는 포즈"
    },
    "worried": {
        "desc": "furrowed brow, one hand on chin thinking, slight frown, concerned look",
        "ko": "걱정스러운 표정, 턱에 손을 얹고 고민하는 포즈"
    },
    "thinking": {
        "desc": "head tilted, finger pointing to temple, thoughtful expression, curious look",
        "ko": "고개를 기울이고 관자놀이에 손가락을 대는 생각하는 포즈"
    },
    "explaining": {
        "desc": "one arm extended forward, palm open, confident expression, presenting gesture",
        "ko": "한 팔을 뻗어 설명하는 포즈, 자신감 있는 표정"
    },
    "pointing": {
        "desc": "index finger pointing to the right side, direct gaze, assertive confident expression",
        "ko": "오른쪽을 손가락으로 가리키는 포즈, 단호한 표정"
    },
}

# 캐릭터 이미지 기본 크기 (합성 시 영상 대비 비율로 조정됨)
POSE_CONFIGS.update({
    "engineer": {
        "desc": "wearing a yellow safety helmet and industrial workwear, holding a small chip and wrench, focused expression",
        "ko": "factory engineer pose",
    },
    "scientist": {
        "desc": "wearing a clean white lab coat and smart goggles, holding a glowing chip sample, curious expression",
        "ko": "research scientist pose",
    },
    "analyst": {
        "desc": "wearing a clean broadcaster jacket, holding a report folder, confident editorial analyst expression",
        "ko": "editorial analyst pose",
    },
    "teacher": {
        "desc": "wearing a warm teacher cardigan, holding a pointer and notebook, explaining expression",
        "ko": "teacher explanation pose",
    },
    "explorer": {
        "desc": "wearing a field explorer vest and utility cap, holding a magnifying glass, curious expression",
        "ko": "field explorer pose",
    },
    "hero_business": {
        "desc": "wearing a tailored navy business suit with a gold accent, standing proudly with one hand raised",
        "ko": "business hero pose",
    },
})

# 작업자와 운영 화면에서 같은 설명을 확인할 수 있도록, 모든 포즈 지시를
# 한국어로 통일한다. 모델에게도 이 원문을 그대로 전달해 의상/표정 기준을
# 한 파일에서 관리한다.
POSE_CONFIGS.update({
    "neutral": {"desc": "정면을 바라보며 한 손은 가볍게 재킷을 잡고 다른 손은 편안히 내린 차분하고 신뢰감 있는 기본 설명 포즈", "ko": "기본 설명 포즈, 차분하고 신뢰감 있는 표정"},
    "happy": {"desc": "한 손으로 엄지를 들어 긍정 신호를 보이고 다른 손은 가볍게 펼친 채 자신 있게 미소 짓는 좋은 소식 반응 포즈", "ko": "긍정 신호 포즈, 자신감 있는 미소"},
    "surprised": {"desc": "눈을 크게 뜨고 한 손은 입가에, 다른 손은 앞으로 펼쳐 예상 밖의 변화를 알리는 놀란 반응 포즈", "ko": "예상 밖 변화에 놀란 반응 포즈"},
    "worried": {"desc": "미간을 살짝 찌푸리고 두 손을 앞으로 모아 위험을 신중하게 경고하는 우려 반응 포즈", "ko": "위험을 신중하게 경고하는 우려 포즈"},
    "thinking": {"desc": "고개를 기울이고 관자놀이에 손가락을 대며 핵심을 고민하는 생각하는 포즈, 집중한 표정", "ko": "고개를 기울이고 관자놀이에 손가락을 대는 생각하는 포즈"},
    "explaining": {"desc": "한 손바닥을 위로 열어 복잡한 내용을 쉽게 풀어 설명하고 다른 손은 몸 앞에서 받쳐 주는 설명 포즈", "ko": "핵심을 쉽게 풀어 설명하는 포즈"},
    "pointing": {"desc": "오른쪽을 손가락으로 가리키는 포즈, 시선은 카메라를 향하고 단호한 표정", "ko": "오른쪽을 손가락으로 가리키는 포즈, 단호한 표정"},
    "engineer": {"desc": "노란 안전모와 작업 조끼를 착용하고 작은 공구를 들며 현장을 점검하는 집중한 엔지니어 포즈", "ko": "현장을 점검하는 엔지니어 포즈"},
    "scientist": {"desc": "흰 연구 가운과 보호 안경을 착용하고 손에 든 작은 칩을 살피는 호기심 있는 연구원 포즈", "ko": "칩을 살피는 연구원 포즈"},
    "analyst": {"desc": "남색 분석가 재킷과 안경을 착용하고 얇은 노트와 펜으로 핵심을 짚는 침착한 분석가 포즈", "ko": "핵심을 짚는 침착한 분석가 포즈"},
    "teacher": {"desc": "갈색 가디건과 둥근 안경을 착용하고 포인터로 설명하는 친절한 교수 포즈", "ko": "포인터로 설명하는 친절한 교수 포즈"},
    "explorer": {"desc": "탐사 조끼와 모자를 착용하고 돋보기로 단서를 찾는 호기심 있는 탐험가 포즈", "ko": "돋보기로 단서를 찾는 탐험가 포즈"},
    "hero_business": {"desc": "금색 포인트의 남색 정장을 입고 한 손을 들어 결론을 제시하는 당당한 비즈니스 리더 포즈", "ko": "결론을 제시하는 비즈니스 리더 포즈"},
})

# Phase 2 role-costume library.  This is deliberately separate from the
# legacy generic poses: creating it makes an explicit 15-image billable asset
# request, while existing channels keep rendering with their current library.
ROLE_COSTUME_SPECS = {
    "field_reporter": "yellow field-reporting vest, compact headset microphone, small handheld pointer",
    "professor": "warm brown professor cardigan, round glasses, graduation cap, wooden pointer",
    "anchor": "tailored navy broadcast jacket, neat bow tie, compact presenter earpiece",
    "referee": "bold black-and-white referee jacket, whistle, gold decision card",
    "analyst": "navy analyst vest, clear data glasses, slim stylus and notebook",
}
ROLE_COSTUME_STATES = {
    "neutral": "calm professional stance, attentive expression, one hand ready to explain",
    "highlight": "confident upbeat explanation, one arm raised toward an important finding, bright smile",
    "worried": "concerned alert expression, cautious pointing gesture, visibly reacting to a risk",
}
ROLE_COSTUME_CONFIGS = {
    f"{role}_{state}": {
        "desc": f"wearing {costume}, {state_desc}, full-body isolated character pose",
        "ko": f"{role} {state} role costume",
        "role": role,
        "state": state,
    }
    for role, costume in ROLE_COSTUME_SPECS.items()
    for state, state_desc in ROLE_COSTUME_STATES.items()
}

_MODEL_POSE_DESCRIPTIONS = {
    "neutral": "calm presenter stance, one hand lightly holds the jacket and the other rests naturally",
    "happy": "confident positive-news gesture, one single right-hand thumbs-up, left arm resting down, warm composed smile, compact vertical silhouette",
    "surprised": "unexpected-change reaction, widened eyes, one hand near the mouth and the other open forward",
    "worried": "careful risk-warning gesture, brows slightly furrowed and both hands gathered forward",
    "thinking": "head tilted, index finger touching the temple, focused thoughtful expression",
    "explaining": "clear teaching pose, one palm open upward and the other hand supporting the explanation",
    "pointing": "index finger pointing to the right, looking toward camera with an assertive expression",
    "engineer": "yellow safety helmet and work vest, holding a small wrench, focused field engineer stance",
    "scientist": "white lab coat and safety glasses, carefully examining a small microchip",
    "analyst": "navy analyst jacket and glasses, using a slim notebook and pen in a calm analytical stance",
    "teacher": "brown cardigan and round glasses, holding a wooden pointer in a kind professor stance",
    "explorer": "field vest and cap, holding a magnifying glass while examining a clue",
    "hero_business": "tailored navy business suit with a gold accent, one hand raised to present a conclusion",
    "field_reporter_neutral": "yellow field reporter vest and compact headset microphone, calm explanatory stance with a small pointer",
    "field_reporter_highlight": "yellow field reporter vest and compact headset microphone, raising a pointer to emphasize a finding",
    "field_reporter_worried": "yellow field reporter vest and compact headset microphone, cautious risk-warning hand gesture",
    "professor_neutral": "brown professor cardigan, round glasses and graduation cap, calm stance holding a wooden pointer",
    "professor_highlight": "brown professor cardigan, round glasses and graduation cap, pointer raised to emphasize a key point",
    "professor_worried": "brown professor cardigan, round glasses and graduation cap, carefully explaining a risk",
    "anchor_neutral": "tailored navy anchor jacket, gold bow tie and presenter earpiece, composed broadcast stance",
    "anchor_highlight": "tailored navy anchor jacket, gold bow tie and presenter earpiece, one hand emphasizing a key point",
    "anchor_worried": "tailored navy anchor jacket, gold bow tie and presenter earpiece, alert breaking-news warning stance",
    "referee_neutral": "black-and-white referee jacket and whistle, holding a gold decision card in a fair neutral stance",
    "referee_highlight": "black-and-white referee jacket and whistle, raising a gold decision card decisively",
    "referee_worried": "black-and-white referee jacket and whistle, holding a gold decision card with a tense risk-warning expression",
    "analyst_neutral": "navy analyst vest and clear data glasses, calmly indicating a key insight with a slim pen and notebook",
    "analyst_highlight": "navy analyst vest and clear data glasses, confidently pointing at an important finding with a slim pen",
    "analyst_worried": "navy analyst vest and clear data glasses, holding a notebook and carefully warning about a risk signal",
}
for _pose_name, _config in {**POSE_CONFIGS, **ROLE_COSTUME_CONFIGS}.items():
    _config["model_desc"] = _MODEL_POSE_DESCRIPTIONS[_pose_name]

# 레퍼런스 영상처럼 캐릭터는 항상 의상을 입은 진행자로 읽혀야 한다.
# 역할 자산은 각각의 직업 의상이 이미 model_desc에 포함되어 있고, 일반
# 포즈는 장면 어디에 합성해도 작동하는 고정 진행자 정장을 공유한다.
_GENERIC_WARDROBE = "tailored navy presenter blazer, crisp white shirt collar, slim gold tie, brown leather shoes"
_SPECIAL_WARDROBE = {
    "engineer": "yellow safety helmet, navy work jacket, utility belt, brown work boots",
    "scientist": "white lab coat, navy shirt, clear safety glasses, brown shoes",
    "teacher": "warm brown cardigan over a white shirt, round glasses, brown shoes",
    "explorer": "brown field vest, utility cap, navy shirt, brown hiking boots",
}
for _pose_name, _config in POSE_CONFIGS.items():
    _config["model_wardrobe"] = _SPECIAL_WARDROBE.get(_pose_name, _GENERIC_WARDROBE)
for _config in ROLE_COSTUME_CONFIGS.values():
    _config["model_wardrobe"] = ""

# 역할 의상 또한 한국어 원문으로 고정한다. 다섯 역할의 금색 코인 캐릭터,
# 굵은 짙은 갈색 외곽선, 2D 카툰 채색이라는 공통 스타일을 공유한다.
ROLE_COSTUME_CONFIGS = {
    "field_reporter_neutral": {"desc": "노란 현장 조끼와 헤드셋 마이크를 착용하고 작은 포인터를 든 현장 기자의 차분한 설명 포즈", "ko": "현장 기자 기본 설명 포즈"},
    "field_reporter_highlight": {"desc": "노란 현장 조끼와 헤드셋 마이크를 착용하고 포인터를 들어 중요한 소식을 강조하는 현장 기자 포즈", "ko": "현장 기자 핵심 강조 포즈"},
    "field_reporter_worried": {"desc": "노란 현장 조끼와 헤드셋 마이크를 착용하고 위험을 알리듯 조심스럽게 손짓하는 현장 기자 포즈", "ko": "현장 기자 위험 경고 포즈"},
    "professor_neutral": {"desc": "갈색 가디건, 둥근 안경, 학사모를 착용하고 포인터를 든 차분한 교수 포즈", "ko": "교수 기본 설명 포즈"},
    "professor_highlight": {"desc": "갈색 가디건, 둥근 안경, 학사모를 착용하고 포인터로 핵심을 강조하는 교수 포즈", "ko": "교수 핵심 강조 포즈"},
    "professor_worried": {"desc": "갈색 가디건, 둥근 안경, 학사모를 착용하고 신중한 표정으로 위험을 설명하는 교수 포즈", "ko": "교수 위험 설명 포즈"},
    "anchor_neutral": {"desc": "남색 방송 재킷, 금색 나비넥타이, 발표용 이어피스를 착용하고 차분히 진행하는 앵커 포즈", "ko": "앵커 기본 진행 포즈"},
    "anchor_highlight": {"desc": "남색 방송 재킷, 금색 나비넥타이, 발표용 이어피스를 착용하고 한 손으로 핵심을 강조하는 앵커 포즈", "ko": "앵커 핵심 강조 포즈"},
    "anchor_worried": {"desc": "남색 방송 재킷, 금색 나비넥타이, 발표용 이어피스를 착용하고 긴급 소식을 신중하게 전달하는 앵커 포즈", "ko": "앵커 긴급 경고 포즈"},
    "referee_neutral": {"desc": "흑백 심판 재킷과 호루라지를 착용하고 금색 판정 카드를 든 공정한 심판 포즈", "ko": "심판 기본 판정 포즈"},
    "referee_highlight": {"desc": "흑백 심판 재킷과 호루라지를 착용하고 금색 판정 카드를 높이 들어 결정을 알리는 심판 포즈", "ko": "심판 결정 강조 포즈"},
    "referee_worried": {"desc": "흑백 심판 재킷과 호루라지를 착용하고 금색 판정 카드를 든 채 위험을 경고하는 긴장된 심판 포즈", "ko": "심판 위험 경고 포즈"},
    "analyst_neutral": {"desc": "남색 분석가 조끼와 데이터 안경을 착용하고 얇은 펜과 노트로 핵심을 짚는 침착한 분석가 포즈", "ko": "분석가 기본 설명 포즈"},
    "analyst_highlight": {"desc": "남색 분석가 조끼와 데이터 안경을 착용하고 펜으로 중요한 발견을 자신 있게 짚는 분석가 포즈", "ko": "분석가 핵심 강조 포즈"},
    "analyst_worried": {"desc": "남색 분석가 조끼와 데이터 안경을 착용하고 노트를 든 채 위험 신호를 신중하게 경고하는 분석가 포즈", "ko": "분석가 위험 경고 포즈"},
}

CHAR_WIDTH = 480
CHAR_HEIGHT = 854  # 9:16 비율 (세로형 캐릭터)


class CharacterLibraryWorker:
    """
    채널별 캐릭터 포즈 라이브러리를 생성·관리합니다.
    """

    POSES_BASE_DIR = Path("/app/data/characters")

    @staticmethod
    def _safe_channel_id(channel_id: str) -> str:
        """Keep a user supplied channel id inside the character asset root."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", channel_id or ""):
            raise ValueError("channel_id must use letters, numbers, underscores, or hyphens")
        return channel_id

    def generate_library(
        self,
        channel_id: str,
        character_description: str,
        regenerate: bool = False,
        include_role_costumes: bool = False,
        include_legacy_poses: bool = False,
        pose_names: list[str] | None = None,
    ) -> dict:
        """
        채널 ID에 해당하는 포즈 라이브러리 전체를 생성(또는 재생성)합니다.

        Returns:
            {
              "channel_id": ...,
              "poses_dir": ...,
              "generated": [...],
              "skipped": [...],
              "errors": [...]
            }
        """
        channel_id = self._safe_channel_id(channel_id)
        poses_dir = self.POSES_BASE_DIR / channel_id / "poses"
        poses_dir.mkdir(parents=True, exist_ok=True)

        results = {
            "channel_id": channel_id,
            "poses_dir": str(poses_dir),
            "generated": [],
            "skipped": [],
            "errors": [],
        }

        # AI 이미지 프로바이더 로드
        ai_provider = None
        try:
            from app.providers.factory import get_image_provider
            ai_provider = get_image_provider()
            logger.info("캐릭터 라이브러리 생성: AI 프로바이더 로드 성공")
        except Exception as e:
            logger.error(f"AI 프로바이더 로드 실패: {e}")
            results["errors"].append(f"AI 프로바이더 로드 실패: {e}")
            return results

        # The opt-in role library is exactly the priced 15-asset set.  Do not
        # silently add the legacy 12 poses to that purchase on a new channel.
        if include_role_costumes and include_legacy_poses:
            pose_configs = {**POSE_CONFIGS, **ROLE_COSTUME_CONFIGS}
        else:
            pose_configs = dict(ROLE_COSTUME_CONFIGS if include_role_costumes else POSE_CONFIGS)
        if pose_names is not None:
            requested = list(dict.fromkeys(str(name) for name in pose_names))
            unknown = [name for name in requested if name not in pose_configs]
            if unknown:
                raise ValueError(f"unknown pose names for selected library: {', '.join(unknown)}")
            pose_configs = {name: pose_configs[name] for name in requested}

        for pose_name, pose_config in pose_configs.items():
            raw_path = poses_dir / f"{pose_name}_raw.png"
            final_path = poses_dir / f"{pose_name}.png"

            # 이미 존재하고 재생성 요청이 없으면 스킵
            if final_path.exists() and not regenerate:
                logger.info(f"포즈 '{pose_name}' 이미 존재함, 스킵")
                results["skipped"].append(pose_name)
                continue

            # 1. 캐릭터 포즈 이미지 생성 (배경 있는 버전)
            prompt = self._build_character_prompt(
                character_description,
                pose_config["desc"],
                pose_config.get("model_desc"),
                pose_config.get("model_wardrobe"),
            )
            logger.info(f"포즈 '{pose_name}' 생성 중... prompt_len={len(prompt)}")

            try:
                # 2026-10-02 channel_b 재현: 이 유료 호출에 예산 추적 감사 객체를
                # 전달하지 않아, 모든 유료 Gemini 호출을 막는 안전장치("영속
                # 요청 감사 객체 없음; 유료 POST 차단")에 15개 포즈 전부 막혔다.
                # 이 기능이 처음 실제로 쓰인 이번에야 발견됐다. job 비디오 예산
                # 캡(PricingConfig)과는 별개로, 채널당 1회성 캐릭터 라이브러리
                # 생성 비용을 추적하는 자체 원장을 channel_id별로 둔다.
                audit = ProviderRequestAudit.for_path(
                    path=poses_dir / "cost_ledger.json",
                    scene_key=f"character_library:{channel_id}:{pose_name}",
                    model="gemini-3-pro-image",
                    unit_usd=float(runtime_config.value("img_cost_pro_2k_usd")),
                    usd_krw=float(runtime_config.value("usd_krw")),
                    # 영상 1건 예산 상한(PricingConfig)과 같은 안전판을 재사용한다.
                    # 포즈 15장 추정 비용(₩5천대)보다 훨씬 커서, 폭주 호출만 막는
                    # 상한 역할을 한다.
                    budget_limit_krw=int(runtime_config.value("max_budget_per_video_krw")),
                    # 관리자가 직접 결과를 보고 누르는 캐릭터 라이브러리 재생성은
                    # 클릭 자체가 이미 인간 검수다. 자동 Job 장면 생성과 같은
                    # ImageRequestControl의 영구 재검토 잠금을 공유하지 않는다.
                    enforce_request_lock=False,
                )
                ai_provider.generate_image(
                    prompt=prompt,
                    output_path=str(raw_path),
                    character_style_prompt="none",  # 프로바이더의 CHARACTER_STYLE 중복 주입 방지
                    image_provider="gemini",
                    gemini_model="gemini-3-pro-image",
                    gemini_image_size="2K",
                    gemini_request_audit=audit,
                )
            except Exception as e:
                logger.error(f"포즈 '{pose_name}' 이미지 생성 실패: {e}")
                results["errors"].append(f"{pose_name}: 이미지 생성 실패 - {e}")
                continue

            # 2. 배경 제거 (rembg)
            try:
                removed_path = self._remove_background(raw_path, final_path)
                logger.info(f"포즈 '{pose_name}' 배경 제거 완료: {removed_path}")
                results["generated"].append({
                    "pose": pose_name,
                    "path": str(final_path),
                    "ko": pose_config["ko"],
                })
            except Exception as e:
                logger.warning(f"포즈 '{pose_name}' 배경 제거 실패, raw 이미지 사용: {e}")
                # 배경 제거 실패 시 raw 이미지를 final로 복사
                import shutil
                shutil.copy2(str(raw_path), str(final_path))
                results["generated"].append({
                    "pose": pose_name,
                    "path": str(final_path),
                    "ko": pose_config["ko"],
                    "note": "배경 제거 미적용",
                })

        # 라이브러리 메타데이터 저장
        meta_path = poses_dir / "library_meta.json"
        existing_role_assets = any((poses_dir / f"{name}.png").exists() for name in ROLE_COSTUME_CONFIGS)
        meta = {
            "channel_id": channel_id,
            "character_description": character_description,
            "role_costume_library": bool(include_role_costumes or existing_role_assets),
            "role_costume_count": sum((poses_dir / f"{name}.png").exists() for name in ROLE_COSTUME_CONFIGS),
            "poses": {
                p["pose"]: {"path": p["path"], "ko": p["ko"]}
                for p in results["generated"]
            },
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # A pose collection is a character identity lock, not a loose bundle
        # of independently generated mascots.  Downstream composition records
        # this id and the selected asset hash on every final scene so mixed
        # character libraries cannot pass unnoticed.
        pose_assets = {}
        for pose_name in sorted({**POSE_CONFIGS, **ROLE_COSTUME_CONFIGS}):
            pose_path = poses_dir / f"{pose_name}.png"
            if pose_path.is_file():
                pose_assets[pose_name] = hashlib.sha256(pose_path.read_bytes()).hexdigest()
        canonical_seed = json.dumps({
            "channel_id": channel_id,
            "character_description": character_description.strip(),
            "style_lock": IDENTITY_STYLE_LOCK,
        }, ensure_ascii=False, sort_keys=True)
        identity_manifest = {
            "version": IDENTITY_MANIFEST_VERSION,
            "canonical_character_id": hashlib.sha256(canonical_seed.encode("utf-8")).hexdigest()[:20],
            "style_lock": IDENTITY_STYLE_LOCK,
            "pose_assets": pose_assets,
        }
        (poses_dir / "identity_manifest.json").write_text(
            json.dumps(identity_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        logger.info(
            f"캐릭터 라이브러리 생성 완료: channel={channel_id}, "
            f"생성={len(results['generated'])}, 스킵={len(results['skipped'])}, 오류={len(results['errors'])}"
        )
        return results

    def get_pose_path(self, channel_id: str, pose: str) -> str | None:
        """
        채널 ID와 포즈명으로 해당 캐릭터 투명 PNG 경로를 반환합니다.
        포즈가 없을 경우 'neutral'로 폴백하고, neutral도 없으면 None 반환.
        """
        channel_id = self._safe_channel_id(channel_id)
        poses_dir = self.POSES_BASE_DIR / channel_id / "poses"

        # 요청한 포즈 확인
        target = poses_dir / f"{pose}.png"
        if target.exists():
            return str(target)

        # neutral 폴백
        neutral = poses_dir / "neutral.png"
        if neutral.exists():
            logger.warning(f"포즈 '{pose}' 없음, neutral 폴백")
            return str(neutral)

        logger.warning(f"채널 '{channel_id}'의 캐릭터 라이브러리가 없음 (포즈: {pose})")
        return None

    def list_channels(self) -> list:
        """생성된 모든 채널 라이브러리 목록 반환"""
        if not self.POSES_BASE_DIR.exists():
            return []
        return [
            d.name
            for d in self.POSES_BASE_DIR.iterdir()
            if d.is_dir() and any((d / "poses").glob("*.png"))
        ]

    def get_library_status(self, channel_id: str) -> dict:
        """Return usable pose metadata without exposing container file paths."""
        channel_id = self._safe_channel_id(channel_id)
        poses_dir = self.POSES_BASE_DIR / channel_id / "poses"
        meta_path = poses_dir / "library_meta.json"
        if not meta_path.exists():
            return {"channel_id": channel_id, "exists": False, "poses": [], "pose_count": 0}

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            meta = {}

        poses = []
        pose_configs = {**POSE_CONFIGS, **ROLE_COSTUME_CONFIGS}
        for pose_name, pose_config in pose_configs.items():
            if (poses_dir / f"{pose_name}.png").exists():
                poses.append({"pose": pose_name, "label": pose_config["ko"]})

        return {
            "channel_id": channel_id,
            "exists": bool(poses),
            "poses": poses,
            "pose_count": len(poses),
            "character_description": meta.get("character_description", ""),
            "canonical_character_id": self._read_canonical_character_id(poses_dir),
        }

    @staticmethod
    def _read_canonical_character_id(poses_dir: Path) -> str | None:
        try:
            payload = json.loads((poses_dir / "identity_manifest.json").read_text(encoding="utf-8"))
            return str(payload.get("canonical_character_id") or "") or None
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _build_character_prompt(
        character_description: str,
        pose_desc: str,
        model_desc: str | None = None,
        model_wardrobe: str | None = None,
    ) -> str:
        """
        캐릭터 설명 + 포즈 설명을 조합하여 이미지 생성 프롬프트 구성.
        배경 제거(rembg)가 효과적으로 작동하도록 단색 배경 지정.
        """
        # The operational description remains Korean, but Gemini's provider
        # intentionally rejects non-English prompts and replaces them with a
        # generic finance scene.  Keep a separate, locked production sentence
        # so every reusable asset still renders as the fixed coin mascot.
        production_pose = model_desc or "calm full-body explanatory pose"
        wardrobe = f"Costume: {model_wardrobe}. " if model_wardrobe else ""
        return (
            "Single original yellow gold coin mascot character only: round coin body, large expressive cartoon eyes, "
            "white gloves, short legs, thick clean dark-brown ink outline, crisp 2D digital cartoon cel shading. "
            f"{wardrobe}Pose: {production_pose}. Full-body centered character with generous padding. "
            "Perfectly uniform pure white background only; no floor line, no cast shadow, no contact shadow, no reflection, "
            "no gradient, no scenery, no background objects, no other characters. No text, numbers, logo, or watermark. "
            "No human person, no animal, no bull, no realistic photo, no 3D render, no mixed art style."
        )

    @staticmethod
    def _remove_background(input_path: Path, output_path: Path) -> str:
        """
        rembg를 사용해 배경 제거 후 투명 PNG로 저장.
        rembg가 설치되지 않은 경우 fallback으로 PIL 기반 흰색 배경 제거.
        """
        try:
            from rembg import remove
            from PIL import Image
            import io

            with open(input_path, "rb") as f:
                input_data = f.read()

            output_data = remove(input_data)
            img = Image.open(io.BytesIO(output_data)).convert("RGBA")
            img.save(str(output_path), "PNG")
            return str(output_path)

        except ImportError:
            logger.warning("rembg 미설치 — PIL 흰색 배경 제거 폴백 사용")
            return CharacterLibraryWorker._remove_white_background_fallback(input_path, output_path)

    @staticmethod
    def _remove_white_background_fallback(input_path: Path, output_path: Path) -> str:
        """
        PIL 기반 흰색 배경 단순 제거 (rembg 미설치 시 폴백).
        흰색 또는 밝은 회색 픽셀을 투명 처리.
        """
        from PIL import Image
        import numpy as np

        img = Image.open(str(input_path)).convert("RGBA")
        data = np.array(img)

        r, g, b, a = data[:, :, 0], data[:, :, 1], data[:, :, 2], data[:, :, 3]
        # 흰색에 가까운 픽셀 (RGB 모두 220 이상) → 투명 처리
        white_mask = (r > 220) & (g > 220) & (b > 220)
        data[white_mask, 3] = 0

        result = Image.fromarray(data, "RGBA")
        result.save(str(output_path), "PNG")
        return str(output_path)
