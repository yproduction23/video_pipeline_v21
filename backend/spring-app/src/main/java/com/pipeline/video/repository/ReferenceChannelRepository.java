package com.pipeline.video.repository;

import com.pipeline.video.domain.ReferenceChannel;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface ReferenceChannelRepository extends JpaRepository<ReferenceChannel, Long> {
    List<ReferenceChannel> findAllByOrderByDisplayOrderAscIdAsc();
    List<ReferenceChannel> findByActiveTrueOrderByDisplayOrderAscIdAsc();
    Optional<ReferenceChannel> findByChannelId(String channelId);
    boolean existsByChannelId(String channelId);

    @Query("select r from ReferenceChannel r where r.active = true "
            + "and (r.ownerChannelId is null or r.ownerChannelId = :ownerChannelId) "
            + "order by r.displayOrder asc, r.id asc")
    List<ReferenceChannel> findActiveVisibleToOwner(@Param("ownerChannelId") String ownerChannelId);
}
