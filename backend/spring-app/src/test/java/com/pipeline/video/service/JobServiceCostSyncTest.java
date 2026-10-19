package com.pipeline.video.service;

import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 2026-10-19 20번 Job 재현: 조회 화면이 주기적으로 부르는 비용 동기화가 작업 행 전체를 save해서, 조립이 끝나 PREVIEW_PENDING으로 바뀐 상태를
 * 옛 값(ASSEMBLING)으로 되돌렸다(조립 완료가 어드민에 보이지 않았다). 동기화는 비용 컬럼만 갱신해야 한다.
 */
@ExtendWith(MockitoExtension.class)
class JobServiceCostSyncTest {

    @Mock
    private VideoJobRepository jobRepository;

    @Mock
    private CostService costService;

    @InjectMocks
    private JobService jobService;

    private VideoJob job(BigDecimal stored) {
        VideoJob job = new VideoJob();
        job.setId(20L);
        job.setStatus(JobStatus.ASSEMBLING);
        job.setCostAccumulated(stored);
        return job;
    }

    @Test
    void costSyncUpdatesOnlyTheCostColumnAndNeverSavesTheWholeJob() {
        VideoJob job = job(new BigDecimal("8724"));
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job));
        when(costService.calculateTotalCostKrw(20L)).thenReturn(new BigDecimal("50728"));

        JobResponse response = jobService.getJob(20L);

        verify(jobRepository).updateCostAccumulated(20L, new BigDecimal("50728"));
        verify(jobRepository, never()).save(any(VideoJob.class));
        assertThat(response.getCostAccumulated()).isEqualByComparingTo("50728");
        assertThat(job.getCostAccumulated()).isEqualByComparingTo("8724");   // 엔티티는 바꾸지 않는다(열린 트랜잭션이 행 전체를 쓰지 않게)
    }

    @Test
    void unchangedCostWritesNothing() {
        VideoJob job = job(new BigDecimal("50728"));
        when(jobRepository.findById(20L)).thenReturn(Optional.of(job));
        when(costService.calculateTotalCostKrw(20L)).thenReturn(new BigDecimal("50728"));

        jobService.getJob(20L);

        verify(jobRepository, never()).updateCostAccumulated(any(), any());
        verify(jobRepository, never()).save(any(VideoJob.class));
    }
}
