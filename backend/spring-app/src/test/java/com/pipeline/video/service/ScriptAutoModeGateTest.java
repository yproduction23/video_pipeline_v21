package com.pipeline.video.service;

import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.Autonomy;
import com.pipeline.video.domain.Category;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.ScriptGenerateResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ScriptAutoModeGateTest {

    private VideoJobRepository jobRepository;
    private AssetRepository assetRepository;
    private FastApiClient fastApiClient;
    private GateService gateService;
    private AutonomyService autonomyService;
    private CostService costService;
    private ScriptService scriptService;
    private VideoJob job;

    @BeforeEach
    void setUp() {
        jobRepository = mock(VideoJobRepository.class);
        assetRepository = mock(AssetRepository.class);
        fastApiClient = mock(FastApiClient.class);
        gateService = mock(GateService.class);
        autonomyService = mock(AutonomyService.class);
        costService = mock(CostService.class);
        scriptService = spy(new ScriptService(
                jobRepository,
                assetRepository,
                fastApiClient,
                gateService,
                autonomyService,
                costService
        ));
        job = VideoJob.builder()
                .id(1L)
                .title("테스트")
                .keyword("삼성전자 실적")
                .category(Category.INDIVIDUAL_STOCK)
                .status(JobStatus.SCRIPT_PENDING)
                .autonomy(Autonomy.AUTO)
                .longformTargetMinutes(5)
                .build();

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.KEYWORD)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(true);
    }

    @Test
    void autoMode_doesNotConfirmWhenRequiresManualReview() {
        ScriptGenerateResponse response = response(true);
        when(fastApiClient.generateScript(
                eq(1L), eq("삼성전자 실적"), eq(5), eq("INDIVIDUAL_STOCK"),
                isNull(), eq(false), isNull(), eq("AUTO"), isNull(), isNull()
        )).thenReturn(response);

        scriptService.generate(1L, "AUTO");

        assertThat(job.getStatus()).isEqualTo(JobStatus.SCRIPT_PENDING);
        verify(scriptService, never()).confirm(eq(1L), anyString(), anyList(), eq("AUTO"));
    }

    @Test
    void autoMode_confirmsWhenQualityPasses() {
        ScriptGenerateResponse response = response(false);
        when(fastApiClient.generateScript(
                eq(1L), eq("삼성전자 실적"), eq(5), eq("INDIVIDUAL_STOCK"),
                isNull(), eq(false), isNull(), eq("AUTO"), isNull(), isNull()
        )).thenReturn(response);
        doNothing().when(scriptService).confirm(
                eq(1L), eq("검증된 대본"), eq(List.of()), eq("AUTO"));

        scriptService.generate(1L, "AUTO");

        verify(scriptService).confirm(1L, "검증된 대본", List.of(), "AUTO");
    }

    private ScriptGenerateResponse response(boolean requiresManualReview) {
        ScriptGenerateResponse response = new ScriptGenerateResponse();
        response.setScript("검증된 대본");
        response.setSections(List.of());
        response.setCharCount(7);
        response.setLlmCallCount(3);
        response.setRequiresManualReview(requiresManualReview);
        return response;
    }
}
