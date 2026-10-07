package com.pipeline.video.service;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 2026-10-07 사용자 재현(job 14, 씬 0): 분류 버그 수정 이전에 승인된 대본은
 * DB에 저장된 옛 scene_type·art_direction을 그대로 쓴다. 이미 승인되어
 * 다음 단계로 넘어간 Job도 (SCRIPT_PENDING 제한 없이) 분류 메타데이터만
 * 최신 코드로 다시 계산할 수 있어야 한다.
 */
class ScriptServiceReclassifyTest {

    @Test
    void reclassifyScenes_updatesSectionsAndSavesNewAssetWithoutTouchingNarration() {
        VideoJobRepository jobRepository = mock(VideoJobRepository.class);
        AssetRepository assetRepository = mock(AssetRepository.class);
        FastApiClient fastApiClient = mock(FastApiClient.class);
        ScriptService service = new ScriptService(
                jobRepository, assetRepository, fastApiClient, null, null, null, new JobGenerationLock()
        );

        Map<String, Object> staleScene = Map.of(
                "content", "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
                "text_for_tts", "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
                "scene_type", "metric"
        );
        Asset latest = Asset.builder()
                .id(71L)
                .jobId(14L)
                .assetType(AssetType.SCRIPT)
                .metaJson("{\"sections\":[" + toJson(staleScene) + "]}")
                .build();
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(14L, AssetType.SCRIPT))
                .thenReturn(Optional.of(latest));

        Map<String, Object> reclassifiedScene = Map.of(
                "content", staleScene.get("content"),
                "text_for_tts", staleScene.get("text_for_tts"),
                "scene_type", "general"
        );
        when(fastApiClient.reclassifyScriptScenes(any())).thenReturn(List.of(reclassifiedScene));

        Map<String, Object> result = service.reclassifyScenes(14L, "operator");

        assertThat(result.get("status")).isEqualTo("RECLASSIFIED");
        assertThat(result.get("scene_count")).isEqualTo(1);
        verify(assetRepository).save(argThatAssetJsonContains("\"scene_type\":\"general\""));
        verify(assetRepository).save(argThatAssetJsonContains(
                "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다."));
    }

    @Test
    void reclassifyScenes_failsWithoutAnExistingScriptAsset() {
        AssetRepository assetRepository = mock(AssetRepository.class);
        ScriptService service = new ScriptService(
                mock(VideoJobRepository.class), assetRepository, mock(FastApiClient.class),
                null, null, null, new JobGenerationLock()
        );
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(14L, AssetType.SCRIPT))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.reclassifyScenes(14L, "operator"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("재분류할 SCRIPT 자산이 없습니다");
    }

    private static Asset argThatAssetJsonContains(String fragment) {
        return org.mockito.ArgumentMatchers.argThat(asset -> asset.getMetaJson().contains(fragment));
    }

    private static String toJson(Map<String, Object> scene) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> entry : scene.entrySet()) {
            if (!first) sb.append(",");
            first = false;
            sb.append("\"").append(entry.getKey()).append("\":\"").append(entry.getValue()).append("\"");
        }
        return sb.append("}").toString();
    }
}
