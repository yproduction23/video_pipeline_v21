package com.pipeline.video.controller;

import com.pipeline.video.dto.ImagesConfirmRequest;
import com.pipeline.video.dto.ImagesGenerateRequest;
import com.pipeline.video.dto.ImagesGenerateResponse;
import com.pipeline.video.service.FastApiClient;
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
    private final FastApiClient fastApiClient;

    @PostMapping("/generate")
    public ResponseEntity<ImagesGenerateResponse> generate(
            @PathVariable Long jobId,
            @RequestBody(required = false) ImagesGenerateRequest request,
            @AuthenticationPrincipal String username) {
        List<Integer> sceneIndices = request == null ? null : request.getSceneIndices();
        return ResponseEntity.ok(imagesService.generate(jobId, username, sceneIndices));
    }

    /** 슬라이드형 씬별 생성 진행 상황: 장면 분할 개수와 지금까지 만들어진 장면. */
    @GetMapping("/progress")
    public ResponseEntity<Map<String, Object>> progress(@PathVariable Long jobId) {
        return ResponseEntity.ok(fastApiClient.getSlideProgress(jobId));
    }

    /** 편집 계획서만 만든다. 이미지·영상은 만들지 않는다(비용 없음). */
    @PostMapping("/plan")
    public ResponseEntity<ImagesGenerateResponse> buildEditPlan(@PathVariable Long jobId) {
        return ResponseEntity.ok(imagesService.buildEditPlan(jobId));
    }

    /** 슬라이드형 편집 계획서(저장된 것을 읽는다). */
    @GetMapping("/plan")
    public ResponseEntity<Map<String, Object>> editPlan(@PathVariable Long jobId) {
        return ResponseEntity.ok(fastApiClient.getSlideEditPlan(jobId));
    }

    @GetMapping("/progress/scene/{index}")
    public ResponseEntity<byte[]> progressSceneMedia(@PathVariable Long jobId, @PathVariable int index) {
        ResponseEntity<byte[]> response = fastApiClient.getSlideSceneMedia(jobId, index);
        if (response.getBody() == null) {
            return ResponseEntity.notFound().build();
        }
        org.springframework.http.MediaType type = response.getHeaders().getContentType() != null
                ? response.getHeaders().getContentType()
                : org.springframework.http.MediaType.APPLICATION_OCTET_STREAM;
        return ResponseEntity.ok().contentType(type).body(response.getBody());
    }

    @GetMapping("/progress/thumb/{index}")
    public ResponseEntity<byte[]> progressThumb(@PathVariable Long jobId, @PathVariable int index) {
        ResponseEntity<byte[]> response = fastApiClient.getSlideProgressThumb(jobId, index);
        if (response.getBody() == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok()
                .contentType(org.springframework.http.MediaType.IMAGE_JPEG)
                .body(response.getBody());
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

    /**
     * 2026-10-13 사용자 요청: 자동 게이트가 보류시킨 장면도 사용자가 실물 후보
     * 이미지를 직접 보고 괜찮다고 판단하면 재생성 없이 그대로 승인한다.
     */
    @PostMapping("/scenes/{index}/accept")
    public ResponseEntity<ImagesGenerateResponse> acceptHeldScene(
            @PathVariable Long jobId,
            @PathVariable int index,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(imagesService.acceptHeldScene(jobId, index, username));
    }

    /**
     * 2026-10-14 사용자 재현(job 18 scene 11): 콘텐츠 거부 반복으로
     * needs_review에 들어간 씬은 재시도 상한을 올려도 풀리지 않는다. 사람이
     * 명시적으로 승인해야만 요청 상한 카운터가 열린다. overrideCooldown이
     * false인데 24시간이 안 지났으면 409로 실패한다 — 그때 다시
     * overrideCooldown=true로 호출해야 즉시 재도전이 승인된다.
     */
    @PostMapping("/scenes/{index}/approve-retry")
    public ResponseEntity<Map<String, Object>> approveSceneRetry(
            @PathVariable Long jobId,
            @PathVariable int index,
            @RequestBody(required = false) Map<String, Boolean> body,
            @AuthenticationPrincipal String username) {
        boolean overrideCooldown = body != null && Boolean.TRUE.equals(body.get("overrideCooldown"));
        return ResponseEntity.ok(imagesService.approveSceneRetry(jobId, index, username, overrideCooldown));
    }
}
