package com.pipeline.video.repository;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AssetRepository extends JpaRepository<Asset, Long> {
    List<Asset> findByJobIdOrderByCreatedAtAsc(Long jobId);
    List<Asset> findByJobIdAndAssetType(Long jobId, AssetType assetType);
    // 2026-10-06 사용자 재현: 프런트는 이 타입별 목록의 마지막 원소를 "최신"으로
    // 읽는다(findByJobIdOrderByCreatedAtAsc와 같은 관례). 정렬 없는 쿼리는 행
    // 순서를 보장하지 않아, 스크립트를 재생성해도 화면에 옛 버전이 계속
    // 보였다. 목록 조회 전용으로 생성 시각 오름차순 변형을 둔다.
    List<Asset> findByJobIdAndAssetTypeOrderByCreatedAtAsc(Long jobId, AssetType assetType);
    List<Asset> findByAssetType(AssetType assetType);
    List<Asset> findByAssetTypeOrderByCreatedAtDesc(AssetType assetType);
    List<Asset> findByAssetTypeAndMetaJsonContaining(AssetType assetType, String status);
    Optional<Asset> findFirstByJobIdAndAssetType(Long jobId, AssetType assetType);

    // 가장 최근 Asset (script 최종본 조회용)
    Optional<Asset> findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(Long jobId, AssetType assetType);
    void deleteByJobId(Long jobId);
    void deleteByJobIdAndAssetType(Long jobId, AssetType assetType);
}
