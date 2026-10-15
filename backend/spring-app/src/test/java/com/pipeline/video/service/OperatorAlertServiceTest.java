package com.pipeline.video.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpEntity;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * 2026-10-14 사용자 재현: IMAGES_RETRY_REQUIRED 등 사람 검토가 필요한 상태로
 * Job이 들어가도 로그 한 줄 외에는 아무 알림이 없어서, AUTO 모드로 돌리면
 * 멈춘 Job을 아무도 모르는 상태가 될 수 있었다. 이 테스트는 그 알림 경로의
 * 최소 계약만 확인한다: 웹훅 미설정 시 조용히 건너뛰고, 설정 시 전송하며,
 * 전송 실패가 호출자에게 전파되지 않는다(파이프라인 처리를 막지 않음).
 */
@ExtendWith(MockitoExtension.class)
class OperatorAlertServiceTest {

    @Mock RestTemplate restTemplate;

    @Test
    void doesNothingWhenWebhookUrlIsNotConfigured() {
        OperatorAlertService service = new OperatorAlertService(restTemplate);
        ReflectionTestUtils.setField(service, "webhookUrl", "");

        service.notifyJobNeedsAttention(1L, "제목", "사유");

        verifyNoInteractions(restTemplate);
    }

    @Test
    void postsToWebhookWhenConfigured() {
        OperatorAlertService service = new OperatorAlertService(restTemplate);
        ReflectionTestUtils.setField(service, "webhookUrl", "https://hooks.example.com/alert");

        service.notifyJobNeedsAttention(42L, "홈플러스 칼부림 사건", "이미지 검수 실패");

        verify(restTemplate).postForEntity(eq("https://hooks.example.com/alert"), any(HttpEntity.class), eq(String.class));
    }

    @Test
    void swallowsWebhookFailuresWithoutThrowing() {
        OperatorAlertService service = new OperatorAlertService(restTemplate);
        ReflectionTestUtils.setField(service, "webhookUrl", "https://hooks.example.com/alert");
        when(restTemplate.postForEntity(anyString(), any(), eq(String.class)))
                .thenThrow(new RuntimeException("연결 실패"));

        service.notifyJobNeedsAttention(1L, "제목", "사유");
    }
}
