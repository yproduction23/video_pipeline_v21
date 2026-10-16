package com.pipeline.video.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class ImagesGenerateResponse {
    private String status;

    @JsonProperty("batch_job_name")
    private String batchJobName;

    @JsonProperty("batch_state")
    private String batchState;

    private String error;

    @JsonProperty("job_id")
    private Long jobId;

    @JsonProperty("scene_count")
    private Integer sceneCount;

    @JsonProperty("gif_count")
    private Integer gifCount;

    @JsonProperty("scenes")
    private List<SceneImageDto> scenes;

    @JsonProperty("gifs")
    private List<GifClipDto> gifs;

    @JsonProperty("quality_report")
    private Map<String, Object> qualityReport;

    /** 슬라이드형 편집 계획서 요약(버전·입력 해시·배경/후편집 개수·구간). 산출물 계보 확인용. */
    @JsonProperty("edit_plan")
    private Map<String, Object> editPlan;

    @JsonProperty("tts_subtitle_sync")
    private Map<String, Object> ttsSubtitleSync;

    @JsonProperty("requires_manual_review")
    private boolean requiresManualReview;

    @JsonProperty("review_reasons")
    private List<String> reviewReasons;

    @JsonProperty("held_scenes")
    private List<Map<String, Object>> heldScenes;

    @JsonProperty("operational_contract_audit")
    private Map<String, Object> operationalContractAudit;
}
