package com.pipeline.video.repository;

import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;

public interface VideoJobRepository extends JpaRepository<VideoJob, Long> {
    List<VideoJob> findByCreatedByOrderByCreatedAtDesc(String createdBy);
    List<VideoJob> findByStatusOrderByCreatedAtDesc(JobStatus status);

    /**
     * 누적 비용 컬럼만 갱신한다. 조회 화면이 주기적으로 부르는 동기화가 작업 행 전체를 다시 쓰면, 그 사이에 바뀐 상태
     * (예: 조립 완료 → PREVIEW_PENDING)를 옛 값으로 덮어쓴다(2026-10-19 20번 Job 재현).
     */
    @Transactional
    @Modifying(clearAutomatically = false, flushAutomatically = false)
    @Query("update VideoJob j set j.costAccumulated = :cost where j.id = :id")
    int updateCostAccumulated(@Param("id") Long id, @Param("cost") BigDecimal cost);
}
