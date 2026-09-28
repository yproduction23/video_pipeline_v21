package com.pipeline.video.service;

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
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 2026-09-28 재현: GUIDED 모드에서 "실행"을 누른 뒤 응답이 오기 전에 새로고침
 * 하고 다시 누르면, 같은 작업의 스크립트 생성이 동시에 두 번 시작됐다(job 10).
 * LLM 호출 비용이 두 배로 나가고 어느 응답이 최종 저장될지도 예측할 수 없었다.
 */
class ScriptGenerationDuplicateGuardTest {

    private VideoJobRepository jobRepository;
    private AssetRepository assetRepository;
    private FastApiClient fastApiClient;
    private JobGenerationLock lock;
    private ScriptService scriptService;
    private VideoJob job;

    @BeforeEach
    void setUp() {
        jobRepository = mock(VideoJobRepository.class);
        assetRepository = mock(AssetRepository.class);
        fastApiClient = mock(FastApiClient.class);
        lock = new JobGenerationLock();
        scriptService = new ScriptService(
                jobRepository,
                assetRepository,
                fastApiClient,
                mock(GateService.class),
                mock(AutonomyService.class),
                mock(CostService.class),
                lock
        );
        job = VideoJob.builder()
                .id(1L)
                .keyword("삼성전자 실적")
                .category(Category.KOSPI)
                .status(JobStatus.SCRIPT_PENDING)
                .autonomy(Autonomy.GUIDED)
                .longformTargetMinutes(5)
                .build();
        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findByJobIdAndAssetType(anyLong(), any())).thenReturn(List.of());
    }

    @Test
    void secondConcurrentRequestForSameJobIsRejectedWithoutCallingFastApi() {
        assertThat(lock.tryAcquire(1L, "SCRIPT")).isTrue(); // 첫 요청이 이미 진행 중이라고 가정

        assertThatThrownBy(() -> scriptService.generate(1L, "admin"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("이미 진행 중");

        verify(fastApiClient, never()).generateScript(
                anyLong(), anyString(), anyInt(), anyString(), any(),
                anyBoolean(), any(), any(), any(), any());
    }

    @Test
    void lockIsReleasedAfterSuccessSoARetryCanProceed() {
        ScriptGenerateResponse response = new ScriptGenerateResponse();
        response.setScript("본문");
        response.setSections(List.of());
        response.setRequiresManualReview(true); // GUIDED라 자동 확정으로 더 진행하지 않음
        when(fastApiClient.generateScript(
                anyLong(), anyString(), anyInt(), anyString(), any(),
                anyBoolean(), any(), any(), any(), any())).thenReturn(response);

        scriptService.generate(1L, "admin");

        assertThat(lock.tryAcquire(1L, "SCRIPT")).isTrue();
    }

    @Test
    void lockIsReleasedAfterFailureSoARetryCanProceed() {
        when(fastApiClient.generateScript(
                anyLong(), anyString(), anyInt(), anyString(), any(),
                anyBoolean(), any(), any(), any(), any()))
                .thenThrow(new RuntimeException("FastAPI 오류"));

        assertThatThrownBy(() -> scriptService.generate(1L, "admin"));

        assertThat(lock.tryAcquire(1L, "SCRIPT")).isTrue();
    }

    @Test
    void differentJobIsNotBlockedByAnotherJobsInFlightGeneration() {
        assertThat(lock.tryAcquire(1L, "SCRIPT")).isTrue();

        assertThat(lock.tryAcquire(2L, "SCRIPT")).isTrue();
    }
}
