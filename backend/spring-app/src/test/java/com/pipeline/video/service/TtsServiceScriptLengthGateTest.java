package com.pipeline.video.service;

import com.pipeline.video.domain.Asset;
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
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 2026-09-29 사용자 재현: job 12가 "TTS duration is outside the allowed 5%
 * range" 오류로 실패한 뒤, 같은 승인 대본으로 "실행"을 다시 눌러도 똑같은
 * 초과 오류가 반복됐다 — 대본 길이가 그대로라 실측 발화 속도가 바뀌지 않기
 * 때문이다. FastAPI 오류 메시지 자체가 "Regenerate the script length before
 * image generation"이라고 안내하지만, Spring 쪽에는 그 안내를 실행할 경로가
 * 없어서 사용자가 영구히 막혔다. TTS 분량 실패 시 job을 SCRIPT_PENDING으로
 * 되돌려 대본 재생성 UI가 다시 열리게 한다.
 */
class TtsServiceScriptLengthGateTest {

    private VideoJobRepository jobRepository;
    private AssetRepository assetRepository;
    private FastApiClient fastApiClient;
    private TtsService ttsService;
    private VideoJob job;

    @BeforeEach
    void setUp() {
        jobRepository = mock(VideoJobRepository.class);
        assetRepository = mock(AssetRepository.class);
        ChannelProfileRepository channelProfileRepository = mock(ChannelProfileRepository.class);
        fastApiClient = mock(FastApiClient.class);
        GateService gateService = mock(GateService.class);
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

        Asset scriptAsset = Asset.builder()
                .id(45L).jobId(12L).assetType(AssetType.SCRIPT)
                .metaJson("{\"script\": \"승인된 대본 본문입니다.\"}")
                .build();
        when(assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(12L, AssetType.SCRIPT))
                .thenReturn(Optional.of(scriptAsset));
    }

    @Test
    void ttsDurationMismatchSendsJobBackToScriptPendingInsteadOfStayingStuck() {
        when(fastApiClient.generateTts(anyLong(), anyString(), any(), any(), any(), anyString()))
                .thenThrow(new RuntimeException(
                        "TTS 생성 오류: HTTP 500: {\"detail\":\"TTS 생성 실패: TTS duration is outside " +
                        "the allowed 5% range: requested=60.0s, actual=75.6s. Regenerate the script " +
                        "length before image generation.\"}"));

        assertThatThrownBy(() -> ttsService.generate(12L, "voice-1", "admin"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("다시 생성");

        assertThat(job.getStatus()).isEqualTo(JobStatus.SCRIPT_PENDING);
    }

    @Test
    void unrelatedTtsFailureDoesNotChangeJobStatus() {
        when(fastApiClient.generateTts(anyLong(), anyString(), any(), any(), any(), anyString()))
                .thenThrow(new RuntimeException("TTS 생성 오류: HTTP 502: upstream timeout"));

        assertThatThrownBy(() -> ttsService.generate(12L, "voice-1", "admin"));

        assertThat(job.getStatus()).isEqualTo(JobStatus.TTS_PENDING);
    }
}
