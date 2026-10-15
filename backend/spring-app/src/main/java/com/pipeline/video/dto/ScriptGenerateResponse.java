package com.pipeline.video.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class ScriptGenerateResponse {
    @JsonProperty("job_id")
    private Long jobId;

    @JsonProperty("keyword")
    private String keyword;

    @JsonProperty("synopsis")
    private String synopsis;

    @JsonProperty("script")
    private String script;

    @JsonProperty("sections")
    private List<Map<String, Object>> sections;

    @JsonProperty("estimated_minutes")
    private Double estimatedMinutes;

    @JsonProperty("char_count")
    private Integer charCount;

    @JsonProperty("length_contract")
    private Map<String, Object> lengthContract;

    @JsonProperty("keyword_validation")
    private Map<String, Object> keywordValidation;

    @JsonProperty("unit_validation")
    private Map<String, Object> unitValidation;

    // v3 팩트체크 결과
    @JsonProperty("verified_facts")
    private List<Map<String, Object>> verifiedFacts;

    @JsonProperty("suspect_facts")
    private List<Map<String, Object>> suspectFacts;

    @JsonProperty("fact_check_summary")
    private Map<String, Object> factCheckSummary;

    @JsonProperty("news_articles")
    private List<Map<String, Object>> newsArticles;

    @JsonProperty("source_ref")
    private List<String> sourceRef;

    @JsonProperty("source_videos")
    private List<Map<String, Object>> sourceVideos;

    @JsonProperty("news_cross_check_status")
    private String newsCrossCheckStatus;

    @JsonProperty("fact_check_rounds")
    private Integer factCheckRounds;

    @JsonProperty("fact_check_log")
    private List<String> factCheckLog;

    @JsonProperty("market_snapshot_used")
    private Boolean marketSnapshotUsed;

    @JsonProperty("used_real_llm")
    private Boolean usedRealLlm;

    /** Mock output or a provider fallback must be reviewed before AUTO can continue. */
    @JsonProperty("requires_manual_review")
    private Boolean requiresManualReview;

    @JsonProperty("llm_provider_log")
    private List<java.util.Map<String, Object>> llmProviderLog;

    /** 실제 Claude 호출 수. 내러티브 플랜·흐름 QA까지 비용 장부에 반영한다. */
    @JsonProperty("llm_call_count")
    private Integer llmCallCount;

    @JsonProperty("narrative_plan")
    private Map<String, Object> narrativePlan;

    /** 슬라이드형 하이라이트 구간 계약(end_text, end_char, passed). 이미지 단계의 고지 배너 위치 기준. */
    @JsonProperty("highlight")
    private Map<String, Object> highlight;

    @JsonProperty("flow_qa")
    private Map<String, Object> flowQa;

    @JsonProperty("market_snapshot")
    private Map<String, Object> marketSnapshot;

    @JsonProperty("quality_report")
    private Map<String, Object> qualityReport;

    /** 승인 대본과 장면별 TTS 원문 해시 계보. 알 수 없는 필드로 버리면
     * 이미지 단계가 생성 시점의 장면 경계를 감사할 수 없다. */
    @JsonProperty("narration_contract")
    private Map<String, Object> narrationContract;

    /** 영상 생성 버튼의 전역 계약 버전과 이 단계의 실제 통과 판정. */
    @JsonProperty("operational_contract_audit")
    private Map<String, Object> operationalContractAudit;
}
