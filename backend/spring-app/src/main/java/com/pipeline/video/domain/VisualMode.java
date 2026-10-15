package com.pipeline.video.domain;

/** 장면 이미지를 만드는 시각 모드. 모드에 따라 FastAPI의 어떤 워커(images_worker
 * vs slides_worker)가 호출되는지가 달라진다. */
public enum VisualMode {
    /** 기존 V5 캐릭터·아키타입 기반 장면 이미지. */
    CHARACTER,
    /** 캐릭터 없이 실사 배경·기사 증거 카드·통계 그래프로 구성하는 신규 파일럿. */
    SLIDE
}
