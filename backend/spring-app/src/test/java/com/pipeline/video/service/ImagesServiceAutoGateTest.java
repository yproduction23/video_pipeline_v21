package com.pipeline.video.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.GateName;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.domain.VisualMode;
import com.pipeline.video.dto.ImagesGenerateResponse;
import com.pipeline.video.dto.SceneImageDto;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
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
    @Mock OperatorAlertService operatorAlertService;

    private ImagesService imagesService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        imagesService = spy(new ImagesService(
                jobRepository, assetRepository,
                null,
                characterAssetResolver, fastApiClient,
                gateService, autonomyService, costService,
                operatorAlertService
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
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(result);
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
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(result);
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
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any()))
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
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), eq(List.of(1, 3)), any()))
                .thenReturn(result);
        when(autonomyService.isAuto(job)).thenReturn(false);

        imagesService.generate(1L, "SONG", List.of(1, 3));

        verify(fastApiClient).generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), eq(List.of(1, 3)), any());
    }

    @Test
    void klingUpgradeRequiresSlideModeAndSelectedScenes() {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);
        job.setVisualMode(VisualMode.SLIDE);
        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));

        org.junit.jupiter.api.Assertions.assertThrows(IllegalStateException.class,
                () -> imagesService.generate(1L, "SONG", null, true));
        org.junit.jupiter.api.Assertions.assertThrows(IllegalStateException.class,
                () -> imagesService.generate(1L, "SONG", List.of(), true));

        job.setVisualMode(VisualMode.CHARACTER);
        org.junit.jupiter.api.Assertions.assertThrows(IllegalStateException.class,
                () -> imagesService.generate(1L, "SONG", List.of(2), true));
        verify(fastApiClient, org.mockito.Mockito.never()).generateImages(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), anyBoolean());
    }

    /**
     * 2026-10-12 job 14 재현: costService.getTotal()은 Spring 원장(TTS/Claude)
     * + Worker 원장(Gemini 누적, FastApiClient.getWorkerCostLedger)을 합산한
     * "전체 누적 비용"이다. remainingBudget 계산에 이 값을 그대로 쓰면,
     * FastAPI의 budget.py::before_attempt()가 같은 Worker 누적 비용을 자기
     * 장부에서 다시 더해 비교하므로 동일 금액이 양쪽에서 이중으로 차감된다.
     * Worker 누적 비용이 전체 예산의 절반을 넘는 순간부터는 재시도가 몇 번을
     * 더 해도 구조적으로 통과할 수 없는 상태가 된다. remainingBudget은
     * Spring 쪽에서만 추적하는 비용(TTS/Claude)만 빼야 한다.
     */
    @Test
    void generate_remainingBudget_doesNotDoubleSubtractWorkerLedgerTotal() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);
        job.setBudgetCap(new BigDecimal("70000"));
        job.setGeminiImageBudgetCap(new BigDecimal("70000"));

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
        // Spring 쪽(TTS/Claude)만 1,500원 썼다고 가정한다. FastAPI Worker 장부는
        // costService 내부에서 이미 34,704원을 합산했다고 가정해도(=getTotal이
        // 36,204원을 반환해도), remainingBudget은 Worker 누적을 다시 빼면 안 된다.
        when(costService.getSpringOnlyTotal(1L)).thenReturn(new BigDecimal("1500"));
        when(fastApiClient.generateImages(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(result);
        when(autonomyService.isAuto(job)).thenReturn(false);

        imagesService.generate(1L, "SONG");

        verify(fastApiClient).generateImages(
                any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
                eq(new BigDecimal("68500")), any(), any(), any());
        verify(costService, never()).getTotal(1L);
    }

    /**
     * 2026-10-13 사용자 요청(job 14): 자동 게이트가 보류시킨 장면도 사용자가
     * 실물 후보 이미지를 직접 보고 괜찮다고 판단하면 재생성 없이 승인할 수
     * 있어야 한다. generate()와 같은 SCENE_IMAGE/IMAGE_QC_REPORT 영속 경로를
     * 공유하는지 검증한다.
     */
    @Test
    void acceptHeldScene_savesSceneImageAssetAndReplacesQcReport() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        SceneImageDto scene = new SceneImageDto();
        scene.setIndex(2);
        scene.setImagePath("/app/data/jobs/1/images/scene_002.png");

        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(scene));
        result.setHeldScenes(List.of());
        result.setRequiresManualReview(false);

        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson(objectMapper.writeValueAsString(Map.of(
                "sections", List.of(
                        Map.of("text_for_tts", "씬0 내레이션"),
                        Map.of("text_for_tts", "씬1 내레이션"),
                        Map.of("text_for_tts", "씬2 내레이션", "section", "intro")))));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT))
                .thenReturn(Optional.of(scriptAsset));
        when(fastApiClient.acceptHeldScene(1L, 2, "SONG", "씬2 내레이션", "intro")).thenReturn(result);
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.SCENE_IMAGE)).thenReturn(List.of());
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(false);

        ImagesGenerateResponse response = imagesService.acceptHeldScene(1L, 2, "SONG");

        assertThat(response.getScenes()).hasSize(1);
        verify(assetRepository).save(argThat(asset ->
                asset.getAssetType() == AssetType.SCENE_IMAGE
                        && "/app/data/jobs/1/images/scene_002.png".equals(asset.getLocalPath())));
        verify(assetRepository).save(argThat(asset -> asset.getAssetType() == AssetType.IMAGE_QC_REPORT));
    }

    @Test
    void acceptHeldScene_autoMode_confirmsOnceNoHeldScenesRemain() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        SceneImageDto scene = new SceneImageDto();
        scene.setIndex(2);
        scene.setImagePath("/app/data/jobs/1/images/scene_002.png");

        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(scene));
        result.setHeldScenes(List.of());
        result.setRequiresManualReview(false);

        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson(objectMapper.writeValueAsString(Map.of(
                "sections", List.of(
                        Map.of("text_for_tts", "씬0 내레이션"),
                        Map.of("text_for_tts", "씬1 내레이션"),
                        Map.of("text_for_tts", "씬2 내레이션")))));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT))
                .thenReturn(Optional.of(scriptAsset));
        when(fastApiClient.acceptHeldScene(1L, 2, "AUTO", "씬2 내레이션", null)).thenReturn(result);
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.SCENE_IMAGE)).thenReturn(List.of());
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(true);
        doNothing().when(imagesService).confirm(eq(1L), eq("AUTO"));

        imagesService.acceptHeldScene(1L, 2, "AUTO");

        verify(imagesService).confirm(eq(1L), eq("AUTO"));
    }

    @Test
    void acceptHeldScene_autoMode_doesNotConfirmWhileOtherScenesStillHeld() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        SceneImageDto scene = new SceneImageDto();
        scene.setIndex(2);
        scene.setImagePath("/app/data/jobs/1/images/scene_002.png");

        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(scene));
        result.setHeldScenes(List.of(Map.of("index", 6, "reason", "장면 누적 요청 상한 도달")));
        result.setRequiresManualReview(true);

        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson(objectMapper.writeValueAsString(Map.of(
                "sections", List.of(
                        Map.of("text_for_tts", "씬0 내레이션"),
                        Map.of("text_for_tts", "씬1 내레이션"),
                        Map.of("text_for_tts", "씬2 내레이션")))));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT))
                .thenReturn(Optional.of(scriptAsset));
        when(fastApiClient.acceptHeldScene(1L, 2, "AUTO", "씬2 내레이션", null)).thenReturn(result);
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.SCENE_IMAGE)).thenReturn(List.of());
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(true);

        imagesService.acceptHeldScene(1L, 2, "AUTO");

        assertThat(job.getStatus()).isEqualTo(JobStatus.IMAGES_RETRY_REQUIRED);
        verify(imagesService, never()).confirm(eq(1L), any());
    }

    /**
     * 2026-10-13 job 14 씬5 재현: 내레이션 원문 없이 승인 이미지만 보내면
     * SCENE_IMAGE 자산의 text가 비어 longform 조립 직전에 전체가 막혔다.
     * 승인 SCRIPT 자산에 해당 인덱스 내레이션이 없으면 FastAPI 호출 전에
     * 바로 막아야 한다.
     */
    @Test
    void acceptHeldScene_throwsWhenScriptHasNoSectionForThatIndex() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.IMAGES_PENDING);

        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson(objectMapper.writeValueAsString(Map.of(
                "sections", List.of(Map.of("text_for_tts", "씬0 내레이션")))));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT))
                .thenReturn(Optional.of(scriptAsset));

        org.junit.jupiter.api.Assertions.assertThrows(IllegalStateException.class,
                () -> imagesService.acceptHeldScene(1L, 5, "SONG"));

        verify(fastApiClient, never()).acceptHeldScene(any(), anyInt(), any(), any(), any());
    }

    @Test
    void approveSceneRetry_delegatesToFastApiClientAndReturnsItsResult() {
        Map<String, Object> fastApiResult = Map.of(
                "status", "ok", "scene", "scene:11", "previous_status", "needs_review",
                "review_cycle", 1, "override_cooldown_used", true
        );
        when(fastApiClient.approveSceneRetry(18L, 11, "jam", true)).thenReturn(fastApiResult);

        Map<String, Object> result = imagesService.approveSceneRetry(18L, 11, "jam", true);

        assertThat(result).isEqualTo(fastApiResult);
        verify(fastApiClient).approveSceneRetry(18L, 11, "jam", true);
    }

    /**
     * 2026-10-15 실제 Job(id=19)에서 발견: 슬라이드형은 한 장면 생성이 실패하면
     * image_path 없이 오류만 담아 돌려준다. persistGenerateResult가 이를 NPE로
     * 받아 나머지 63개 장면 결과가 통째로 저장되지 못했다.
     */
    @Test
    void persistGenerateResult_sceneWithoutImagePath_doesNotBreakTheBatch() {
        VideoJob job = new VideoJob();
        job.setId(19L);
        SceneImageDto ok = new SceneImageDto();
        ok.setImagePath("/app/data/jobs/19/slides/scene_000_bg.png");
        SceneImageDto failed = new SceneImageDto();
        failed.setImagePath(null);
        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(ok, failed));
        result.setRequiresManualReview(true);
        when(assetRepository.findByJobIdAndAssetType(19L, AssetType.SCENE_IMAGE)).thenReturn(List.of());
        when(assetRepository.findByJobIdAndAssetType(19L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(false);

        org.springframework.test.util.ReflectionTestUtils.invokeMethod(
                imagesService, "persistGenerateResult", 19L, job, result);

        verify(assetRepository, times(2)).save(argThat(a -> a.getAssetType() == AssetType.SCENE_IMAGE));
    }

    /**
     * 2026-10-16 20번 Job: 실패했던 71번 장면(경로 없음)을 다시 생성하자 같은 번호의 자산이 둘로 남았다.
     */
    @Test
    void persistGenerateResult_regeneratedSceneReplacesTheStalePlaceholderOfTheSameIndex() {
        VideoJob job = new VideoJob();
        job.setId(20L);
        Asset stale = Asset.builder().jobId(20L).assetType(AssetType.SCENE_IMAGE).localPath(null)
                .metaJson("{\"index\":70,\"image_path\":null}").build();
        Asset other = Asset.builder().jobId(20L).assetType(AssetType.SCENE_IMAGE)
                .localPath("/app/data/jobs/20/slides/scene_069_composed_frame.png")
                .metaJson("{\"index\":69}").build();
        SceneImageDto regenerated = new SceneImageDto();
        regenerated.setIndex(70);
        regenerated.setImagePath("/app/data/jobs/20/slides/scene_070_composed_frame.png");
        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(regenerated));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.SCENE_IMAGE)).thenReturn(List.of(stale, other));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(false);

        org.springframework.test.util.ReflectionTestUtils.invokeMethod(
                imagesService, "persistGenerateResult", 20L, job, result);

        verify(assetRepository).delete(stale);
        verify(assetRepository, never()).delete(other);
        verify(assetRepository).save(argThat(a -> a.getAssetType() == AssetType.SCENE_IMAGE
                && "/app/data/jobs/20/slides/scene_070_composed_frame.png".equals(a.getLocalPath())));
    }

    @Test
    void persistGenerateResult_failedRegenerationKeepsTheGoodPreviousImageAndAddsNothing() {
        VideoJob job = new VideoJob();
        job.setId(20L);
        Asset good = Asset.builder().jobId(20L).assetType(AssetType.SCENE_IMAGE)
                .localPath("/app/data/jobs/20/slides/scene_070_composed_frame.png")
                .metaJson("{\"index\":70}").build();
        SceneImageDto failed = new SceneImageDto();
        failed.setIndex(70);
        failed.setImagePath(null);
        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(failed));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.SCENE_IMAGE)).thenReturn(List.of(good));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(false);

        org.springframework.test.util.ReflectionTestUtils.invokeMethod(
                imagesService, "persistGenerateResult", 20L, job, result);

        verify(assetRepository, never()).delete(good);
        verify(assetRepository, never()).save(argThat(a -> a.getAssetType() == AssetType.SCENE_IMAGE));
    }

    /**
     * 2026-10-18 20번 Job: 후편집·시퀀스가 없는 칠판 장면은 공용 칠판 배경 파일을 경로로 쓴다.
     * 다른 장면이 같은 파일로 이미 등록돼 있어도 이 장면은 건너뛰어지면 안 된다(장면이 사라져 조립이 대본 계약 검증에서 실패했다).
     */
    @Test
    void persistGenerateResult_sceneSharingTheBoardBackgroundPathWithAnotherSceneIsStillRegistered() {
        VideoJob job = new VideoJob();
        job.setId(20L);
        String board = "/app/data/jobs/20/slides/board_background_v4.png";
        Asset first = Asset.builder().jobId(20L).assetType(AssetType.SCENE_IMAGE).localPath(board)
                .metaJson("{\"index\":12}").build();
        SceneImageDto second = new SceneImageDto();
        second.setIndex(63);
        second.setImagePath(board);
        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(second));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.SCENE_IMAGE)).thenReturn(List.of(first));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(false);

        org.springframework.test.util.ReflectionTestUtils.invokeMethod(
                imagesService, "persistGenerateResult", 20L, job, result);

        verify(assetRepository).save(argThat(a -> a.getAssetType() == AssetType.SCENE_IMAGE
                && board.equals(a.getLocalPath()) && a.getMetaJson().contains("\"index\":63")));
    }

    @Test
    void persistGenerateResult_sameSceneAndSamePathPostedAgainIsNotRegisteredTwice() {
        VideoJob job = new VideoJob();
        job.setId(20L);
        String board = "/app/data/jobs/20/slides/board_background_v4.png";
        Asset existing = Asset.builder().jobId(20L).assetType(AssetType.SCENE_IMAGE).localPath(board)
                .metaJson("{\"index\":63}").build();
        SceneImageDto again = new SceneImageDto();
        again.setIndex(63);
        again.setImagePath(board);
        ImagesGenerateResponse result = new ImagesGenerateResponse();
        result.setScenes(List.of(again));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.SCENE_IMAGE)).thenReturn(List.of(existing));
        when(assetRepository.findByJobIdAndAssetType(20L, AssetType.IMAGE_QC_REPORT)).thenReturn(List.of());
        when(autonomyService.isAuto(job)).thenReturn(false);

        org.springframework.test.util.ReflectionTestUtils.invokeMethod(
                imagesService, "persistGenerateResult", 20L, job, result);

        verify(assetRepository, never()).save(argThat(a -> a.getAssetType() == AssetType.SCENE_IMAGE));
    }
}
