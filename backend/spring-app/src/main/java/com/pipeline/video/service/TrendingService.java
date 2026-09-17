package com.pipeline.video.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.ChannelProfile;
import com.pipeline.video.dto.TrendingVideoDto;
import com.pipeline.video.repository.ChannelProfileRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

@Slf4j
@Service
@RequiredArgsConstructor
public class TrendingService {

    private final StringRedisTemplate redisTemplate;
    private final FastApiClient fastApiClient;
    private final ChannelProfileRepository channelProfileRepository;
    private final ObjectMapper objectMapper;

    public List<TrendingVideoDto> getTrendingVideos(String keyword, String ranking, Long minSubscribers, String channelId) {
        String normalizedRanking = switch (ranking == null ? "" : ranking) {
            case "outperformer", "large_channel" -> ranking;
            default -> "evidence";
        };
        long normalizedMinSubscribers = Math.max(0L, minSubscribers == null ? 0L : minSubscribers);
        String normalizedChannelId = channelId == null ? "" : channelId.trim();
        String redisKey = "youtube:trending:" + normalizedRanking + ":minsubs=" + normalizedMinSubscribers
                + ":channel=" + normalizedChannelId + ":" + keyword;

        List<TrendingVideoDto> videos;
        try {
            // 1. Redis Cache Hit 체크
            String cachedJson = redisTemplate.opsForValue().get(redisKey);
            if (cachedJson != null) {
                log.info("Redis Cache Hit: {}", redisKey);
                videos = objectMapper.readValue(cachedJson, new TypeReference<List<TrendingVideoDto>>() {});
                return applyChannelGenreFilter(videos, normalizedChannelId);
            }
        } catch (Exception e) {
            log.warn("Redis 조회 오류: {}", e.getMessage());
        }

        log.info("Redis Cache Miss: {}, FastAPI 수집 호출", redisKey);

        // 2. FastAPI (YouTube Data API) 호출
        int limit = 10;
        videos = fastApiClient.getTrendingVideos(keyword, limit, normalizedRanking, normalizedMinSubscribers);
        videos = videos != null ? videos : List.of();

        // 3. Redis 에 1시간 저장 (채널 필터 적용 전 원본을 캐싱해 채널별 재사용)
        try {
            if (!videos.isEmpty()) {
                String json = objectMapper.writeValueAsString(videos);
                redisTemplate.opsForValue().set(redisKey, json, Duration.ofHours(1));
                log.info("Redis 캐시 저장 성공: {} (1시간)", redisKey);
            }
        } catch (Exception e) {
            log.warn("Redis 저장 오류: {}", e.getMessage());
        }

        return applyChannelGenreFilter(videos, normalizedChannelId);
    }

    /** 선택한 채널의 제외 분야 키워드가 제목·태그에 포함된 영상을 결과에서 제거한다. */
    private List<TrendingVideoDto> applyChannelGenreFilter(List<TrendingVideoDto> videos, String channelId) {
        if (channelId.isEmpty() || videos.isEmpty()) {
            return videos;
        }
        ChannelProfile profile = channelProfileRepository.findById(channelId).orElse(null);
        if (profile == null || profile.getGenreExcluded() == null || profile.getGenreExcluded().isBlank()) {
            return videos;
        }
        List<String> excludedTerms = Arrays.stream(profile.getGenreExcluded().split(","))
                .map(String::trim)
                .filter(term -> !term.isEmpty())
                .map(term -> term.toLowerCase(Locale.KOREAN))
                .toList();
        if (excludedTerms.isEmpty()) {
            return videos;
        }
        return videos.stream()
                .filter(video -> {
                    String haystack = ((video.getTitle() == null ? "" : video.getTitle()) + " "
                            + (video.getTags() == null ? "" : String.join(" ", video.getTags())))
                            .toLowerCase(Locale.KOREAN);
                    return excludedTerms.stream().noneMatch(haystack::contains);
                })
                .toList();
    }
}
