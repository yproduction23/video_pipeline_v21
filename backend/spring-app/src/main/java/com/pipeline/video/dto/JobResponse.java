package com.pipeline.video.dto;

import com.pipeline.video.domain.*;
import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
public class JobResponse {
    private Long id;
    private String title;
    private String keyword;
    private String keywordPlanId;
    private Category category;
    private JobStatus status;
    private Autonomy autonomy;
    private Format format;
    private RenderProfile renderProfile;
    private boolean makeShorts;
    private Integer shortsCount;
    private Integer longformTargetMinutes;
    private BigDecimal budgetCap;
    private BigDecimal geminiImageBudgetCap;
    private BigDecimal costAccumulated;
    private String createdBy;
    private String ttsVoiceId;
    private String sourceVideoPath;
    private String outputPath;
    private String policyJson;
    private String channelId;
    private com.pipeline.video.domain.ContentNature contentNature;
    private String characterOverride;
    private boolean dataVisualsEnabled;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    public static JobResponse from(VideoJob job) {
        JobResponse r = new JobResponse();
        r.setId(job.getId());
        r.setTitle(job.getTitle());
        r.setKeyword(job.getKeyword());
        r.setKeywordPlanId(job.getKeywordPlanId());
        r.setCategory(job.getCategory());
        r.setStatus(job.getStatus());
        // Legacy MANUAL rows are exposed as the new GUIDED mode.
        r.setAutonomy(job.getAutonomy() == Autonomy.MANUAL ? Autonomy.GUIDED : job.getAutonomy());
        r.setFormat(job.getFormat());
        r.setRenderProfile(job.getRenderProfile());
        r.setMakeShorts(job.isMakeShorts());
        r.setShortsCount(job.getShortsCount());
        r.setLongformTargetMinutes(job.getLongformTargetMinutes());
        r.setBudgetCap(job.getBudgetCap());
        r.setGeminiImageBudgetCap(job.getGeminiImageBudgetCap());
        r.setCostAccumulated(job.getCostAccumulated());
        r.setCreatedBy(job.getCreatedBy());
        r.setTtsVoiceId(job.getTtsVoiceId());
        r.setSourceVideoPath(job.getSourceVideoPath());
        r.setOutputPath(job.getOutputPath());
        r.setPolicyJson(job.getPolicyJson());
        r.setChannelId(job.getChannelId());
        r.setContentNature(job.getContentNature() != null ? job.getContentNature() : com.pipeline.video.domain.ContentNature.FACTUAL);
        r.setCharacterOverride(job.getCharacterOverride());
        r.setDataVisualsEnabled(job.isDataVisualsEnabled());
        r.setCreatedAt(job.getCreatedAt());
        r.setUpdatedAt(job.getUpdatedAt());
        return r;
    }
}
