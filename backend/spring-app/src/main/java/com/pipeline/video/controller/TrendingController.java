package com.pipeline.video.controller;

import com.pipeline.video.dto.TrendingVideoDto;
import com.pipeline.video.service.ReferenceChannelService;
import com.pipeline.video.service.TrendingService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@RequiredArgsConstructor
public class TrendingController {

    private final TrendingService trendingService;
    private final ReferenceChannelService referenceChannelService;

    @GetMapping("/api/trending/youtube")
    public ResponseEntity<List<TrendingVideoDto>> getTrendingYoutube(
            @RequestParam(required = false, defaultValue = "") String keyword,
            @RequestParam(required = false, defaultValue = "evidence") String ranking,
            @RequestParam(required = false) Long minSubscribers,
            @RequestParam(required = false) String channelId) {
        return ResponseEntity.ok(trendingService.getTrendingVideos(keyword, ranking, minSubscribers, channelId));
    }

    @GetMapping({"/api/youtube/channels/benchmark", "/api/trending/youtube/channels/benchmark"})
    public ResponseEntity<Object> channelBenchmark() {
        try {
            return ResponseEntity.ok(referenceChannelService.getActiveBenchmarks());
        } catch (RuntimeException exception) {
            return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(Map.of(
                    "status", "error",
                    "message", "YouTube 통계 서비스 연결 실패"
            ));
        }
    }
}
