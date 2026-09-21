package com.pipeline.video.dto;

import com.pipeline.video.domain.ReferenceChannelTier;
import jakarta.validation.constraints.NotBlank;

/** ownerChannelId: null이면 변경 없음, 빈 문자열이면 공용으로 되돌림. */
public record ReferenceChannelUpdateRequest(
        @NotBlank(message = "표시 이름은 필수입니다.") String displayName,
        ReferenceChannelTier tier,
        Integer displayOrder,
        Boolean active,
        String ownerChannelId
) {
}
