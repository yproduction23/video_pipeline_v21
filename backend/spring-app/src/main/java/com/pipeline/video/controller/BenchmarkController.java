package com.pipeline.video.controller;

import com.pipeline.video.dto.BenchmarkJobRequest;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.service.BenchmarkService;
import com.pipeline.video.service.FastApiClient;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

@RestController
@RequiredArgsConstructor
public class BenchmarkController {

    private final FastApiClient fastApiClient;
    private final BenchmarkService benchmarkService;

    @GetMapping("/api/trending/hot-keywords")
    public Map<String, Object> hotKeywords(
            @RequestParam(defaultValue = "ALL") String category,
            @RequestParam(defaultValue = "48h") String window) {
        return fastApiClient.getHotKeywords(category, window);
    }

    @GetMapping("/api/reference-channels/recent-uploads")
    public Map<String, Object> recentUploads(
            @RequestParam(required = false) String ownerChannelId,
            @RequestParam(defaultValue = "7") int days) {
        return benchmarkService.recentUploads(ownerChannelId, Math.max(1, Math.min(days, 7)));
    }

    @PostMapping("/api/jobs/from-benchmark")
    public JobResponse fromBenchmark(
            @Valid @RequestBody BenchmarkJobRequest request,
            @AuthenticationPrincipal String username) {
        return benchmarkService.createFromBenchmark(request, username);
    }

    // Spring Boot는 기본으로 오류 메시지를 응답에 싣지 않으므로, 화면이 사유를 보이도록 message를 직접 내려준다.
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, String>> handle(ResponseStatusException exception) {
        String reason = exception.getReason() == null ? "요청을 처리하지 못했습니다." : exception.getReason();
        return ResponseEntity.status(exception.getStatusCode()).body(Map.of("message", reason));
    }
}
