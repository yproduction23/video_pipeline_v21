package com.pipeline.video.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.SceneImageDto;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * 2026-10-13 job 14 씬5 재현: SCENE_IMAGE 자산의 text가 비어 있으면(예: 보류
 * 장면 수동 승인 경로가 narration을 빠뜨린 과거 버그) longform 조립 직전
 * "대본 원문과 장면 내레이션 불일치"로 전체 조립이 막혔다. rebuild()에만
 * 있던 SCRIPT 자산 기반 복구를 generate()에도 적용했는지 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class LongformServiceNarrationRecoveryTest {

    @Mock VideoJobRepository jobRepository;
    @Mock AssetRepository assetRepository;
    @Mock FastApiClient fastApiClient;
    @Mock GateService gateService;
    @Mock AutonomyService autonomyService;
    @Mock CostService costService;
    @Mock JobService jobService;

    private LongformService longformService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        longformService = new LongformService(
                jobRepository, assetRepository, null,
                fastApiClient, gateService, autonomyService, costService, jobService
        );
    }

    @Test
    void generate_recoversBlankSceneTextFromScriptAssetBeforeAssembling() throws Exception {
        VideoJob job = new VideoJob();
        job.setId(1L);
        job.setStatus(JobStatus.ASSEMBLING);

        Asset ttsAsset = new Asset();
        ttsAsset.setMetaJson("{\"total_duration\":60,\"chunks\":[]}");

        SceneImageDto sceneWithBlankText = new SceneImageDto();
        sceneWithBlankText.setIndex(0);
        sceneWithBlankText.setImagePath("/app/data/jobs/1/images/scene_000.png");
        // text는 의도적으로 설정하지 않는다 (null) — 수동 승인 경로가 narration을
        // 빠뜨린 과거 버그를 재현한다.
        Asset sceneAsset = new Asset();
        sceneAsset.setJobId(1L);
        sceneAsset.setAssetType(AssetType.SCENE_IMAGE);
        sceneAsset.setMetaJson(objectMapper.writeValueAsString(sceneWithBlankText));

        Asset scriptAsset = new Asset();
        scriptAsset.setMetaJson(objectMapper.writeValueAsString(Map.of(
                "sections", List.of(Map.of("text_for_tts", "복구돼야 할 씬0 내레이션")))));

        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.TTS_AUDIO))
                .thenReturn(Optional.of(ttsAsset));
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.SCENE_IMAGE))
                .thenReturn(new java.util.ArrayList<>(List.of(sceneAsset)));
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.GIF_CLIP))
                .thenReturn(new java.util.ArrayList<>());
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(1L, AssetType.SCRIPT))
                .thenReturn(Optional.of(scriptAsset));
        when(fastApiClient.generateLongform(any(), any(), any(), any()))
                .thenThrow(new RuntimeException("테스트: 복구된 scenesJson 캡처 후 중단"));

        assertThatThrownBy(() -> longformService.generate(1L, "SONG", false))
                .hasMessageContaining("테스트: 복구된 scenesJson 캡처 후 중단");

        verify(assetRepository).save(argThat(saved ->
                saved.getAssetType() == AssetType.SCENE_IMAGE
                        && saved.getMetaJson() != null
                        && saved.getMetaJson().contains("복구돼야 할 씬0 내레이션")));

        org.mockito.ArgumentCaptor<String> scenesJsonCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(fastApiClient).generateLongform(eq(1L), any(), scenesJsonCaptor.capture(), any());
        assertThat(scenesJsonCaptor.getValue()).contains("복구돼야 할 씬0 내레이션");
    }
}
