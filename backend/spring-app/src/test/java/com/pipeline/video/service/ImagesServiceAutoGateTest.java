package com.pipeline.video.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.GateName;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.ImagesGenerateResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * AUTO 모드 이미지 검수 게이트 단위 테스트.
 *
 * 1. 수동 검토 필요 + AUTO 모드는 재시도 대기로 전환하고 자동 확정하지 않는다.
 * 2. 수동 검토 불필요 + AUTO 모드는 기존처럼 자동 확정한다.
 * 3. 수동 검토 필요 상태는 사람이 확인한 뒤 명시적으로 승인할 수 있다.
 * 4. 내부 AUTO 확정 경로는 사전 게이트를 통과한 호출로 처리한다.
 */
@ExtendWith(MockitoExtension.class)
class ImagesServiceAutoGateTest {

    @Mock VideoJobRepository jobRepository;
    @Mock AssetRepository assetRepository;
    @Mock AutonomyService autonomyService;
    @Mock GateService gateService;
    @Mock CharacterAssetResolver characterAssetResolver;
    @Mock FastApiClient fastApiClient;
    @Mock CostService costService;

    private ImagesService imagesService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        imagesService = spy(new ImagesService(
                jobRepository, assetRepository,
                null,
                characterAssetResolver, fastApiClient,
                gateService, autonomyService, costService
        ));
    }

    @Test
    void confirm_requiresManualReview_humanReviewer_canApprove() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        ImagesGenerateResponse qc = new ImagesGenerateResponse();
        qc.setRequiresManualReview(true);
        qc.setReviewReasons(List.of("VISUAL_MIX_PREFLIGHT_FAILURE:article_evidence<5%"));

        Asset asset = new Asset();
        asset.setMetaJson(objectMapper.writeValueAsString(qc));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.IMAGE_QC_REPORT))
                .thenReturn(Optional.of(asset));

        imagesService.confirm(1L, "SONG");

        verify(gateService).approve(eq(1L), eq(GateName.IMAGES), eq("SONG"), eq("이미지/GIF 확정"));
    }

    @Test
    void confirm_requiresManualReview_autoUsername_passes() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        ImagesGenerateResponse qc = new ImagesGenerateResponse();
        qc.setRequiresManualReview(true);
        qc.setReviewReasons(List.of("VISUAL_MIX_PREFLIGHT_FAILURE"));

        Asset asset = new Asset();
        asset.setMetaJson(objectMapper.writeValueAsString(qc));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.IMAGE_QC_REPORT))
                .thenReturn(Optional.of(asset));

        imagesService.confirm(1L, "AUTO");

        verify(gateService).approve(eq(1L), any(), eq("AUTO"), any());
    }

    @Test
    void confirm_noManualReview_anyUser_passes() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        ImagesGenerateResponse qc = new ImagesGenerateResponse();
        qc.setRequiresManualReview(false);

        Asset asset = new Asset();
        asset.setMetaJson(objectMapper.writeValueAsString(qc));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.IMAGE_QC_REPORT))
                .thenReturn(Optional.of(asset));

        imagesService.confirm(1L, "SONG");

        verify(gateService).approve(eq(1L), any(), eq("SONG"), any());
    }

    @Test
    void autoMode_requiresManualReview_setsRetryRequired_and_doesNotConfirm() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        Asset ttsAsset = new Asset();
        ttsAsset.setMetaJson("{}");
        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson("{}");

        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setRequiresManualReview(true);
        result.setReviewReasons(List.of("VISUAL_MIX_PREFLIGHT_FAILURE"));

        CharacterAssetResolver.ResolvedCharacter mockChar = new CharacterAssetResolver.ResolvedCharacter(
                "profile-1", "/path/to/img", "style", "/path/to/poses", null, null, null, 1.0f, "0123456789abcdef"
        );

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.TTS_AUDIO)).thenReturn(Optional.of(ttsAsset));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT)).thenReturn(Optional.of(scriptAsset));
        when(characterAssetResolver.resolve(job)).thenReturn(mockChar);
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(result);
        when(autonomyService.isAuto(job)).thenReturn(true);

        imagesService.generate(1L, "AUTO");

        assertThat(job.getStatus()).isEqualTo(JobStatus.IMAGES_RETRY_REQUIRED);
        verify(jobRepository).save(job);
        verify(imagesService, never()).confirm(eq(1L), any());
    }

    @Test
    void autoMode_noManualReview_callsConfirmAuto() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        Asset ttsAsset = new Asset();
        ttsAsset.setMetaJson("{}");
        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson("{}");

        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setRequiresManualReview(false);

        CharacterAssetResolver.ResolvedCharacter mockChar = new CharacterAssetResolver.ResolvedCharacter(
                "profile-1", "/path/to/img", "style", "/path/to/poses", null, null, null, 1.0f, "0123456789abcdef"
        );

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.TTS_AUDIO)).thenReturn(Optional.of(ttsAsset));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT)).thenReturn(Optional.of(scriptAsset));
        when(characterAssetResolver.resolve(job)).thenReturn(mockChar);
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(result);
        when(autonomyService.isAuto(job)).thenReturn(true);
        doNothing().when(imagesService).confirm(eq(1L), eq("AUTO"));

        imagesService.generate(1L, "AUTO");

        verify(imagesService).confirm(eq(1L), eq("AUTO"));
    }

    @Test
    void duplicateImageRequest_returnsAlreadyRunning_withoutFailingTheJob() {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        Asset ttsAsset = new Asset();
        ttsAsset.setMetaJson("{}");
        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson("{}");
        CharacterAssetResolver.ResolvedCharacter mockChar = new CharacterAssetResolver.ResolvedCharacter(
                "profile-1", "/path/to/img", "style", "/path/to/poses", null, null, null, 1.0f, "0123456789abcdef"
        );

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.TTS_AUDIO))
                .thenReturn(Optional.of(ttsAsset));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT))
                .thenReturn(Optional.of(scriptAsset));
        when(characterAssetResolver.resolve(job)).thenReturn(mockChar);
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any()))
                .thenThrow(new RuntimeException("이미지 생성 오류: Image generation is already running for job 1"));

        ImagesGenerateResponse result = imagesService.generate(1L, "AUTO");

        assertThat(result.getStatus()).isEqualTo("ALREADY_RUNNING");
        assertThat(job.getStatus()).isEqualTo(JobStatus.IMAGES_PENDING);
        verify(jobRepository, never()).save(job);
        verify(assetRepository, never()).save(any(Asset.class));
    }

    @Test
    void generate_withSceneIndices_passesThemToFastApiClient() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        Asset ttsAsset = new Asset();
        ttsAsset.setMetaJson("{}");
        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson("{}");

        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setRequiresManualReview(false);

        CharacterAssetResolver.ResolvedCharacter mockChar = new CharacterAssetResolver.ResolvedCharacter(
                "profile-1", "/path/to/img", "style", "/path/to/poses", null, null, null, 1.0f, "0123456789abcdef"
        );

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.TTS_AUDIO)).thenReturn(Optional.of(ttsAsset));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT)).thenReturn(Optional.of(scriptAsset));
        when(characterAssetResolver.resolve(job)).thenReturn(mockChar);
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), eq(List.of(1, 3))))
                .thenReturn(result);
        when(autonomyService.isAuto(job)).thenReturn(false);

        imagesService.generate(1L, "SONG", List.of(1, 3));

        verify(fastApiClient).generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), eq(List.of(1, 3)));
    }
}
