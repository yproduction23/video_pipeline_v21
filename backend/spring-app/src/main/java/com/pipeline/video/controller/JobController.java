package com.pipeline.video.controller;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.dto.CreateJobRequest;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.service.JobService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/jobs")
@RequiredArgsConstructor
public class JobController {

    private final JobService jobService;
    private final AssetRepository assetRepository;

    @PostMapping
    public ResponseEntity<JobResponse> createJob(
            @RequestBody CreateJobRequest request,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(jobService.createJob(request, username));
    }

    @GetMapping("/my")
    public ResponseEntity<List<JobResponse>> getMyJobs(@AuthenticationPrincipal String username) {
        return ResponseEntity.ok(jobService.getMyJobs(username));
    }

    @GetMapping("/{id}")
    public ResponseEntity<JobResponse> getJob(@PathVariable Long id) {
        return ResponseEntity.ok(jobService.getJob(id));
    }

    @GetMapping
    @PreAuthorize("hasRole('ADMIN')")
    public ResponseEntity<List<JobResponse>> getAllJobs() {
        return ResponseEntity.ok(jobService.getAllJobs());
    }

    /**
     * Asset 조회 — 페이지 재진입 시 서버 상태 복원용
     * GET /api/jobs/{id}/assets?type=KEYWORD
     */
    @GetMapping("/{id}/assets")
    public ResponseEntity<List<Asset>> getAssets(
            @PathVariable Long id,
            @RequestParam(required = false) String type) {
        if (type != null) {
            try {
                AssetType assetType = AssetType.valueOf(type.toUpperCase());
                return ResponseEntity.ok(assetRepository.findByJobIdAndAssetTypeOrderByCreatedAtAsc(id, assetType));
            } catch (IllegalArgumentException e) {
                return ResponseEntity.badRequest().build();
            }
        }
        return ResponseEntity.ok(assetRepository.findByJobIdOrderByCreatedAtAsc(id));
    }

    @PostMapping("/{id}/publish")
    public ResponseEntity<JobResponse> publishJob(
            @PathVariable Long id,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(jobService.publishVideo(id));
    }

    @PostMapping("/{id}/stop")
    public ResponseEntity<JobResponse> stopJob(
            @PathVariable Long id,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(jobService.stopJob(id, username));
    }

    @PostMapping("/{id}/retry/script")
    public ResponseEntity<JobResponse> retryFromScript(
            @PathVariable Long id,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(jobService.retryFromScript(id));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deleteJob(
            @PathVariable Long id,
            @AuthenticationPrincipal String username) {
        jobService.deleteJob(id, username);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/{id}/thumbnail/longform")
    public ResponseEntity<org.springframework.core.io.Resource> getLongformThumbnail(@PathVariable Long id) {
        try {
            String path = "/app/data/jobs/" + id + "/longform_thumbnail.png";
            java.io.File file = new java.io.File(path);
            if (!file.exists()) {
                return ResponseEntity.notFound().build();
            }
            org.springframework.core.io.Resource resource = new org.springframework.core.io.UrlResource(file.toURI());
            return ResponseEntity.ok()
                    .contentType(MediaType.IMAGE_PNG)
                    .body(resource);
        } catch (Exception e) {
            return ResponseEntity.internalServerError().build();
        }
    }

    @GetMapping("/{id}/thumbnail/shorts")
    public ResponseEntity<org.springframework.core.io.Resource> getShortsThumbnail(@PathVariable Long id) {
        try {
            String path = "/app/data/jobs/" + id + "/shorts_thumbnail.png";
            java.io.File file = new java.io.File(path);
            if (!file.exists()) {
                return ResponseEntity.notFound().build();
            }
            org.springframework.core.io.Resource resource = new org.springframework.core.io.UrlResource(file.toURI());
            return ResponseEntity.ok()
                    .contentType(MediaType.IMAGE_PNG)
                    .body(resource);
        } catch (Exception e) {
            return ResponseEntity.internalServerError().build();
        }
    }

    @GetMapping("/{id}/thumbnail/{format}/variant/{variant}")
    public ResponseEntity<org.springframework.core.io.Resource> getThumbnailVariant(
            @PathVariable Long id, @PathVariable String format, @PathVariable int variant) {
        if (!("longform".equals(format) || "shorts".equals(format)) || variant < 1 || variant > 3) {
            return ResponseEntity.badRequest().build();
        }
        try {
            java.io.File file = new java.io.File("/app/data/jobs/" + id + "/" + format + "_thumbnail_v" + variant + ".png");
            // Legacy thumbnail generators did not persist _v1 separately.
            if (!file.isFile() && variant == 1) {
                file = new java.io.File("/app/data/jobs/" + id + "/" + format + "_thumbnail.png");
            }
            if (!file.isFile()) return ResponseEntity.notFound().build();
            return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG)
                    .body(new org.springframework.core.io.UrlResource(file.toURI()));
        } catch (Exception exception) {
            return ResponseEntity.internalServerError().build();
        }
    }

    @PostMapping("/{id}/thumbnail/{format}/select")
    public ResponseEntity<java.util.Map<String, Object>> selectThumbnailVariant(
            @PathVariable Long id, @PathVariable String format, @RequestParam int variant) {
        try {
            return ResponseEntity.ok(jobService.selectThumbnailVariant(id, format, variant));
        } catch (IllegalArgumentException | IllegalStateException exception) {
            return ResponseEntity.badRequest().body(java.util.Map.of("message", exception.getMessage()));
        }
    }

    @PostMapping("/{id}/thumbnail/regenerate")
    public ResponseEntity<java.util.Map<String, Object>> regenerateThumbnail(
            @PathVariable Long id,
            @RequestParam(defaultValue = "longform") String format,
            @RequestParam(required = false) String preset) {
        try {
            return ResponseEntity.ok(jobService.regenerateThumbnail(id, format, preset));
        } catch (IllegalArgumentException | IllegalStateException exception) {
            return ResponseEntity.badRequest().body(java.util.Map.of("message", exception.getMessage()));
        }
    }
}
