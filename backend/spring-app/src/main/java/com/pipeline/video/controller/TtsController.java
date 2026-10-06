package com.pipeline.video.controller;

import com.pipeline.video.dto.TtsConfirmRequest;
import com.pipeline.video.dto.TtsGenerateRequest;
import com.pipeline.video.dto.TtsGenerateResponse;
import com.pipeline.video.service.TtsService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Phase 3-3 TTS API
 *
 * 1. POST /api/jobs/{id}/tts/generate  — 스크립트 → 음성 + 청크 타이밍
 * 2. POST /api/jobs/{id}/tts/confirm   — 게이트 통과 → IMAGES_PENDING
 */
@RestController
@RequestMapping("/api/jobs/{jobId}/tts")
@RequiredArgsConstructor
public class TtsController {

    private final TtsService ttsService;

    @PostMapping("/generate")
    public ResponseEntity<TtsGenerateResponse> generate(
            @PathVariable Long jobId,
            @RequestBody(required = false) TtsGenerateRequest request,
            @AuthenticationPrincipal String username) {
        String voiceId = request != null && request.getVoiceId() != null
                ? request.getVoiceId() : "default_ko";
        return ResponseEntity.ok(ttsService.generate(jobId, voiceId, username));
    }

    @PostMapping("/select-voice")
    public ResponseEntity<Map<String, String>> selectVoice(
            @PathVariable Long jobId,
            @RequestBody TtsGenerateRequest request) {
        ttsService.selectVoice(jobId, request != null ? request.getVoiceId() : null);
        return ResponseEntity.ok(Map.of("status", "OK", "voiceId", request.getVoiceId()));
    }

    @PostMapping("/confirm")
    public ResponseEntity<Map<String, String>> confirm(
            @PathVariable Long jobId,
            @RequestBody(required = false) TtsConfirmRequest request,
            @AuthenticationPrincipal String username) {
        ttsService.confirm(jobId, username);
        return ResponseEntity.ok(Map.of("status", "OK"));
    }

    @DeleteMapping
    public ResponseEntity<Map<String, String>> delete(@PathVariable Long jobId) {
        ttsService.deleteAudio(jobId);
        return ResponseEntity.ok(Map.of("status", "OK"));
    }
}
