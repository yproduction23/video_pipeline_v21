package com.pipeline.video.domain;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "channel_profile")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class ChannelProfile {

    @Id
    @Column(name = "channel_id", length = 50)
    private String channelId;

    @Column(name = "channel_name", nullable = false, length = 100)
    private String channelName;

    @Column(name = "character_image_path", length = 500)
    private String characterImagePath;

    /** Transparent channel logo/wordmark rendered in the thumbnail's top layer. */
    @Column(name = "watermark_path", length = 500)
    private String watermarkPath;

    @Column(name = "character_style_prompt", columnDefinition = "TEXT")
    private String characterStylePrompt;

    /** [S2-4] 채널별 캐릭터 포즈 라이브러리 디렉토리
     *  예: /app/data/characters/finance_hunter/poses
     *  설정 시 이중 레이어 합성 모드 활성화, 미설정 시 일체형 모드 유지 */
    @Column(name = "character_poses_dir", length = 500)
    private String characterPosesDir;

    /** 채널의 기본 캐릭터. 모든 작업은 이 값을 상속하며 작업별 override가 우선한다. */
    @Column(name = "character_key", length = 100)
    private String characterKey;

    /** [Sprint 3] 학습 완료된 LoRA 모델 safetensors CDN URL.
     *  Fal.ai flux-lora-fast-training 학습 결과의 diffusers_lora_file.url 값.
     *  설정 시 이미지 생성에 fal-ai/flux-lora 엔드포인트 사용 → 캐릭터 일관성 극대화. */
    @Column(name = "lora_model_id", columnDefinition = "TEXT")
    private String loraModelId;

    /** [Sprint 3] LoRA 활성화 트리거 단어 (영문+숫자만, 학습 시 지정한 값과 동일해야 함).
     *  이미지 생성 프롬프트 맨 앞에 자동 삽입되어 LoRA 캐릭터를 활성화합니다. */
    @Column(name = "lora_trigger_word", length = 50)
    private String loraTriggerWord;

    /** [Sprint 3] LoRA 적용 강도 (0.8~1.2, 기본 1.0).
     *  값이 낮을수록 LoRA 영향 감소(배경 자연스러움), 높을수록 캐릭터 특성 강조. */
    @Column(name = "lora_scale")
    @Builder.Default
    private Float loraScale = 1.0f;

    /** [Sprint 3] 채널 색상 테마 (HEX 코드 JSON).
     *  예: {"bg": "#0d1b2a", "accent": "#e2b96f", "text": "#ffffff"}
     *  배경 이미지 생성 프롬프트의 색상 지시에 활용. */
    @Column(name = "color_theme", columnDefinition = "TEXT")
    private String colorTheme;

    /** [Sprint 3] 자막 스타일 설정 (JSON).
     *  예: {"font_size": 76, "font_name": "NanumGothicBold", "color": "white", "bg_opacity": 0.6}
     *  미설정 시 runtime_config의 기본 자막 스타일 적용. */
    @Column(name = "subtitle_style", columnDefinition = "TEXT")
    private String subtitleStyle;

    /** Versioned rendering policy, not a copied channel artwork or logo. */
    @Column(name = "reference_style_profile", length = 100)
    @Builder.Default
    private String referenceStyleProfile = "black_han_sans_v1";

    /** [Sprint 3] 채널별 TTS 속도 배속 오버라이드.
     *  미설정(null) 시 runtime_config의 전역 tts_speed 값 사용.
     *  채널마다 다른 나레이션 템포를 유지해야 할 때 사용. */
    @Column(name = "tts_speed_override")
    private Float ttsSpeedOverride;

    @Column(name = "voice_id", length = 100)
    private String voiceId;

    /** 채널의 주요 콘텐츠 분야 (예: 경제/역사/시사, 노인/생활정보, 건강/제품판매).
     *  키워드 검색 시 이 분야에 맞는 인기 영상만 추천하는 필터의 기준값. */
    @Column(name = "genre_primary", length = 50)
    private String genrePrimary;

    /** 이 채널에서 다루지 않는 제외 분야·키워드 (쉼표로 구분).
     *  트렌드 검색 결과에서 제목·태그에 이 키워드가 포함된 영상을 제외한다. */
    @Column(name = "genre_excluded", columnDefinition = "TEXT")
    private String genreExcluded;

    /** 이 채널의 기본 콘텐츠 성격. 비어 있으면 FACTUAL로 취급한다. 작업 생성 시 작업별로 바꿀 수 있다. */
    @Enumerated(EnumType.STRING)
    @Column(name = "content_nature", length = 20)
    private ContentNature contentNature;

    @CreationTimestamp
    private LocalDateTime createdAt;
}
