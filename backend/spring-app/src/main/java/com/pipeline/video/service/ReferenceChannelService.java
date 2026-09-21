package com.pipeline.video.service;

import com.pipeline.video.domain.ChannelCandidate;
import com.pipeline.video.domain.ReferenceChannel;
import com.pipeline.video.domain.ReferenceChannelStatus;
import com.pipeline.video.domain.ReferenceChannelTier;
import com.pipeline.video.dto.ReferenceChannelConfirmItem;
import com.pipeline.video.dto.ReferenceChannelCreateRequest;
import com.pipeline.video.dto.ReferenceChannelUpdateRequest;
import com.pipeline.video.repository.ReferenceChannelRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class ReferenceChannelService {

    private final ReferenceChannelRepository repository;
    private final FastApiClient fastApiClient;

    @Transactional(readOnly = true)
    public List<ReferenceChannel> list(boolean activeOnly) {
        return activeOnly
                ? repository.findByActiveTrueOrderByDisplayOrderAscIdAsc()
                : repository.findAllByOrderByDisplayOrderAscIdAsc();
    }

    @Transactional(readOnly = true)
    public List<ReferenceChannel> listForOwner(String ownerChannelId) {
        String owner = normalizeOwner(ownerChannelId);
        if (owner == null) {
            return repository.findByActiveTrueOrderByDisplayOrderAscIdAsc().stream()
                    .filter(channel -> channel.getOwnerChannelId() == null)
                    .toList();
        }
        return repository.findActiveVisibleToOwner(owner);
    }

    private static String normalizeOwner(String ownerChannelId) {
        return ownerChannelId == null || ownerChannelId.isBlank() ? null : ownerChannelId.trim();
    }

    @Transactional
    public ReferenceChannel create(ReferenceChannelCreateRequest request, String username) {
        ChannelCandidate verified = fastApiClient.resolveChannel(request.channelRef())
                .orElseThrow(() -> new IllegalArgumentException("존재하는 YouTube 채널을 확인할 수 없습니다."));
        return saveVerified(
                request.displayName(),
                verified,
                request.tier(),
                request.displayOrder(),
                username,
                request.ownerChannelId()
        );
    }

    @Transactional
    public ReferenceChannel update(long id, ReferenceChannelUpdateRequest request) {
        ReferenceChannel channel = requireChannel(id);
        channel.setDisplayName(request.displayName().trim());
        if (request.ownerChannelId() != null) {
            channel.setOwnerChannelId(normalizeOwner(request.ownerChannelId()));
        }
        if (request.tier() != null) {
            channel.setTier(request.tier());
        }
        if (request.displayOrder() != null) {
            channel.setDisplayOrder(request.displayOrder());
        }
        if (request.active() != null) {
            channel.setActive(request.active());
        }
        return repository.save(channel);
    }

    @Transactional
    public ReferenceChannel softDelete(long id) {
        ReferenceChannel channel = requireChannel(id);
        channel.setActive(false);
        return repository.save(channel);
    }

    @Transactional
    public ReferenceChannel revalidate(long id) {
        ReferenceChannel channel = requireChannel(id);
        ChannelCandidate verified = fastApiClient.resolveChannel(channel.getChannelId())
                .orElseThrow(() -> new IllegalArgumentException("존재하는 YouTube 채널을 확인할 수 없습니다."));
        applyVerifiedMetadata(channel, verified);
        channel.setValidationStatus(ReferenceChannelStatus.VALID);
        channel.setLastValidatedAt(LocalDateTime.now());
        return repository.save(channel);
    }

    @Transactional(readOnly = true)
    public List<BulkPreviewItem> preview(List<String> channelNames) {
        List<BulkPreviewItem> output = new ArrayList<>();
        for (String channelName : channelNames == null ? List.<String>of() : channelNames) {
            String normalized = channelName == null ? "" : channelName.trim();
            if (normalized.isEmpty()) {
                continue;
            }
            try {
                List<Map<String, Object>> candidates = isDirectChannelReference(normalized)
                        ? fastApiClient.resolveChannel(normalized)
                                .map(candidate -> List.of(candidateMap(candidate)))
                                .orElseGet(List::of)
                        : fastApiClient.searchChannelCandidates(normalized, 3);
                output.add(new BulkPreviewItem(
                        normalized,
                        candidates,
                        candidates.isEmpty() ? "YouTube 채널 후보를 찾지 못했습니다." : null
                ));
            } catch (RuntimeException exception) {
                output.add(new BulkPreviewItem(
                        normalized,
                        List.of(),
                        "YouTube 채널 후보 검색에 실패했습니다."
                ));
            }
        }
        return output;
    }

    private boolean isDirectChannelReference(String value) {
        return value.startsWith("UC")
                || value.startsWith("@")
                || value.startsWith("https://")
                || value.startsWith("http://");
    }

    private Map<String, Object> candidateMap(ChannelCandidate candidate) {
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("channel_id", candidate.channelId());
        output.put("title", candidate.title());
        output.put("handle", candidate.handle());
        output.put("description", candidate.description());
        output.put("thumbnail_url", candidate.thumbnailUrl());
        output.put("subscriber_count", candidate.subscriberCount());
        output.put("subscriber_count_available", candidate.subscriberCountAvailable());
        output.put("hidden_subscriber_count", candidate.hiddenSubscriberCount());
        output.put("total_view_count", candidate.totalViewCount());
        output.put("video_count", candidate.videoCount());
        return output;
    }

    @Transactional
    public BulkConfirmResult confirm(List<ReferenceChannelConfirmItem> items, String username) {
        List<ReferenceChannel> succeeded = new ArrayList<>();
        List<BulkConfirmFailure> failed = new ArrayList<>();

        for (ReferenceChannelConfirmItem item : items == null ? List.<ReferenceChannelConfirmItem>of() : items) {
            try {
                ChannelCandidate verified = fastApiClient.resolveChannel(item.channelId())
                        .orElseThrow(() -> new IllegalArgumentException("존재하는 YouTube 채널을 확인할 수 없습니다."));
                succeeded.add(saveVerified(
                        item.displayName(),
                        verified,
                        null,
                        item.displayOrder(),
                        username,
                        null
                ));
            } catch (RuntimeException exception) {
                failed.add(new BulkConfirmFailure(
                        item.channelId(),
                        exception.getMessage() == null ? "채널 저장에 실패했습니다." : exception.getMessage()
                ));
            }
        }
        return new BulkConfirmResult(succeeded, failed);
    }

    @Transactional(readOnly = true)
    public Map<String, Object> getActiveBenchmarks() {
        List<String> ids = repository.findByActiveTrueOrderByDisplayOrderAscIdAsc().stream()
                .map(ReferenceChannel::getChannelId)
                .toList();
        if (ids.isEmpty()) {
            return Map.of("status", "ok", "channels", List.of());
        }
        return fastApiClient.getChannelBenchmarks(ids);
    }

    private ReferenceChannel saveVerified(
            String displayName,
            ChannelCandidate verified,
            ReferenceChannelTier requestedTier,
            Integer displayOrder,
            String username,
            String ownerChannelId
    ) {
        if (repository.existsByChannelId(verified.channelId())) {
            throw new IllegalArgumentException("이미 등록된 YouTube 채널입니다.");
        }
        ReferenceChannel channel = ReferenceChannel.builder()
                .displayName(displayName.trim())
                .channelId(verified.channelId())
                .youtubeTitle(verified.title())
                .youtubeHandle(verified.handle())
                .thumbnailUrl(verified.thumbnailUrl())
                .subscriberCount(verified.subscriberCount())
                .subscriberCountHidden(verified.hiddenSubscriberCount())
                .tier(requestedTier != null ? requestedTier : tierFor(verified.subscriberCount()))
                .validationStatus(ReferenceChannelStatus.VALID)
                .active(true)
                .displayOrder(displayOrder != null ? displayOrder : 0)
                .ownerChannelId(normalizeOwner(ownerChannelId))
                .lastValidatedAt(LocalDateTime.now())
                .createdBy(username)
                .build();
        return repository.save(channel);
    }

    private void applyVerifiedMetadata(ReferenceChannel channel, ChannelCandidate verified) {
        channel.setYoutubeTitle(verified.title());
        channel.setYoutubeHandle(verified.handle());
        channel.setThumbnailUrl(verified.thumbnailUrl());
        channel.setSubscriberCount(verified.subscriberCount());
        channel.setSubscriberCountHidden(verified.hiddenSubscriberCount());
    }

    private ReferenceChannel requireChannel(long id) {
        return repository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("레퍼런스 채널을 찾을 수 없습니다."));
    }

    private ReferenceChannelTier tierFor(Long subscriberCount) {
        if (subscriberCount == null) {
            return ReferenceChannelTier.MEDIUM;
        }
        if (subscriberCount >= 1_000_000) {
            return ReferenceChannelTier.MEGA;
        }
        if (subscriberCount >= 300_000) {
            return ReferenceChannelTier.LARGE;
        }
        if (subscriberCount >= 50_000) {
            return ReferenceChannelTier.MEDIUM;
        }
        return ReferenceChannelTier.SMALL;
    }

    public record BulkPreviewItem(
            String query,
            List<Map<String, Object>> candidates,
            String errorMessage
    ) {
    }

    public record BulkConfirmFailure(String channelId, String errorMessage) {
    }

    public record BulkConfirmResult(
            List<ReferenceChannel> succeeded,
            List<BulkConfirmFailure> failed
    ) {
    }
}
