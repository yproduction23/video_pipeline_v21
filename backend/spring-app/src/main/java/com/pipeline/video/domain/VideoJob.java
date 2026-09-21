package com.pipeline.video.domain;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Entity
@Table(name = "video_job")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VideoJob {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String title;

    private String keyword;

    /** 선택한 다중 키워드 기획안의 추적 ID. 성과 피드백 루프에서 원인을 연결한다. */
    @Column(name = "keyword_plan_id", length = 64)
    private String keywordPlanId;

    @Enumerated(EnumType.STRING)
    private Category category;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private JobStatus status;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private Autonomy autonomy;

    @Enumerated(EnumType.STRING)
    private Format format;

    @Enumerated(EnumType.STRING)
    private RenderProfile renderProfile;

    @Column(columnDefinition = "TEXT")
    private String policyJson;

    private BigDecimal budgetCap;

    /** Gemini 정지 이미지 전용 상한. Fal 모션 및 다른 단계 비용과 분리해 기록한다. */
    @Column(name = "gemini_image_budget_cap")
    private BigDecimal geminiImageBudgetCap;

    private BigDecimal costAccumulated;

    private boolean makeShorts;
    private Integer shortsCount;

    private Integer longformTargetMinutes;

    private String sourceVideoPath;
    private String outputPath;

    @Column(length = 255)
    private String youtubeUrl;

    private String createdBy;

    @Column(name = "channel_id", length = 50)
    private String channelId;

    /** 작업 생성 시 확정된 콘텐츠 성격. null이면 FACTUAL로 취급한다. */
    @Enumerated(EnumType.STRING)
    @Column(name = "content_nature", length = 20)
    private ContentNature contentNature;

    /** null이면 channel profile의 기본 캐릭터를 상속한다. */
    @Column(name = "character_override", length = 100)
    private String characterOverride;

    @Builder.Default
    private boolean dataVisualsEnabled = false;

    /** GUIDED 작업에서 TTS 생성 전에 사용자가 선택한 목소리. */
    @Column(name = "tts_voice_id", length = 100)
    private String ttsVoiceId;

    @CreationTimestamp
    private LocalDateTime createdAt;

    @UpdateTimestamp
    private LocalDateTime updatedAt;
}
