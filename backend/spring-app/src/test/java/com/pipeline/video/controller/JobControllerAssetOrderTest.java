package com.pipeline.video.controller;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.service.JobService;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

/**
 * 2026-10-06 사용자 재현(job 14): 스크립트를 "다시 생성"해 새 SCRIPT 자산이
 * 생겼는데도 화면에는 옛 버전이 계속 보였다. 프런트는 GET /assets?type=X
 * 목록의 마지막 원소를 "최신"으로 읽는데(scriptAssets[scriptAssets.length-1]),
 * 이 엔드포인트가 쓰던 findByJobIdAndAssetType은 정렬을 보장하지 않아 실제로
 * [71, 66, 67] 같은 뒤섞인 순서로 돌아왔다 — 마지막 원소가 가장 오래된
 * 자산(67)이 되어 재생성이 전혀 반영되지 않은 것처럼 보였다.
 */
class JobControllerAssetOrderTest {

    @Test
    void getAssetsByType_usesTheCreatedAtOrderedQuery_soTheLastElementIsTrulyLatest() {
        AssetRepository assetRepository = mock(AssetRepository.class);
        JobService jobService = mock(JobService.class);
        JobController controller = new JobController(jobService, assetRepository);

        Asset older = Asset.builder().id(66L).jobId(14L).assetType(AssetType.SCRIPT).build();
        Asset newest = Asset.builder().id(71L).jobId(14L).assetType(AssetType.SCRIPT).build();
        when(assetRepository.findByJobIdAndAssetTypeOrderByCreatedAtAsc(14L, AssetType.SCRIPT))
                .thenReturn(List.of(older, newest));

        List<Asset> result = controller.getAssets(14L, "SCRIPT").getBody();

        assertThat(result).containsExactly(older, newest);
        assertThat(result.get(result.size() - 1).getId()).isEqualTo(71L);
        verify(assetRepository).findByJobIdAndAssetTypeOrderByCreatedAtAsc(14L, AssetType.SCRIPT);
        verifyNoMoreInteractions(assetRepository);
    }
}
