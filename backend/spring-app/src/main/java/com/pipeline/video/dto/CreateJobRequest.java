package com.pipeline.video.dto;

import com.pipeline.video.domain.Autonomy;
import com.pipeline.video.domain.Category;
import com.pipeline.video.domain.Format;
import com.pipeline.video.domain.RenderProfile;
import lombok.Data;

import java.math.BigDecimal;

@Data
public class CreateJobRequest {
    private String title;
    private String keyword;
    private String keywordPlanId;
    private Category category;                       // 주식 카테고리
    private Autonomy autonomy = Autonomy.GUIDED;
    private Format format = Format.FACELESS_NARRATION;
    private RenderProfile renderProfile = RenderProfile.LONGFORM_16x9;
    private boolean makeShorts = false;
    private Integer shortsCount = 3;
    private Integer longformTargetMinutes = 20;       // 15/20/30 등 유동적
    private BigDecimal budgetCap;
    /** Gemini 정지 이미지 생성 전용 상한. Fal 모션 비용은 이 값에 포함하지 않는다. */
    private BigDecimal geminiImageBudgetCap;
    private String policyJson;
    private String channelId;
    /** null이면 채널 기본값, 그것도 없으면 FACTUAL. */
    private com.pipeline.video.domain.ContentNature contentNature;
    private String characterOverride;
    // 숫자 카드·차트 오버레이는 레거시 호환용 명시 옵션이다. 새 영상은
    // 기사형·일반형·정보형의 대본 의미 시각화를 기본값으로 사용한다.
    private boolean dataVisualsEnabled = false;
}
