package com.pipeline.video.service;

import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.Autonomy;
import com.pipeline.video.domain.Category;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.ChannelProfileRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TtsServiceTest {

    @Test
    void providerCopyUsesNarrationWithoutMarkdownSceneHeadings() {
        Map<String, Object> meta = Map.of(
                "script", "## 씬 1: 급락\n첫 문장입니다.\n\n## 씬 2: 반등\n둘째 문장입니다.",
                "sections", List.of(
                        Map.of("title", "씬 1: 급락", "content", "첫 문장입니다."),
                        Map.of("title", "씬 2: 반등", "content", "둘째 문장입니다.")
                )
        );

        String providerCopy = TtsService.narrationFromMeta(meta);

        assertThat(providerCopy).isEqualTo("첫 문장입니다.\n\n둘째 문장입니다.");
        assertThat(providerCopy).doesNotContain("##", "씬 1", "급락");
    }

    @Test
    void providerCopyPrefersApprovedTtsTextOverEditorContent() {
        Map<String, Object> meta = Map.of(
                "script", "대체 원문",
                "sections", List.of(
                        Map.of(
                                "content", "약 38포인트가 빠졌습니다.",
                                "text_for_tts", "약 38퍼센트가 빠졌습니다."
                        )
                )
        );

        assertThat(TtsService.narrationFromMeta(meta))
                .isEqualTo("약 38퍼센트가 빠졌습니다.")
                .doesNotContain("포인트");
    }

    /**
     * 2026-10-06 사용자 요청: 대본 분할 버그로 문장이 중복 낭독된 TTS처럼
     * 잘못 만들어진 음성을 그냥 지울 방법이 없었다. 삭제 후에는 TTS_AUDIO
     * 자산이 지워지고, 이미지 단계로 넘어간 상태였다면 TTS_PENDING으로
     * 되돌아가 다음 단계가 더 이상 유효하지 않은 음성을 참조하지 않는다.
     */
    @Test
    void deleteAudio_removesAssetsAndRevertsStatusPastTtsPending() {
        VideoJobRepository jobRepository = mock(VideoJobRepository.class);
        AssetRepository assetRepository = mock(AssetRepository.class);
        ChannelProfileRepository channelProfileRepository = mock(ChannelProfileRepository.class);
        FastApiClient fastApiClient = mock(FastApiClient.class);
        GateService gateService = mock(GateService.class);
        AutonomyService autonomyService = mock(AutonomyService.class);
        CostService costService = mock(CostService.class);
        TtsService ttsService = new TtsService(
                jobRepository, assetRepository, channelProfileRepository,
                fastApiClient, gateService, autonomyService, costService
        );
        VideoJob job = VideoJob.builder()
                .id(14L)
                .category(Category.CUSTOM)
                .status(JobStatus.IMAGES_PENDING)
                .autonomy(Autonomy.GUIDED)
                .longformTargetMinutes(1)
                .build();
        when(jobRepository.findById(14L)).thenReturn(Optional.of(job));

        ttsService.deleteAudio(14L);

        verify(assetRepository).deleteByJobIdAndAssetType(14L, AssetType.TTS_AUDIO);
        assertThat(job.getStatus()).isEqualTo(JobStatus.TTS_PENDING);
        verify(jobRepository).save(job);
    }

    @Test
    void deleteAudio_doesNotTouchStatusBeforeScriptIsConfirmed() {
        VideoJobRepository jobRepository = mock(VideoJobRepository.class);
        AssetRepository assetRepository = mock(AssetRepository.class);
        ChannelProfileRepository channelProfileRepository = mock(ChannelProfileRepository.class);
        FastApiClient fastApiClient = mock(FastApiClient.class);
        GateService gateService = mock(GateService.class);
        AutonomyService autonomyService = mock(AutonomyService.class);
        CostService costService = mock(CostService.class);
        TtsService ttsService = new TtsService(
                jobRepository, assetRepository, channelProfileRepository,
                fastApiClient, gateService, autonomyService, costService
        );
        VideoJob job = VideoJob.builder()
                .id(15L)
                .category(Category.CUSTOM)
                .status(JobStatus.SCRIPT_PENDING)
                .autonomy(Autonomy.GUIDED)
                .longformTargetMinutes(1)
                .build();
        when(jobRepository.findById(15L)).thenReturn(Optional.of(job));

        ttsService.deleteAudio(15L);

        verify(assetRepository).deleteByJobIdAndAssetType(15L, AssetType.TTS_AUDIO);
        assertThat(job.getStatus()).isEqualTo(JobStatus.SCRIPT_PENDING);
    }
}
