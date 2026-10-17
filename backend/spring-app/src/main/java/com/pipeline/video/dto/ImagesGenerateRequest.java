package com.pipeline.video.dto;

import lombok.Data;

import java.util.List;

@Data
public class ImagesGenerateRequest {
    /** 검토 필요 목록에서 고른 씬만 재시도할 때 지정한다. 비어 있으면 전체 씬을 처리한다. */
    private List<Integer> sceneIndices;

    /** 슬라이드형 후작업: 이미 만든 Gemini 정지 이미지를 Kling 클립으로 바꾼다(sceneIndices와 함께 사용). */
    private boolean klingUpgrade;
}
