package com.pipeline.video.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
@Slf4j
@RequiredArgsConstructor
public class BenchmarkService {

    private static final int MAX_KEYWORD_LENGTH = 40;

    private final FastApiClient fastApiClient;
    private final ReferenceChannelService referenceChannelService;
    private final JobService jobService;
    private final KeywordService keywordService;
    private final VideoJobRepository jobRepository;
    private final AssetRepository assetRepository;
    private final TransactionTemplate transactionTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public Map<String, Object> recentUploads(String ownerChannelId, int days) {
        List<String> channelIds = referenceChannelService.listForOwner(ownerChannelId).stream()
                .map(ReferenceChannel::getChannelId)
                .toList();
        if (channelIds.isEmpty()) {
            Map<String, Object> empty = new LinkedHashMap<>();
            empty.put("videos", List.of());
            empty.put("channels", List.of());
            empty.put("empty", true);
            return empty;
        }
        return fastApiClient.getRecentUploads(channelIds, days);
    }

    public JobResponse createFromBenchmark(BenchmarkJobRequest request, String username) {
        Map<String, Object> video;
        try {
            video = fastApiClient.getYoutubeVideo(request.videoId());
        } catch (ResponseStatusException e) {
            if (e.getStatusCode().value() == HttpStatus.NOT_FOUND.value()) {
                throw new ResponseStatusException(HttpStatus.CONFLICT,
                        "선택한 영상을 찾을 수 없습니다. 삭제되었거나 비공개일 수 있습니다.");
            }
            throw e;
        }

        Map<String, Object> analysis = null;
        try {
            analysis = fastApiClient.analyzeBenchmark(video);
        } catch (Exception e) {
            log.warn("벤치마크 분석 실패, 분석 없이 진행: {}", e.getMessage());
        }

        String keyword = pickKeyword(analysis, String.valueOf(video.getOrDefault("title", "")));
        CreateJobRequest job = request.job();
        job.setTitle(keyword);
        job.setKeyword(keyword);
        Map<String, Object> finalAnalysis = analysis;
        return transactionTemplate.execute(status -> persist(job, video, finalAnalysis, keyword, username));
    }

    private JobResponse persist(CreateJobRequest jobRequest, Map<String, Object> video,
                                Map<String, Object> analysis, String keyword, String username) {
        JobResponse created = jobService.createJob(jobRequest, username);
        VideoJob job = jobRepository.findById(created.getId())
                .orElseThrow(() -> new IllegalStateException("작업 생성 직후 조회 실패: " + created.getId()));
        job.setStatus(JobStatus.KEYWORD_PENDING);
        jobRepository.save(job);

        Map<String, Object> candidate = new LinkedHashMap<>();
        candidate.put("keyword", keyword);
        candidate.put("reason", "벤치마크 영상 기반 제작");
        candidate.put("content_angle", analysis == null ? null : analysis.get("title_pattern"));
        candidate.put("source", "youtube");
        candidate.put("source_videos", List.of(video));
        candidate.put("evidence_video_ids", List.of(String.valueOf(video.get("videoId"))));
        candidate.put("benchmark_analysis", analysis);

        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("job_id", created.getId());
        meta.put("seed", String.valueOf(video.getOrDefault("title", "")));
        meta.put("selection_path", "BENCHMARK");
        meta.put("candidates", List.of(candidate));
        assetRepository.save(Asset.builder()
                .jobId(created.getId())
                .assetType(AssetType.KEYWORD)
                .metaJson(toJson(meta))
                .build());

        keywordService.confirm(created.getId(), keyword, username);
        return created;
    }

    private static String pickKeyword(Map<String, Object> analysis, String title) {
        if (analysis != null && analysis.get("topic_keyword") instanceof String topic && !topic.isBlank()) {
            String trimmed = topic.trim();
            return trimmed.length() > MAX_KEYWORD_LENGTH ? trimmed.substring(0, MAX_KEYWORD_LENGTH).trim() : trimmed;
        }
        String cleaned = cleanTitle(title);
        return cleaned.isBlank() ? "벤치마크 영상" : cleaned;
    }

    static String cleanTitle(String title) {
        String cleaned = title == null ? "" : title
                .replaceAll("[\\[\\]()\"'‘’“”!?…~#]", " ")
                .replaceAll("\\s+", " ")
                .trim();
        return cleaned.length() > MAX_KEYWORD_LENGTH ? cleaned.substring(0, MAX_KEYWORD_LENGTH).trim() : cleaned;
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            return "{}";
        }
    }
}
