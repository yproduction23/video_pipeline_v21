package com.pipeline.video.domain;

/** 콘텐츠의 사실성 등급. 등급에 따라 근거 수집·팩트체크·대본 프롬프트가 달라진다. */
public enum ContentNature {
    /** 경제·시사 등 뉴스로 검증되는 소재. */
    FACTUAL,
    /** 화제 콘텐츠·이슈 해설. 벤치마크 분석과 웹 검색을 근거로 쓰고 불확실한 내용은 완화 표현. */
    EXPLAINER,
    /** 야담·설화 등 전해 내려오는 이야기. 창작임을 영상에서 밝힌다. */
    STORY
}
