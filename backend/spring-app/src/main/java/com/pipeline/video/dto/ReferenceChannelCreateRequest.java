package com.pipeline.video.dto;

import com.pipeline.video.domain.ReferenceChannelTier;
import jakarta.validation.constraints.NotBlank;

public record ReferenceChannelCreateRequest(
        @NotBlank(message = "표시 이름은 필수입니다.") String displayName,
        @NotBlank(message = "채널 ID 또는 @handle은 필수입니다.") String channelRef,
        ReferenceChannelTier tier,
        Integer displayOrder,
        String ownerChannelId
) {
}
