package com.pipeline.video.controller;

import com.pipeline.video.dto.ScriptConfirmRequest;
import com.pipeline.video.dto.ScriptGenerateResponse;
import com.pipeline.video.service.ScriptService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Phase 3-2 스크립트 생성 API
 *
 * 1. POST /api/jobs/{id}/script/generate  — 키워드 + 분량으로 초안 생성
 * 2. POST /api/jobs/{id}/script/confirm   — 최종 스크립트 확정 → TTS_PENDING
 */
@RestController
@RequestMapping("/api/jobs/{jobId}/script")
@RequiredArgsConstructor
public class ScriptController {

    private final ScriptService scriptService;

    @PostMapping("/generate")
    public ResponseEntity<ScriptGenerateResponse> generate(
            @PathVariable Long jobId,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(scriptService.generate(jobId, username));
    }

    @PostMapping("/confirm")
    public ResponseEntity<Map<String, Object>> confirm(
            @PathVariable Long jobId,
            @RequestBody ScriptConfirmRequest request,
            @AuthenticationPrincipal String username) {
        scriptService.confirm(jobId, request.getFinalScript(), request.getSections(), username);
        return ResponseEntity.ok(Map.of(
                "status", "OK",
                "char_count", request.getFinalScript().length()
        ));
    }

    @PostMapping("/revalidate")
    public ResponseEntity<Map<String, Object>> revalidate(
            @PathVariable Long jobId,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(scriptService.revalidate(jobId, username));
    }

    /** 2026-10-07: 이미 승인된 대본의 scene_type·배경 분류만 다시 계산 (내레이션 미변경, Claude 미호출). */
    @PostMapping("/reclassify")
    public ResponseEntity<Map<String, Object>> reclassify(
            @PathVariable Long jobId,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(scriptService.reclassifyScenes(jobId, username));
    }
}
