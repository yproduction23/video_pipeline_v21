package com.pipeline.video.controller;

import com.pipeline.video.service.FastApiClient;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 연상 소재 라이브러리 관리(어드민 화면, 2026-10-19 운영자 요청).
 * 목록·이미지·설명으로 생성(유료, 장당 약 339원)·직접 업로드·확인/삭제를 FastAPI에 위임한다.
 * FastAPI가 돌려준 한국어 사유(422)는 화면에 그대로 보이도록 message로 전달한다.
 */
@RestController
@RequestMapping("/api/motifs")
@RequiredArgsConstructor
@PreAuthorize("hasRole('ADMIN')")
public class MotifController {

    private final FastApiClient fastApiClient;

    @GetMapping
    public Map<String, Object> list() {
        return fastApiClient.listMotifs();
    }

    @GetMapping("/{slug}/image")
    public ResponseEntity<byte[]> image(@PathVariable String slug) {
        ResponseEntity<byte[]> response = fastApiClient.getMotifImage(slug);
        if (response.getBody() == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG).body(response.getBody());
    }

    @PostMapping("/generate")
    public ResponseEntity<?> generate(@RequestBody Map<String, Object> request) {
        try {
            return ResponseEntity.ok(fastApiClient.generateMotif(
                    String.valueOf(request.getOrDefault("description", "")),
                    request.get("jobId") instanceof Number n ? n.longValue() : 0L));
        } catch (Exception e) {
            return failure(e);
        }
    }

    @PostMapping("/upload")
    public ResponseEntity<?> upload(@RequestBody Map<String, Object> request) {
        try {
            return ResponseEntity.ok(fastApiClient.uploadMotif(
                    String.valueOf(request.getOrDefault("label", "")),
                    String.valueOf(request.getOrDefault("aliases", "")),
                    String.valueOf(request.getOrDefault("imageBase64", ""))));
        } catch (Exception e) {
            return failure(e);
        }
    }

    @PostMapping("/{slug}/review")
    public ResponseEntity<?> review(@PathVariable String slug, @RequestBody Map<String, String> request) {
        try {
            return ResponseEntity.ok(fastApiClient.reviewMotif(slug, request.getOrDefault("action", "")));
        } catch (Exception e) {
            return failure(e);
        }
    }

    // ── 아이콘 라이브러리(같은 어드민 화면, 2026-10-19 운영자 요청) ──

    @GetMapping("/icons")
    public Map<String, Object> listIcons() {
        return fastApiClient.listIcons();
    }

    @GetMapping("/icons/{name}/image")
    public ResponseEntity<byte[]> iconImage(@PathVariable String name) {
        ResponseEntity<byte[]> response = fastApiClient.getIconImage(name);
        if (response.getBody() == null) {
            return ResponseEntity.notFound().build();
        }
        MediaType type = response.getHeaders().getContentType() != null ? response.getHeaders().getContentType() : MediaType.IMAGE_PNG;
        return ResponseEntity.ok().contentType(type).body(response.getBody());
    }

    @PostMapping("/icons/upload")
    public ResponseEntity<?> uploadIcon(@RequestBody Map<String, Object> request) {
        try {
            return ResponseEntity.ok(fastApiClient.uploadIcon(
                    String.valueOf(request.getOrDefault("label", "")),
                    String.valueOf(request.getOrDefault("hint", "")),
                    String.valueOf(request.getOrDefault("fileBase64", ""))));
        } catch (Exception e) {
            return failure(e);
        }
    }

    @PostMapping("/icons/{name}/delete")
    public ResponseEntity<?> deleteIcon(@PathVariable String name) {
        try {
            return ResponseEntity.ok(fastApiClient.deleteIcon(name));
        } catch (Exception e) {
            return failure(e);
        }
    }

    private ResponseEntity<Map<String, String>> failure(Exception e) {
        String message = e.getMessage() == null ? "요청에 실패했습니다." : e.getMessage();
        int start = message.indexOf("\"detail\":\"");
        if (start >= 0) {
            int from = start + 10;
            int end = message.indexOf('"', from);
            message = end > from ? message.substring(from, end) : message;
        }
        return ResponseEntity.unprocessableEntity().body(Map.of("message", message));
    }
}
