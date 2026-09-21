package com.pipeline.video.service;

import com.pipeline.video.domain.ChannelCandidate;
import com.pipeline.video.domain.ReferenceChannel;
import com.pipeline.video.domain.ReferenceChannelStatus;
import com.pipeline.video.domain.ReferenceChannelTier;
import com.pipeline.video.dto.ReferenceChannelConfirmItem;
import com.pipeline.video.dto.ReferenceChannelCreateRequest;
import com.pipeline.video.dto.ReferenceChannelUpdateRequest;
import com.pipeline.video.repository.ReferenceChannelRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ReferenceChannelServiceTest {

    @Mock
    ReferenceChannelRepository repository;

    @Mock
    FastApiClient fastApiClient;

    private ReferenceChannelService service;

    @BeforeEach
    void setUp() {
        service = new ReferenceChannelService(repository, fastApiClient);
        lenient().when(repository.save(any(ReferenceChannel.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void createVerifiesChannelBeforeSaving() {
        ChannelCandidate candidate = candidate("UCverified", 1_500_000L);
        when(fastApiClient.resolveChannel("@verified")).thenReturn(Optional.of(candidate));
        when(repository.existsByChannelId("UCverified")).thenReturn(false);

        ReferenceChannel saved = service.create(
                new ReferenceChannelCreateRequest("검증 채널", "@verified", null, 7, null),
                "admin"
        );

        assertThat(saved.getChannelId()).isEqualTo("UCverified");
        assertThat(saved.getTier()).isEqualTo(ReferenceChannelTier.MEGA);
        assertThat(saved.getValidationStatus()).isEqualTo(ReferenceChannelStatus.VALID);
        assertThat(saved.getCreatedBy()).isEqualTo("admin");
        verify(fastApiClient).resolveChannel("@verified");
        verify(repository).save(saved);
    }

    @Test
    void createRejectsMissingChannelWithoutSaving() {
        when(fastApiClient.resolveChannel("UCmissing")).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.create(
                new ReferenceChannelCreateRequest("없는 채널", "UCmissing", null, null, null),
                "admin"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("확인할 수 없습니다");

        verify(repository, never()).save(any());
    }

    @Test
    void createRejectsDuplicateChannelId() {
        when(fastApiClient.resolveChannel("@duplicate")).thenReturn(Optional.of(candidate("UCduplicate", 10_000L)));
        when(repository.existsByChannelId("UCduplicate")).thenReturn(true);

        assertThatThrownBy(() -> service.create(
                new ReferenceChannelCreateRequest("중복 채널", "@duplicate", null, null, null),
                "admin"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("이미 등록된");

        verify(repository, never()).save(any());
    }

    @Test
    void softDeleteOnlyDeactivatesExistingRow() {
        ReferenceChannel channel = entity(1L, "UCsoft", "기존 이름", true, 10);
        when(repository.findById(1L)).thenReturn(Optional.of(channel));

        ReferenceChannel deleted = service.softDelete(1L);

        assertThat(deleted.isActive()).isFalse();
        verify(repository).save(channel);
        verify(repository, never()).delete(any());
        verify(repository, never()).deleteById(any());
    }

    @Test
    void updateChangesOnlyEditableFieldsAndKeepsChannelId() {
        ReferenceChannel channel = entity(2L, "UCimmutable", "기존 이름", true, 10);
        when(repository.findById(2L)).thenReturn(Optional.of(channel));

        ReferenceChannel updated = service.update(
                2L,
                new ReferenceChannelUpdateRequest("새 이름", ReferenceChannelTier.SMALL, 30, false, null)
        );

        assertThat(updated.getDisplayName()).isEqualTo("새 이름");
        assertThat(updated.getTier()).isEqualTo(ReferenceChannelTier.SMALL);
        assertThat(updated.getDisplayOrder()).isEqualTo(30);
        assertThat(updated.isActive()).isFalse();
        assertThat(updated.getChannelId()).isEqualTo("UCimmutable");
    }

    @Test
    void bulkPreviewDoesNotWriteDatabaseRows() {
        Map<String, Object> candidate = Map.of(
                "channel_id", "UCpreview",
                "title", "슈카월드"
        );
        when(fastApiClient.searchChannelCandidates("슈카월드", 3)).thenReturn(List.of(candidate));

        List<ReferenceChannelService.BulkPreviewItem> preview = service.preview(List.of("슈카월드"));

        assertThat(preview).hasSize(1);
        assertThat(preview.get(0).query()).isEqualTo("슈카월드");
        assertThat(preview.get(0).candidates()).containsExactly(candidate);
        assertThat(preview.get(0).errorMessage()).isNull();
        verify(fastApiClient).searchChannelCandidates("슈카월드", 3);
        verify(fastApiClient, never()).resolveChannel("슈카월드");
        verifyNoInteractions(repository);
    }

    @Test
    void bulkPreviewKeepsDirectReferenceOnResolvePathWithoutSearchQuota() {
        ChannelCandidate candidate = candidate("UCdirect", 300_000L);
        when(fastApiClient.resolveChannel("https://www.youtube.com/@verified"))
                .thenReturn(Optional.of(candidate));

        List<ReferenceChannelService.BulkPreviewItem> preview = service.preview(
                List.of("https://www.youtube.com/@verified")
        );

        assertThat(preview).hasSize(1);
        assertThat(preview.get(0).candidates().get(0).get("channel_id")).isEqualTo("UCdirect");
        verify(fastApiClient).resolveChannel("https://www.youtube.com/@verified");
        verify(fastApiClient, never()).searchChannelCandidates(any(), anyInt());
        verifyNoInteractions(repository);
    }

    @Test
    void bulkPreviewKeepsExplicitErrorForFailedNameAndContinues() {
        Map<String, Object> candidate = Map.of(
                "channel_id", "UCsecond",
                "title", "두 번째 채널"
        );
        when(fastApiClient.searchChannelCandidates("실패 채널", 3))
                .thenThrow(new IllegalStateException("worker unavailable"));
        when(fastApiClient.searchChannelCandidates("정상 채널", 3)).thenReturn(List.of(candidate));

        List<ReferenceChannelService.BulkPreviewItem> preview = service.preview(
                List.of("실패 채널", "정상 채널")
        );

        assertThat(preview).hasSize(2);
        assertThat(preview.get(0).query()).isEqualTo("실패 채널");
        assertThat(preview.get(0).candidates()).isEmpty();
        assertThat(preview.get(0).errorMessage()).isEqualTo("YouTube 채널 후보 검색에 실패했습니다.");
        assertThat(preview.get(1).candidates()).containsExactly(candidate);
        verifyNoInteractions(repository);
    }

    @Test
    void bulkConfirmRevalidatesSelectedIdBeforeSaving() {
        when(fastApiClient.resolveChannel("UCconfirm")).thenReturn(Optional.of(candidate("UCconfirm", 400_000L)));
        when(repository.existsByChannelId("UCconfirm")).thenReturn(false);

        ReferenceChannelService.BulkConfirmResult result = service.confirm(
                List.of(new ReferenceChannelConfirmItem("확정 채널", "UCconfirm", 15)),
                "admin"
        );

        assertThat(result.succeeded()).hasSize(1);
        assertThat(result.failed()).isEmpty();
        assertThat(result.succeeded().get(0).getChannelId()).isEqualTo("UCconfirm");
        verify(fastApiClient).resolveChannel("UCconfirm");
    }

    @Test
    void bulkConfirmSeparatesSuccessfulAndFailedItems() {
        when(fastApiClient.resolveChannel("UCvalid")).thenReturn(Optional.of(candidate("UCvalid", 80_000L)));
        when(fastApiClient.resolveChannel("UCmissing")).thenReturn(Optional.empty());
        when(repository.existsByChannelId("UCvalid")).thenReturn(false);

        ReferenceChannelService.BulkConfirmResult result = service.confirm(
                List.of(
                        new ReferenceChannelConfirmItem("정상", "UCvalid", 1),
                        new ReferenceChannelConfirmItem("실패", "UCmissing", 2)
                ),
                "admin"
        );

        assertThat(result.succeeded()).extracting(ReferenceChannel::getChannelId).containsExactly("UCvalid");
        assertThat(result.failed()).hasSize(1);
        assertThat(result.failed().get(0).channelId()).isEqualTo("UCmissing");
    }

    @Test
    void noActiveChannelsSkipsFastApiBenchmarkCall() {
        when(repository.findByActiveTrueOrderByDisplayOrderAscIdAsc()).thenReturn(List.of());

        Map<String, Object> result = service.getActiveBenchmarks();

        assertThat(result.get("status")).isEqualTo("ok");
        assertThat(result.get("channels")).isEqualTo(List.of());
        verify(fastApiClient, never()).getChannelBenchmarks(any());
    }

    @Test
    void activeChannelOrderIsPassedToFastApiClient() {
        List<ReferenceChannel> channels = List.of(
                entity(2L, "UCsecond", "두 번째", true, 10),
                entity(1L, "UCfirst", "첫 번째", true, 20)
        );
        when(repository.findByActiveTrueOrderByDisplayOrderAscIdAsc()).thenReturn(channels);
        when(fastApiClient.getChannelBenchmarks(List.of("UCsecond", "UCfirst")))
                .thenReturn(Map.of("status", "ok", "channels", List.of()));

        service.getActiveBenchmarks();

        verify(fastApiClient).getChannelBenchmarks(List.of("UCsecond", "UCfirst"));
    }

    @Test
    void createStoresOwnerChannelWhenProvided() {
        when(fastApiClient.resolveChannel("@owned")).thenReturn(Optional.of(candidate("UCowned", 100_000L)));
        when(repository.existsByChannelId("UCowned")).thenReturn(false);

        ReferenceChannel saved = service.create(
                new ReferenceChannelCreateRequest("소유 채널", "@owned", null, 1, "channel_a"), "admin");

        assertThat(saved.getOwnerChannelId()).isEqualTo("channel_a");
    }

    @Test
    void updateKeepsOwnerWhenRequestOwnerIsNullAndClearsWhenEmpty() {
        ReferenceChannel existing = ReferenceChannel.builder()
                .displayName("이름").channelId("UCx").ownerChannelId("channel_a").active(true).build();
        when(repository.findById(1L)).thenReturn(Optional.of(existing));

        service.update(1L, new ReferenceChannelUpdateRequest("이름", null, null, null, null));
        assertThat(existing.getOwnerChannelId()).isEqualTo("channel_a");

        service.update(1L, new ReferenceChannelUpdateRequest("이름", null, null, null, "channel_b"));
        assertThat(existing.getOwnerChannelId()).isEqualTo("channel_b");

        service.update(1L, new ReferenceChannelUpdateRequest("이름", null, null, null, ""));
        assertThat(existing.getOwnerChannelId()).isNull();
    }

    @Test
    void listForOwnerReturnsOwnedAndSharedChannels() {
        ReferenceChannel shared = ReferenceChannel.builder().displayName("공용").channelId("UCs").active(true).build();
        ReferenceChannel owned = ReferenceChannel.builder().displayName("A전용").channelId("UCa").ownerChannelId("channel_a").active(true).build();
        when(repository.findActiveVisibleToOwner("channel_a")).thenReturn(List.of(shared, owned));

        assertThat(service.listForOwner("channel_a")).containsExactly(shared, owned);
    }

    @Test
    void listForOwnerWithoutOwnerReturnsOnlySharedChannels() {
        ReferenceChannel shared = ReferenceChannel.builder().displayName("공용").channelId("UCs").active(true).build();
        ReferenceChannel owned = ReferenceChannel.builder().displayName("A전용").channelId("UCa").ownerChannelId("channel_a").active(true).build();
        when(repository.findByActiveTrueOrderByDisplayOrderAscIdAsc()).thenReturn(List.of(shared, owned));

        assertThat(service.listForOwner(" ")).containsExactly(shared);
    }

    private static ChannelCandidate candidate(String channelId, Long subscribers) {
        return new ChannelCandidate(
                channelId,
                "실제 YouTube 제목",
                "@verified",
                "설명",
                "https://img.example/channel.jpg",
                subscribers,
                subscribers != null,
                subscribers == null,
                1_000_000L,
                100L
        );
    }

    private static ReferenceChannel entity(Long id, String channelId, String displayName, boolean active, int order) {
        return ReferenceChannel.builder()
                .id(id)
                .displayName(displayName)
                .channelId(channelId)
                .youtubeTitle(displayName)
                .tier(ReferenceChannelTier.MEDIUM)
                .validationStatus(ReferenceChannelStatus.VALID)
                .active(active)
                .displayOrder(order)
                .build();
    }
}
