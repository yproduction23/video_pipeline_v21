package com.pipeline.video.controller;

import com.pipeline.video.dto.ImagesConfirmRequest;
import com.pipeline.video.dto.ImagesGenerateRequest;
import com.pipeline.video.dto.ImagesGenerateResponse;
import com.pipeline.video.service.ImagesService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * Phase 3-4 이미지/GIF 생성 API
 *
 * 1. POST /api/jobs/{id}/images/generate — 씬 이미지 + GIF 생성
 * 2. POST /api/jobs/{id}/images/confirm  — 게이트 통과 → ASSEMBLING
 */
@RestController
@RequestMapping("/api/jobs/{jobId}/images")
@RequiredArgsConstructor
public class ImagesController {

    private final ImagesService imagesService;

    @PostMapping("/generate")
    public ResponseEntity<ImagesGenerateResponse> generate(
            @PathVariable Long jobId,
            @RequestBody(required = false) ImagesGenerateRequest request,
            @AuthenticationPrincipal String username) {
        List<Integer> sceneIndices = request == null ? null : request.getSceneIndices();
        return ResponseEntity.ok(imagesService.generate(jobId, username, sceneIndices));
    }

    @PostMapping("/confirm")
    public ResponseEntity<Map<String, String>> confirm(
            @PathVariable Long jobId,
            @RequestBody(required = false) ImagesConfirmRequest request,
            @AuthenticationPrincipal String username) {
        imagesService.confirm(jobId, username);
        return ResponseEntity.ok(Map.of("status", "OK"));
    }

    @PostMapping("/scenes/{index}")
    public ResponseEntity<Map<String, String>> updateScene(
            @PathVariable Long jobId,
            @PathVariable int index,
            @RequestBody Map<String, String> body,
            @AuthenticationPrincipal String username) {
        String text = body.get("text");
        String subtitleText = body.get("subtitleText");
        String section = body.get("section");
        String mode = body.get("mode"); // caption_only | image_only | text_and_image
        imagesService.updateScene(jobId, index, text, subtitleText, section, mode);
        return ResponseEntity.ok(Map.of("status", "OK"));
    }

    @PostMapping("/scenes/{index}/split")
    public ResponseEntity<Map<String, String>> splitScene(
            @PathVariable Long jobId,
            @PathVariable int index,
            @RequestBody Map<String, String> body,
            @AuthenticationPrincipal String username) {
        String part1 = body.get("part1");
        String part2 = body.get("part2");
        imagesService.splitScene(jobId, index, part1, part2);
        return ResponseEntity.ok(Map.of("status", "OK"));
    }

    @PostMapping("/scenes/{index}/kling")
    public ResponseEntity<Map<String, String>> setSceneKling(
            @PathVariable Long jobId,
            @PathVariable int index,
            @RequestBody Map<String, Boolean> body,
            @AuthenticationPrincipal String username) {
        imagesService.setSceneKling(jobId, index, Boolean.TRUE.equals(body.get("enabled")));
        return ResponseEntity.ok(Map.of("status", "OK"));
    }
}
