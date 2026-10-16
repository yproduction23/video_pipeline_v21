package com.pipeline.video.controller;

import com.pipeline.video.domain.Approval;
import com.pipeline.video.domain.GateName;
import com.pipeline.video.dto.GateApprovalRequest;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.service.GateService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 7개 게이트 공통 승인/거부 API
 * POST /api/jobs/{id}/gates/{gate}/approve
 * POST /api/jobs/{id}/gates/{gate}/reject
 * POST /api/jobs/{id}/gates/{gate}/rewind  (승인한 대본·TTS·이미지 단계를 다시 연다)
 * GET  /api/jobs/{id}/approvals
 *
 * Phase 2-A에서는 SHORTS_SEGMENTS / SHORTS_PREVIEW 게이트만 실전 동작.
 * 나머지 게이트는 다음 상태로 전이는 가능하나, 그 다음 단계의 워커가 없으므로 멈춤.
 */
@RestController
@RequestMapping("/api/jobs/{jobId}")
@RequiredArgsConstructor
public class GateController {

    private final GateService gateService;

    @PostMapping("/gates/{gate}/approve")
    public ResponseEntity<Approval> approve(
            @PathVariable Long jobId,
            @PathVariable GateName gate,
            @RequestBody(required = false) GateApprovalRequest request,
            @AuthenticationPrincipal String username) {
        String comment = request != null ? request.getComment() : null;
        return ResponseEntity.ok(gateService.approve(jobId, gate, username, comment));
    }

    @PostMapping("/gates/{gate}/reject")
    public ResponseEntity<Approval> reject(
            @PathVariable Long jobId,
            @PathVariable GateName gate,
            @RequestBody(required = false) GateApprovalRequest request,
            @AuthenticationPrincipal String username) {
        String comment = request != null ? request.getComment() : null;
        return ResponseEntity.ok(gateService.reject(jobId, gate, username, comment));
    }

    @PostMapping("/gates/{gate}/rewind")
    public ResponseEntity<JobResponse> rewind(
            @PathVariable Long jobId,
            @PathVariable GateName gate,
            @AuthenticationPrincipal String username) {
        return ResponseEntity.ok(JobResponse.from(gateService.rewindToGate(jobId, gate, username)));
    }

    @GetMapping("/approvals")
    public ResponseEntity<List<Approval>> getApprovals(@PathVariable Long jobId) {
        return ResponseEntity.ok(gateService.getApprovals(jobId));
    }
}
