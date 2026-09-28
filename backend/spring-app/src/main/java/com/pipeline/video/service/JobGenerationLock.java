package com.pipeline.video.service;

import org.springframework.stereotype.Component;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 같은 작업(Job)의 같은 생성 단계(스크립트·TTS·이미지·영상 조립)가 동시에
 * 두 번 실행되는 것을 막는다.
 *
 * 반자동(GUIDED) 모드에서 사용자가 "실행"을 누른 뒤 응답이 오기 전에
 * 새로고침하고 다시 누르면, 이전 요청이 끝나기 전에 같은 단계가 중복
 * 시작된다. 그러면 LLM 호출 비용이 두 배로 나가고, 두 응답 중 어느 쪽이
 * 최종적으로 저장될지도 도착 순서에 따라 달라져 예측할 수 없다.
 *
 * 이 서버는 단일 인스턴스로 배포되므로 메모리 내 잠금으로 충분하다.
 */
@Component
public class JobGenerationLock {

    private final Set<String> inFlight = ConcurrentHashMap.newKeySet();

    /** 이미 진행 중이면 false를 반환하고 아무것도 바꾸지 않는다. 아니면 잠그고 true. */
    public boolean tryAcquire(Long jobId, String stage) {
        return inFlight.add(key(jobId, stage));
    }

    public void release(Long jobId, String stage) {
        inFlight.remove(key(jobId, stage));
    }

    private static String key(Long jobId, String stage) {
        return jobId + ":" + stage;
    }
}
