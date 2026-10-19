package com.pipeline.video.service;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.domain.VisualMode;
import com.pipeline.video.dto.ImagesGenerateResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 2026-10-16 운영자 요청: "계획서를 만들어야 하는데 왜 이미지를 만드냐고, 돈 나가게."
 * 슬라이드형은 편집 계획서를 먼저 만들고(비용 없음), 수동·가이드 모드에서는 계획서가 있어야만 이미지 생성(비용
 * 단계)을 시작한다. AUTO는 자동화 목적상 워커가 계획서를 만든 뒤 이어서 생성한다.
 */
@ExtendWith(MockitoExtension.class)
class ImagesServiceEditPlanGateTest {

    @Mock VideoJobRepository jobRepository;
    @Mock AssetRepository assetRepository;
    @Mock AutonomyService autonomyService;
    @Mock GateService gateService;
    @Mock CharacterAssetResolver characterAssetResolver;
    @Mock FastApiClient fastApiClient;
    @Mock CostService costService;
    @Mock OperatorAlertService operatorAlertService;

    private ImagesService imagesService;

    @BeforeEach
    void setUp() {
        imagesService = new ImagesService(
                jobRepository, assetRepository, null,
                characterAssetResolver, fastApiClient,
                gateService, autonomyService, costService,
                operatorAlertService);
    }

    private void stubAssets() {
        Asset asset = new Asset();
        asset.setMetaJson("{}");
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(anyLong(), any()))
                .thenReturn(Optional.of(asset));
    }

    private VideoJob slideJob(JobStatus status) {
        VideoJob job = new VideoJob();
        job.setId(20L);
        job.setStatus(status);
        job.setVisualMode(VisualMode.SLIDE);
        return job;
    }

    @Test
    void generate_slideJobWithoutPlan_isBlockedBeforeAnyProviderCall() {
        VideoJob job = slideJob(JobStatus.IMAGES_PENDING);
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job));
        when(autonomyService.isAuto(job)).thenReturn(false);
        when(fastApiClient.getSlideEditPlan(20L)).thenReturn(Map.of("job_id", 20));

        assertThatThrownBy(() -> imagesService.generate(20L, "jam", null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("편집 계획서를 먼저");
        verify(fastApiClient, never()).generateImages(anyLong(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any(), any());
    }

    @Test
    void generate_slideJobInAutoMode_doesNotRequireASavedPlan() {
        VideoJob job = slideJob(JobStatus.IMAGES_PENDING);
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job));
        when(autonomyService.isAuto(job)).thenReturn(true);
        stubAssets();
        when(characterAssetResolver.resolve(job)).thenThrow(new IllegalStateException("character-resolved"));

        // 계획서 검사에서 막히지 않고 다음 단계(캐릭터 해석)까지 진행한다.
        assertThatThrownBy(() -> imagesService.generate(20L, "AUTO", null))
                .hasMessageContaining("character-resolved");
        verify(fastApiClient, never()).getSlideEditPlan(anyLong());
    }

    @Test
    void buildEditPlan_callsThePlanOnlyEndpointAndNeverTheGenerateEndpoint() {
        VideoJob job = slideJob(JobStatus.IMAGES_PENDING);
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job));
        stubAssets();
        ImagesGenerateResponse planResponse = new ImagesGenerateResponse();
        planResponse.setStatus("PLAN_READY");
        when(fastApiClient.planSlideEdit(anyLong(), any(), any(), anyBoolean())).thenReturn(planResponse);

        ImagesGenerateResponse response = imagesService.buildEditPlan(20L);

        assertThat(response.getStatus()).isEqualTo("PLAN_READY");
        verify(fastApiClient).planSlideEdit(anyLong(), any(), any(), anyBoolean());
        verify(fastApiClient, never()).generateImages(anyLong(), any(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), any(), any(), any());
    }

    @Test
    void buildEditPlan_isRejectedForCharacterJobsAndBeforeTts() {
        VideoJob character = new VideoJob();
        character.setId(21L);
        character.setStatus(JobStatus.IMAGES_PENDING);
        character.setVisualMode(VisualMode.CHARACTER);
        when(jobRepository.findById(21L)).thenReturn(Optional.of(character));
        assertThatThrownBy(() -> imagesService.buildEditPlan(21L))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("슬라이드형");

        VideoJob early = slideJob(JobStatus.TTS_PENDING);
        early.setId(22L);
        when(jobRepository.findById(22L)).thenReturn(Optional.of(early));
        assertThatThrownBy(() -> imagesService.buildEditPlan(22L))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("TTS 확정 전");
    }
}
