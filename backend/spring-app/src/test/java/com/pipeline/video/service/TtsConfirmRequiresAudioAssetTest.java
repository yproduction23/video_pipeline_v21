package com.pipeline.video.service;

import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.Autonomy;
import com.pipeline.video.domain.Category;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.ChannelProfileRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 2026-09-29 사용자 재현: job 12에서 GUIDED "TTS 확정" 게이트를 눌렀더니
 * 실제 TTS 생성(jobsApi.generateTts)은 한 번도 호출되지 않은 채
 * 목소리 선택 → 게이트 승인만으로 IMAGES_PENDING까지 넘어갔다. TTS_AUDIO
 * Asset이 DB에 아예 없는 상태로 이미지 단계에 진입해 "TTS_AUDIO Asset이
 * 없습니다: 12"로 실패했다. ScriptService.confirm()은 finalScript가 비어
 * 있으면 확정을 막는 것과 달리, TtsService.confirm()은 실제 TTS_AUDIO
 * Asset 존재 여부를 전혀 확인하지 않아 품질 게이트를 우회할 수 있었다.
 */
class TtsConfirmRequiresAudioAssetTest {

    private VideoJobRepository jobRepository;
    private AssetRepository assetRepository;
    private GateService gateService;
    private TtsService ttsService;
    private VideoJob job;

    @BeforeEach
    void setUp() {
        jobRepository = mock(VideoJobRepository.class);
        assetRepository = mock(AssetRepository.class);
        ChannelProfileRepository channelProfileRepository = mock(ChannelProfileRepository.class);
        FastApiClient fastApiClient = mock(FastApiClient.class);
        gateService = mock(GateService.class);
        AutonomyService autonomyService = mock(AutonomyService.class);
        CostService costService = mock(CostService.class);
        ttsService = new TtsService(
                jobRepository, assetRepository, channelProfileRepository,
                fastApiClient, gateService, autonomyService, costService
        );
        job = VideoJob.builder()
                .id(12L)
                .keyword("DMZ 지뢰 사고 관련 정부 대응 투명성 의혹 제기")
                .category(Category.CUSTOM)
                .status(JobStatus.TTS_PENDING)
                .autonomy(Autonomy.GUIDED)
                .longformTargetMinutes(1)
                .build();
        when(jobRepository.findById(12L)).thenReturn(Optional.of(job));
    }

    @Test
    void confirmWithoutTtsAudioAssetIsRejectedInsteadOfAdvancingToImages() {
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(12L, AssetType.TTS_AUDIO))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> ttsService.confirm(12L, "admin"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("TTS");

        verify(gateService, never()).approve(any(), any(), anyString(), anyString());
        assertThat(job.getStatus()).isEqualTo(JobStatus.TTS_PENDING);
    }

    @Test
    void confirmWithExistingTtsAudioAssetApprovesTheGate() {
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(12L, AssetType.TTS_AUDIO))
                .thenReturn(Optional.of(com.pipeline.video.domain.Asset.builder()
                        .id(1L).jobId(12L).assetType(AssetType.TTS_AUDIO).localPath("/tmp/a.mp3").build()));

        ttsService.confirm(12L, "admin");

        verify(gateService).approve(eq(12L), eq(com.pipeline.video.domain.GateName.TTS), eq("admin"), anyString());
    }
}
