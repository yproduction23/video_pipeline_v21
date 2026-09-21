package com.pipeline.video.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

public record BenchmarkJobRequest(
        @NotBlank(message = "벤치마크 영상 ID는 필수입니다.") String videoId,
        @NotNull @Valid CreateJobRequest job
) {
}
