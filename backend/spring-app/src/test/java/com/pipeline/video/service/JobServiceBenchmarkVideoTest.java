package com.pipeline.video.service;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.repository.AssetRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

/**
 * 2026-10-15 실제 Job(id=19)에서 발견된 버그를 고정한다: KeywordService.confirm()이
 * 벤치마크 KEYWORD 에셋 뒤에 항상 "candidates 없는" 선택-감사 에셋(selection_path=
 * MANUAL/EVIDENCE_TOP_RANKED)을 추가로 저장한다. loadBenchmarkVideo()가 "가장 최근"
 * 에셋만 보면 그 감사 에셋에 가려 벤치마크 영상 정보를 영영 못 찾는다 — ScriptService.
 * generate()처럼 모든 KEYWORD 에셋을 스캔해 candidates를 담은 것을 찾아야 한다.
 */
@ExtendWith(MockitoExtension.class)
class JobServiceBenchmarkVideoTest {

    @Mock
    private AssetRepository assetRepository;

    @InjectMocks
    private JobService jobService;

    private Asset asset(String metaJson) {
        return Asset.builder().jobId(19L).assetType(AssetType.KEYWORD).metaJson(metaJson).build();
    }

    private static final String BENCHMARK_ASSET_JSON = """
            {"job_id":19,"selection_path":"BENCHMARK","candidates":[{"keyword":"k","source_videos":[
            {"videoId":"v1","title":"벤치마크 실제 제목","tags":["태그1","태그2"],"description":"설명"}
            ]}]}""";

    private static final String MANUAL_AUDIT_ASSET_JSON = """
            {"selected":"k","final":true,"selection_path":"MANUAL","manual_override_evidence":true}""";

    @SuppressWarnings("unchecked")
    private Map<String, Object> invoke() {
        return (Map<String, Object>) ReflectionTestUtils.invokeMethod(jobService, "loadBenchmarkVideo", 19L);
    }

    @Test
    void findsBenchmarkVideoEvenWhenAuditAssetIsMostRecent() {
        // 실제 저장 순서: BENCHMARK 에셋이 먼저, MANUAL 감사 에셋이 나중(더 최근)
        when(assetRepository.findByJobIdAndAssetType(19L, AssetType.KEYWORD))
                .thenReturn(List.of(asset(BENCHMARK_ASSET_JSON), asset(MANUAL_AUDIT_ASSET_JSON)));

        Map<String, Object> video = invoke();

        assertThat(video).isNotNull();
        assertThat(video.get("title")).isEqualTo("벤치마크 실제 제목");
        assertThat(video.get("tags")).isEqualTo(List.of("태그1", "태그2"));
    }

    @Test
    void returnsNullWhenNoBenchmarkAssetExists() {
        when(assetRepository.findByJobIdAndAssetType(19L, AssetType.KEYWORD))
                .thenReturn(List.of(asset(MANUAL_AUDIT_ASSET_JSON)));

        assertThat(invoke()).isNull();
    }

    @Test
    void returnsNullWhenNoKeywordAssetsExist() {
        when(assetRepository.findByJobIdAndAssetType(19L, AssetType.KEYWORD)).thenReturn(List.of());

        assertThat(invoke()).isNull();
    }
}
