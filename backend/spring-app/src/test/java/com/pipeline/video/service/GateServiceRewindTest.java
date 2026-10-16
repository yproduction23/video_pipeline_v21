package com.pipeline.video.service;

import com.pipeline.video.domain.Approval;
import com.pipeline.video.domain.Autonomy;
import com.pipeline.video.domain.GateName;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.repository.ApprovalRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 2026-10-16 운영자 요청: "스텝을 넘어가버리면 이전 단계로 돌릴 수 없는 것도 문제". 승인한 단계(대본·TTS·이미지)를 다시 열어
 * 그 단계부터 다시 실행할 수 있어야 한다. 다시 연 단계와 그 뒤 단계의 승인은 해제하고, 워크플로는 앞 게이트까지 통과한 상태로 둔다.
 */
@ExtendWith(MockitoExtension.class)
class GateServiceRewindTest {

    @Mock VideoJobRepository jobRepository;
    @Mock ApprovalRepository approvalRepository;
    @Mock AutonomyService autonomyService;
    @Mock WorkflowOrchestrator workflowOrchestrator;

    private final JobGenerationLock generationLock = new JobGenerationLock();
    private GateService gateService;

    @BeforeEach
    void setUp() {
        gateService = new GateService(jobRepository, approvalRepository, autonomyService, workflowOrchestrator, generationLock);
    }

    private VideoJob job(JobStatus status, Autonomy autonomy) {
        VideoJob job = new VideoJob();
        job.setStatus(status);
        job.setAutonomy(autonomy);
        return job;
    }

    private Approval approved(GateName gate) {
        return Approval.builder().jobId(20L).gate(gate).result("APPROVED").build();
    }

    @Test
    void rewindingToImagesReopensImagesAndClearsLaterApprovals() {
        VideoJob job = job(JobStatus.PREVIEW_PENDING, Autonomy.GUIDED);
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job));
        when(approvalRepository.findByJobIdAndGate(20L, GateName.IMAGES)).thenReturn(List.of(approved(GateName.IMAGES)));

        gateService.rewindToGate(20L, GateName.IMAGES, "op");

        assertThat(job.getStatus()).isEqualTo(JobStatus.IMAGES_PENDING);
        verify(approvalRepository).deleteByJobIdAndGateIn(eq(20L), eq(List.of(
                GateName.IMAGES, GateName.PREVIEW, GateName.SHORTS_SEGMENTS, GateName.SHORTS_PREVIEW)));
        verify(jobRepository).save(job);
    }

    @Test
    void workflowIsKeptAliveAndOnlyEarlierGatesAreSignalled() {
        VideoJob job = job(JobStatus.PREVIEW_PENDING, Autonomy.GUIDED);
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job));
        when(approvalRepository.findByJobIdAndGate(20L, GateName.TTS)).thenReturn(List.of(approved(GateName.TTS)));

        gateService.rewindToGate(20L, GateName.TTS, "op");

        InOrder order = inOrder(workflowOrchestrator);
        order.verify(workflowOrchestrator).startPipeline(20L);
        order.verify(workflowOrchestrator).sendApproveSignal(20L, "KEYWORD");
        order.verify(workflowOrchestrator).sendApproveSignal(20L, "SCRIPT");
        verify(workflowOrchestrator, never()).sendApproveSignal(20L, "TTS");
        verify(workflowOrchestrator, never()).sendApproveSignal(20L, "IMAGES");
    }

    @Test
    void autoJobsCannotBeRewoundBecauseTheWorkflowRunsTheStagesItself() {
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job(JobStatus.PREVIEW_PENDING, Autonomy.AUTO)));

        assertThatThrownBy(() -> gateService.rewindToGate(20L, GateName.IMAGES, "op"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("AUTO");
        verify(approvalRepository, never()).deleteByJobIdAndGateIn(anyLong(), anyCollection());
    }

    @Test
    void publishedJobsAndRunningGenerationsAreRefused() {
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job(JobStatus.PUBLISHED, Autonomy.GUIDED)));
        assertThatThrownBy(() -> gateService.rewindToGate(20L, GateName.IMAGES, "op"))
                .isInstanceOf(IllegalStateException.class);

        when(jobRepository.findById(21L)).thenReturn(Optional.of(job(JobStatus.ASSEMBLING, Autonomy.GUIDED)));
        generationLock.tryAcquire(21L, "LONGFORM");
        assertThatThrownBy(() -> gateService.rewindToGate(21L, GateName.IMAGES, "op"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("진행 중");
    }

    @Test
    void aStageThatWasNeverApprovedCannotBeRewoundTo() {
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job(JobStatus.TTS_PENDING, Autonomy.GUIDED)));
        when(approvalRepository.findByJobIdAndGate(20L, GateName.IMAGES)).thenReturn(List.of());

        assertThatThrownBy(() -> gateService.rewindToGate(20L, GateName.IMAGES, "op"))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("승인");
    }

    @Test
    void onlyScriptTtsAndImagesCanBeReopened() {
        assertThatThrownBy(() -> gateService.rewindToGate(20L, GateName.PREVIEW, "op"))
                .isInstanceOf(IllegalArgumentException.class);
        verify(jobRepository, never()).findById(any());
    }
}
