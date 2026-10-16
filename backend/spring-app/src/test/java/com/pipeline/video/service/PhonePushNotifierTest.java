package com.pipeline.video.service;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 2026-10-16 운영자 요청: PC 앱 창이 활성이어도 폰으로 푸시가 와야 한다. 서버가 세션과 무관하게
 * ntfy로 직접 보낸다. 토픽이 비어 있으면 아무 일도 하지 않고, 전송 실패는 호출자에게 전파되지 않는다.
 */
class PhonePushNotifierTest {

    @Test
    void doesNothingWithoutATopic() {
        PhonePushNotifier notifier = new PhonePushNotifier();
        ReflectionTestUtils.setField(notifier, "topic", "");
        ReflectionTestUtils.setField(notifier, "server", "http://127.0.0.1:9");

        assertThat(notifier.enabled()).isFalse();
        notifier.push(1L, "제목", "대본 생성 완료", "상세", 3, List.of());  // 예외 없이 반환
    }

    @Test
    void staticEntryPointsAreNoOpsWhenNothingIsRegistered() {
        PhonePushNotifier.stageCompleted(1L, "제목", "대본 생성 완료", "상세");
        PhonePushNotifier.needsAttention(1L, "제목", "사유");
    }

    @Test
    void postsJsonToTheNtfyServerWithUtf8TitleAndMessage() throws Exception {
        AtomicReference<String> body = new AtomicReference<>();
        AtomicReference<String> contentType = new AtomicReference<>();
        CountDownLatch received = new CountDownLatch(1);
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            contentType.set(exchange.getRequestHeaders().getFirst("Content-Type"));
            exchange.sendResponseHeaders(200, -1);
            exchange.close();
            received.countDown();
        });
        server.start();
        try {
            PhonePushNotifier notifier = new PhonePushNotifier();
            ReflectionTestUtils.setField(notifier, "topic", "pipeline-test-topic");
            ReflectionTestUtils.setField(notifier, "server", "http://127.0.0.1:" + server.getAddress().getPort());

            notifier.push(20L, "삼성전자 급락 분석", "대본 생성 완료", "2300자 · 검토 후 확정", 3, List.of("white_check_mark"));

            assertThat(received.await(5, TimeUnit.SECONDS)).isTrue();
            assertThat(contentType.get()).isEqualTo("application/json");
            assertThat(body.get())
                    .contains("\"topic\":\"pipeline-test-topic\"")
                    .contains("Job #20 대본 생성 완료")
                    .contains("삼성전자 급락 분석")
                    .contains("2300자 · 검토 후 확정");
        } finally {
            server.stop(0);
        }
    }

    @Test
    void connectionFailureDoesNotThrow() {
        PhonePushNotifier notifier = new PhonePushNotifier();
        ReflectionTestUtils.setField(notifier, "topic", "t");
        ReflectionTestUtils.setField(notifier, "server", "http://127.0.0.1:9");

        notifier.push(1L, "제목", "대본 생성 완료", "상세", 3, List.of());
    }
}
