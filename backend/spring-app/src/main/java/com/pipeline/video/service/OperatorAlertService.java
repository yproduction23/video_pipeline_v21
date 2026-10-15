package com.pipeline.video.service;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

/**
 * Job이 사람 개입을 기다리는 상태(예: IMAGES_RETRY_REQUIRED)로 들어갈 때
 * 운영자에게 알리는 최소 경로.
 *
 * 2026-10-14 사용자 재현: AUTO 모드가 품질 게이트 때문에 자동 확정을
 * 올바르게 차단하고 IMAGES_RETRY_REQUIRED로 멈춰도, 로그 한 줄 외에는
 * 아무 알림이 없어서 운영자가 Job 목록을 직접 열어보기 전까지는 멈춘
 * 사실 자체를 몰랐다. 이 서비스는 그 공백만 메운다 — 품질 게이트 판정
 * 로직 자체는 건드리지 않는다.
 *
 * ALERT_WEBHOOK_URL이 설정되지 않으면 조용히 아무 일도 하지 않는다
 * (필수 비밀값이 아니라 선택적 알림 채널이므로, AGENTS.md의 "필수 환경
 * 변수 누락 시 시작 실패" 규칙 대상이 아니다). 전송 실패가 실제 파이프라인
 * 처리를 막아서는 안 되므로 모든 예외를 흡수하고 경고 로그만 남긴다.
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class OperatorAlertService {

    @Value("${alert.webhook-url:}")
    private String webhookUrl;

    private final RestTemplate restTemplate;

    public void notifyJobNeedsAttention(Long jobId, String title, String reason) {
        if (webhookUrl == null || webhookUrl.isBlank()) {
            log.info("ALERT_WEBHOOK_URL 미설정 — jobId={} 주의 필요 알림을 전송하지 않음 (UI에서 확인 필요)", jobId);
            return;
        }
        try {
            String text = String.format(
                "⚠️ 영상 Job #%d \"%s\" — 사람 검토 필요\n사유: %s",
                jobId, title == null ? "" : title, reason == null ? "" : reason
            );
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            HttpEntity<Map<String, String>> request = new HttpEntity<>(Map.of("text", text), headers);
            restTemplate.postForEntity(webhookUrl, request, String.class);
        } catch (Exception e) {
            // 알림 실패가 실제 파이프라인 상태 전이를 막으면 안 된다.
            log.warn("jobId={} 주의 필요 알림 전송 실패 (파이프라인 진행에는 영향 없음): {}", jobId, e.getMessage());
        }
    }
}
