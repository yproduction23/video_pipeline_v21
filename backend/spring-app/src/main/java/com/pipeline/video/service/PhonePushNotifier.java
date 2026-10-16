package com.pipeline.video.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 단계 완료·주의 필요를 운영자 폰으로 푸시한다 (ntfy 앱).
 *
 * 2026-10-16 운영자 요청: Claude 세션 알림은 PC 앱 창이 활성일 때 폰으로 가지 않는다. 자리에 있어도
 * 폰으로 와야 하므로 세션과 무관하게 서버가 직접 보낸다.
 *
 * - NTFY_TOPIC이 비어 있으면 아무 일도 하지 않는다(선택 채널, 필수 환경 변수가 아니다).
 * - 토픽 이름이 곧 구독 비밀이다. 추측 불가능한 값을 쓰고, 메시지에는 Job 번호·제목·단계 요약만 담는다.
 * - 전송은 커밋 이후 백그라운드 스레드에서 짧은 타임아웃으로 보낸다. 실패해도 파이프라인을 막지 않는다.
 * - 정적 진입점을 쓰는 이유: 서비스 클래스 생성자를 바꾸지 않고(여러 테스트가 직접 생성) 호출만 추가하기 위해서다.
 *   인스턴스가 없으면(단위 테스트) 조용한 no-op이다.
 */
@Component
@Slf4j
public class PhonePushNotifier {

    private static volatile PhonePushNotifier instance;
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Value("${alert.ntfy-topic:}")
    private String topic;

    @Value("${alert.ntfy-server:https://ntfy.sh}")
    private String server;

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(3))
            .build();
    private final ExecutorService executor = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "phone-push");
        thread.setDaemon(true);
        return thread;
    });

    @PostConstruct
    void register() {
        instance = this;
        if (enabled()) {
            log.info("폰 푸시 알림 활성화(ntfy 서버={})", server);
        } else {
            log.info("NTFY_TOPIC 미설정 — 폰 푸시 알림 비활성");
        }
    }

    /** 단계 생성이 끝나 사람이 볼 차례가 되었을 때. */
    public static void stageCompleted(Long jobId, String jobTitle, String stage, String detail) {
        PhonePushNotifier notifier = instance;
        if (notifier != null) {
            notifier.push(jobId, jobTitle, stage, detail, 3, List.of("white_check_mark"));
        }
    }

    /** 사람 개입이 필요하거나 실패했을 때(높은 우선순위). */
    public static void needsAttention(Long jobId, String jobTitle, String reason) {
        PhonePushNotifier notifier = instance;
        if (notifier != null) {
            notifier.push(jobId, jobTitle, "검토 필요", reason, 4, List.of("warning"));
        }
    }

    boolean enabled() {
        return topic != null && !topic.isBlank();
    }

    void push(Long jobId, String jobTitle, String stage, String detail, int priority, List<String> tags) {
        if (!enabled()) {
            return;
        }
        String title = String.format("Job #%d %s", jobId, stage);
        StringBuilder message = new StringBuilder();
        if (jobTitle != null && !jobTitle.isBlank()) {
            message.append(jobTitle);
        }
        if (detail != null && !detail.isBlank()) {
            if (message.length() > 0) {
                message.append("\n");
            }
            message.append(detail);
        }
        Runnable send = () -> executor.execute(() -> deliver(title, message.toString(), priority, tags));
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            // 롤백된 변경에 대해서는 알리지 않는다.
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    send.run();
                }
            });
        } else {
            send.run();
        }
    }

    private void deliver(String title, String message, int priority, List<String> tags) {
        try {
            String body = MAPPER.writeValueAsString(Map.of(
                    "topic", topic.trim(),
                    "title", title,
                    "message", message.isBlank() ? title : message,
                    "priority", priority,
                    "tags", tags
            ));
            HttpRequest request = HttpRequest.newBuilder(URI.create(server.trim()))
                    .timeout(Duration.ofSeconds(5))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() / 100 != 2) {
                log.warn("폰 푸시 전송 실패: HTTP {}", response.statusCode());
            }
        } catch (Exception e) {
            // 알림 실패가 파이프라인 처리를 막으면 안 된다.
            log.warn("폰 푸시 전송 실패(파이프라인 진행에는 영향 없음): {}", e.getMessage());
        }
    }
}
