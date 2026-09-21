package com.pipeline.video.service;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.ReferenceChannel;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.BenchmarkJobRequest;
import com.pipeline.video.dto.CreateJobRequest;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BenchmarkServiceTest {

    @Mock FastApiClient fastApiClient;
    @Mock ReferenceChannelService referenceChannelService;
    @Mock JobService jobService;
    @Mock KeywordService keywordService;
    @Mock VideoJobRepository jobRepository;
    @Mock AssetRepository assetRepository;
    @Mock TransactionTemplate transactionTemplate;

    private BenchmarkService service;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        service = new BenchmarkService(fastApiClient, referenceChannelService, jobService,
                keywordService, jobRepository, assetRepository, transactionTemplate);
        org.mockito.Mockito.lenient().when(transactionTemplate.execute(any()))
                .thenAnswer(invocation -> ((TransactionCallback<Object>) invocation.getArgument(0)).doInTransaction(null));
    }

    private static Map<String, Object> video() {
        return Map.of("videoId", "abcdefghijk", "title", "오늘부터 애프터마켓 오픈!! ‘이렇게’ 바뀐다", "views", 1000);
    }

    private BenchmarkJobRequest request() {
        CreateJobRequest job = new CreateJobRequest();
        job.setChannelId("channel_a");
        return new BenchmarkJobRequest("abcdefghijk", job);
    }

    private JobResponse createdJob() {
        JobResponse response = new JobResponse();
        response.setId(5L);
        VideoJob entity = VideoJob.builder().id(5L).status(JobStatus.DRAFT).build();
        when(jobRepository.findById(5L)).thenReturn(Optional.of(entity));
        return response;
    }

    @Test
    void recentUploadsWithoutChannelsSkipsYoutubeCall() {
        when(referenceChannelService.listForOwner("channel_a")).thenReturn(List.of());

        Map<String, Object> result = service.recentUploads("channel_a", 7);

        assertThat((List<?>) result.get("videos")).isEmpty();
        assertThat(result.get("empty")).isEqualTo(true);
        verifyNoInteractions(fastApiClient);
    }

    @Test
    void recentUploadsPassesChannelIdsToFastApi() {
        ReferenceChannel channel = ReferenceChannel.builder().channelId("UC1").displayName("x").build();
        when(referenceChannelService.listForOwner("channel_a")).thenReturn(List.of(channel));
        when(fastApiClient.getRecentUploads(List.of("UC1"), 7)).thenReturn(Map.of("videos", List.of(), "channels", List.of()));

        service.recentUploads("channel_a", 7);

        verify(fastApiClient).getRecentUploads(List.of("UC1"), 7);
    }

    @Test
    void createFromBenchmarkStoresEvidenceAndConfirmsKeyword() {
        when(fastApiClient.getYoutubeVideo("abcdefghijk")).thenReturn(video());
        when(fastApiClient.analyzeBenchmark(any())).thenReturn(Map.of("topic_keyword", "애프터마켓 오픈", "reasons", List.of("이유")));
        JobResponse created = createdJob();
        when(jobService.createJob(any(CreateJobRequest.class), eq("user"))).thenReturn(created);

        JobResponse result = service.createFromBenchmark(request(), "user");

        assertThat(result.getId()).isEqualTo(5L);
        ArgumentCaptor<CreateJobRequest> jobCaptor = ArgumentCaptor.forClass(CreateJobRequest.class);
        verify(jobService).createJob(jobCaptor.capture(), eq("user"));
        assertThat(jobCaptor.getValue().getKeyword()).isEqualTo("애프터마켓 오픈");
        assertThat(jobCaptor.getValue().getTitle()).isEqualTo("애프터마켓 오픈");
        assertThat(jobCaptor.getValue().getChannelId()).isEqualTo("channel_a");

        ArgumentCaptor<Asset> assetCaptor = ArgumentCaptor.forClass(Asset.class);
        verify(assetRepository).save(assetCaptor.capture());
        assertThat(assetCaptor.getValue().getAssetType()).isEqualTo(AssetType.KEYWORD);
        assertThat(assetCaptor.getValue().getMetaJson())
                .contains("benchmark_analysis").contains("BENCHMARK").contains("source_videos").contains("abcdefghijk");
        verify(keywordService).confirm(5L, "애프터마켓 오픈", "user");
    }

    @Test
    void analysisFailureDoesNotBlockCreationAndFallsBackToCleanedTitle() {
        when(fastApiClient.getYoutubeVideo("abcdefghijk")).thenReturn(video());
        when(fastApiClient.analyzeBenchmark(any())).thenThrow(new IllegalStateException("크레딧 부족"));
        JobResponse created = createdJob();
        when(jobService.createJob(any(CreateJobRequest.class), eq("user"))).thenReturn(created);

        service.createFromBenchmark(request(), "user");

        ArgumentCaptor<CreateJobRequest> jobCaptor = ArgumentCaptor.forClass(CreateJobRequest.class);
        verify(jobService).createJob(jobCaptor.capture(), eq("user"));
        assertThat(jobCaptor.getValue().getKeyword()).doesNotContain("!").doesNotContain("‘");
        ArgumentCaptor<Asset> assetCaptor = ArgumentCaptor.forClass(Asset.class);
        verify(assetRepository).save(assetCaptor.capture());
        assertThat(assetCaptor.getValue().getMetaJson()).contains("\"benchmark_analysis\":null");
        verify(keywordService).confirm(eq(5L), any(), eq("user"));
    }

    @Test
    void missingVideoBecomesConflictAndCreatesNothing() {
        when(fastApiClient.getYoutubeVideo("abcdefghijk"))
                .thenThrow(new ResponseStatusException(HttpStatus.NOT_FOUND, "없음"));

        assertThatThrownBy(() -> service.createFromBenchmark(request(), "user"))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.CONFLICT));
        verifyNoInteractions(jobService);
        verify(keywordService, never()).confirm(any(), any(), any());
    }

    @Test
    void cleanTitleRemovesDecorationAndLimitsLength() {
        String cleaned = BenchmarkService.cleanTitle("오늘부터 8시까지 애프터마켓 오픈!! 주식패턴 ‘이렇게’ 완전히 바뀐다 " + "가".repeat(60));

        assertThat(cleaned).doesNotContain("!").doesNotContain("‘").doesNotContain("’");
        assertThat(cleaned.length()).isLessThanOrEqualTo(40);
    }
}
