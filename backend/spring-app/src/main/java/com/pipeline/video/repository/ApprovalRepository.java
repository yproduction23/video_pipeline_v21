package com.pipeline.video.repository;

import com.pipeline.video.domain.Approval;
import com.pipeline.video.domain.GateName;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ApprovalRepository extends JpaRepository<Approval, Long> {
    List<Approval> findByJobIdOrderByCreatedAtAsc(Long jobId);
    List<Approval> findByJobIdAndGate(Long jobId, GateName gate);
    void deleteByJobId(Long jobId);
    void deleteByJobIdAndGateIn(Long jobId, java.util.Collection<GateName> gates);
}
