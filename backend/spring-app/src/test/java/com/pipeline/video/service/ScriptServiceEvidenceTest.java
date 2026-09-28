package com.pipeline.video.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestTemplate;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class ScriptServiceEvidenceTest {

    private final ScriptService service = new ScriptService(null, null, null, null, null, null, new JobGenerationLock());

    @Test
    void extractCandidateEvidence_matchesJobKeyword() {
        List<Map<String, Object>> candidates = List.of(Map.of(
                "keyword", "반도체 전망",
                "news_articles", List.of(Map.of("url", "https://news.example/1")),
                "source_videos", List.of(Map.of("video_id", "video-1")),
                "evidence_video_ids", List.of("video-1"),
                "youtube_score", 15
        ));

        Map<String, Object> evidence = service.extractCandidateEvidence(candidates, "반도체 전망");

        assertThat(evidence).isNotNull();
        assertThat(evidence.get("news_articles")).isEqualTo(
                List.of(Map.of("url", "https://news.example/1")));
        assertThat(evidence.get("source_videos")).isEqualTo(
                List.of(Map.of("video_id", "video-1")));
    }

    @Test
    void extractCandidateEvidence_noMatchReturnsNull() {
        List<Map<String, Object>> candidates = List.of(Map.of(
                "keyword", "반도체 전망",
                "source_videos", List.of()
        ));

        assertThat(service.extractCandidateEvidence(candidates, "없는 키워드")).isNull();
        assertThat(service.extractCandidateEvidence(null, "반도체 전망")).isNull();
    }

    @Test
    void generateScriptPayload_includesCandidateEvidence() throws Exception {
        AtomicReference<String> capturedBody = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/workers/script/generate", exchange -> {
            capturedBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] response = "{}".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            FastApiClient client = new FastApiClient(new RestTemplate());
            ReflectionTestUtils.setField(client, "fastApiUrl", "http://localhost:" + server.getAddress().getPort());
            Map<String, Object> candidateEvidence = Map.of(
                    "source_videos", List.of(Map.of("video_id", "video-1")),
                    "evidence_video_ids", List.of("video-1")
            );

            client.generateScript(
                    1L, "반도체 전망", 5, "KOSPI", "{}", false,
                    null, "GUIDED", candidateEvidence);

            Map<String, Object> payload = new ObjectMapper().readValue(
                    capturedBody.get(), new TypeReference<>() {});
            assertThat(payload).containsKey("candidate_evidence");
            assertThat(payload.get("candidate_evidence")).isEqualTo(candidateEvidence);
        } finally {
            server.stop(0);
        }
    }
}
