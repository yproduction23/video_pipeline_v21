package com.pipeline.video.dto;

import lombok.Getter;
import lombok.Setter;

import java.util.List;

@Getter
@Setter
public class CharacterLibraryGenerateRequest {
    private String characterDescription;
    private boolean regenerate;
    /** Explicitly opt in to the 5 roles × 3 emotional-state asset set. */
    private boolean includeRoleCostumes;
    /** 특정 포즈만 다시 만들 때 지정한다(예: 깨진 포즈 한 개 교정). 비어 있으면 전체 세트. */
    private List<String> poseNames;
}
