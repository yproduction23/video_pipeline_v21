package com.pipeline.video.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.ChannelCandidate;
import com.pipeline.video.dto.*;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.util.UriComponentsBuilder;

import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Service
@Slf4j
@RequiredArgsConstructor
public class FastApiClient {

    @Value("${fastapi.url:http://fastapi-workers:8001}")
    private String fastApiUrl;

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private static final class FastApiHttpException extends RuntimeException {
        private final int statusCode;
        private final String responseBody;

        private FastApiHttpException(int statusCode, String responseBody) {
            super("HTTP " + statusCode + ": " + responseBody);
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }
    }

    // Phase 2 — 쇼츠
    public ShortsAnalyzeResponse analyzeShorts(MultipartFile file, int shortsCount, Long jobId)
            throws IOException {
        String urlStr = String.format("%s/workers/shorts/analyze?shorts_count=%d&job_id=%d",
                fastApiUrl, shortsCount, jobId);
        String boundary = UUID.randomUUID().toString().replace("-", "");
        String fileName = file.getOriginalFilename() != null ? file.getOriginalFilename() : "video.mp4";
        byte[] fileBytes = file.getBytes();

        URL url = new URL(urlStr);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(10_000);
        // Scene transcription/enrichment may process a long upload. Keep this
        // within the long-video workflow ceiling rather than timing out early.
        conn.setReadTimeout(21_600_000);
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        try (OutputStream os = conn.getOutputStream()) {
            String partHeader = "--" + boundary + "\r\n"
                    + "Content-Disposition: form-data; name=\"file\"; filename=\"" + fileName + "\"\r\n"
                    + "Content-Type: video/mp4\r\n\r\n";
            os.write(partHeader.getBytes(StandardCharsets.UTF_8));
            os.write(fileBytes);
            os.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
            os.flush();
        }
        int code = conn.getResponseCode();
        InputStream is = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String body = new String(is.readAllBytes(), StandardCharsets.UTF_8);
        if (code < 200 || code >= 300) throw new RuntimeException("FastAPI analyze 실패: " + code);
        return objectMapper.readValue(body, ShortsAnalyzeResponse.class);
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> normalizeShortsScenes(String sourceVideoPath, List<Map<String, Object>> scenes) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("source_video_path", sourceVideoPath);
            bodyMap.put("scenes", scenes);
            String responseBody = postJson(fastApiUrl + "/workers/shorts/normalize-scenes", bodyMap);
            Map<String, Object> response = objectMapper.readValue(responseBody, Map.class);
            Object normalized = response.get("scenes");
            return normalized instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
        } catch (Exception e) {
            throw new RuntimeException("FastAPI scene timeline normalization failed: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public List<ShortClipInfo> cutShorts(Long jobId, String sourceVideoPath, ShortsConfirmRequest request) {
        try {
            List<Map<String, Object>> segmentMaps = new ArrayList<>();
            for (var s : request.getSegments()) {
                Map<String, Object> seg = new HashMap<>();
                seg.put("index", s.getIndex());
                seg.put("text", s.getText() != null ? s.getText() : "");
                seg.put("start", s.getStart());
                seg.put("end", s.getEnd());
                segmentMaps.add(seg);
            }
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("source_video_path", sourceVideoPath);
            bodyMap.put("segments", segmentMaps);
            bodyMap.put("job_id", jobId);
            String responseBody = postJson(fastApiUrl + "/workers/shorts/cut", bodyMap);
            Map<String, Object> response = objectMapper.readValue(responseBody, Map.class);
            List<Map<String, Object>> rawClips = (List<Map<String, Object>>) response.get("clips");
            if (rawClips == null) return List.of();
            List<ShortClipInfo> clips = new ArrayList<>();
            for (Map<String, Object> m : rawClips) {
                ShortClipInfo info = new ShortClipInfo();
                info.setIndex((Integer) m.get("index"));
                info.setText((String) m.get("text"));
                info.setLabel((String) m.get("label"));
                info.setStart(((Number) m.get("start")).doubleValue());
                info.setEnd(((Number) m.get("end")).doubleValue());
                if (m.get("duration") instanceof Number duration) info.setDuration(duration.doubleValue());
                if (m.get("file_size_mb") instanceof Number size) info.setFileSizeMb(size.doubleValue());
                info.setOutputPath((String) m.get("output_path"));
                clips.add(info);
            }
            return clips;
        } catch (Exception e) {
            throw new RuntimeException("FastAPI cut 오류: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> extractShortsScenarios(Long jobId, List<Map<String, Object>> scenes) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            bodyMap.put("scenes", scenes);
            String responseBody = postJson(fastApiUrl + "/workers/shorts/extract-scenarios", bodyMap);
            return objectMapper.readValue(responseBody, Map.class);
        } catch (Exception e) {
            throw new RuntimeException("FastAPI extract scenarios 오류: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public ShortClipInfo cutMergeShorts(Long jobId, String sourceVideoPath, List<Map<String, Object>> segments, String outputPath) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("source_video_path", sourceVideoPath);
            bodyMap.put("segments", segments);
            bodyMap.put("job_id", jobId);
            bodyMap.put("output_path", outputPath);
            String responseBody = postJson(fastApiUrl + "/workers/shorts/cut-merge", bodyMap);
            Map<String, Object> response = objectMapper.readValue(responseBody, Map.class);
            Map<String, Object> clipMap = (Map<String, Object>) response.get("clip");
            
            ShortClipInfo info = new ShortClipInfo();
            info.setIndex((Integer) clipMap.get("index"));
            info.setText((String) clipMap.get("text"));
            info.setLabel((String) clipMap.get("label"));
            info.setStart(((Number) clipMap.get("start")).doubleValue());
            info.setEnd(((Number) clipMap.get("end")).doubleValue());
            if (clipMap.get("duration") instanceof Number duration) info.setDuration(duration.doubleValue());
            if (clipMap.get("file_size_mb") instanceof Number size) info.setFileSizeMb(size.doubleValue());
            info.setOutputPath((String) clipMap.get("output_path"));
            return info;
        } catch (Exception e) {
            throw new RuntimeException("FastAPI cut-merge 오류: " + e.getMessage(), e);
        }
    }

    // Phase 3-1 — 키워드
    public KeywordSearchResponse searchKeywords(String seed, int limit, String category,
                                                int outperformerCount, Long jobId, String autonomyMode) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("seed", seed != null ? seed : "");
            bodyMap.put("limit", limit);
            bodyMap.put("category", category);
            bodyMap.put("outperformer_count", outperformerCount);
            bodyMap.put("job_id", jobId);
            if (autonomyMode != null && !autonomyMode.isBlank()) {
                bodyMap.put("autonomy_mode", autonomyMode);
            }
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/keyword/search", bodyMap),
                    KeywordSearchResponse.class);
        } catch (Exception e) {
            throw new RuntimeException("키워드 탐색 오류: " + e.getMessage(), e);
        }
    }

    // Phase 3-2 — 스크립트
    public ScriptGenerateResponse generateScript(Long jobId, String keyword, int targetMinutes,
                                                  String category, String marketSnapshotJson, boolean dataVisualsEnabled,
                                                  String voiceId, String autonomyMode,
                                                  Map<String, Object> candidateEvidence) {
        return generateScript(jobId, keyword, targetMinutes, category, marketSnapshotJson, dataVisualsEnabled,
                voiceId, autonomyMode, candidateEvidence, null);
    }

    public ScriptGenerateResponse generateScript(Long jobId, String keyword, int targetMinutes,
                                                  String category, String marketSnapshotJson, boolean dataVisualsEnabled,
                                                  String voiceId, String autonomyMode,
                                                  Map<String, Object> candidateEvidence, String contentNature) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            if (contentNature != null && !contentNature.isBlank()) {
                bodyMap.put("content_nature", contentNature);
            }
            bodyMap.put("keyword", keyword);
            bodyMap.put("target_minutes", targetMinutes);
            bodyMap.put("category", category != null ? category : "CUSTOM");
            bodyMap.put("data_visuals_enabled", dataVisualsEnabled);
            // null도 명시적으로 전송해 FastAPI의 선택형 계약과 payload 형태를 고정한다.
            bodyMap.put("candidate_evidence", candidateEvidence);
            if (voiceId != null && !voiceId.isBlank()) {
                bodyMap.put("voice_id", voiceId);
            }
            if (autonomyMode != null && !autonomyMode.isBlank()) {
                bodyMap.put("autonomy_mode", autonomyMode);
            }
            
            if (marketSnapshotJson != null && !marketSnapshotJson.isBlank()) {
                try {
                    Map<String, Object> marketDataMap = objectMapper.readValue(marketSnapshotJson, Map.class);
                    bodyMap.put("market_data", marketDataMap);
                } catch (Exception parseEx) {
                    log.warn("marketSnapshotJson 파싱 실패: {}", parseEx.getMessage());
                }
            }
            
            try {
                return objectMapper.readValue(
                        postJson(fastApiUrl + "/workers/script/generate", bodyMap),
                        ScriptGenerateResponse.class);
            } catch (FastApiHttpException e) {
                if (e.statusCode == 422) {
                    Map<String, Object> error = objectMapper.readValue(e.responseBody, Map.class);
                    if ("SCRIPT_RESEARCH_REQUIRED".equals(error.get("error_code"))) {
                        Object rawTerms = error.get("missing_terms");
                        List<String> missingTerms = rawTerms instanceof List<?> list
                                ? list.stream().map(String::valueOf).toList() : List.of();
                        throw new ScriptResearchRequiredException(
                                String.valueOf(error.getOrDefault("message", "Script research is required.")),
                                missingTerms);
                    }
                }
                throw e;
            }
        } catch (ScriptResearchRequiredException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException("스크립트 생성 오류: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getKlingMotionPolicy() {
        try {
            String response = restTemplate.getForObject(fastApiUrl + "/pipeline/motion-policy", String.class);
            if (response == null || response.isBlank()) return Map.of();
            return objectMapper.readValue(response, Map.class);
        } catch (Exception e) {
            throw new RuntimeException("FastAPI Kling 모션 정책 조회 실패: " + e.getMessage(), e);
        }
    }

    /**
     * 수동 편집 대본도 생성 경로와 같은 하우스 스타일 하드 게이트로 검증한다.
     * 실제 활성화 여부와 숫자 추적 정책은 FastAPI 런타임 설정이 결정한다.
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> assessScriptHouseStyle(String script, String format,
                                                       List<Map<String, Object>> verifiedFacts) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("script", script != null ? script : "");
            bodyMap.put("format", "shorts".equals(format) ? "shorts" : "longform");
            bodyMap.put("verified_facts", verifiedFacts != null ? verifiedFacts : List.of());
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/script/quality-gate", bodyMap), Map.class);
        } catch (Exception e) {
            throw new RuntimeException("스크립트 하우스 스타일 검증 오류: " + e.getMessage(), e);
        }
    }

    /** 기존 Claude 의미 검토는 보존하고 배포된 최신 문장·리듬 계약만 재검증한다. */
    @SuppressWarnings("unchecked")
    public Map<String, Object> revalidateScriptFlow(String script,
                                                     Map<String, Object> narrativePlan,
                                                     Map<String, Object> previousFlow) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("script", script != null ? script : "");
            bodyMap.put("narrative_plan", narrativePlan != null ? narrativePlan : Map.of());
            bodyMap.put("previous_flow", previousFlow != null ? previousFlow : Map.of());
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/script/flow-revalidate", bodyMap), Map.class);
        } catch (Exception e) {
            throw new RuntimeException("스크립트 흐름 재검증 오류: " + e.getMessage(), e);
        }
    }

    // Phase 3-3 — TTS
    public TtsGenerateResponse generateTts(Long jobId, String script, String voiceId) {
        return generateTts(jobId, script, voiceId, null, null, null);
    }

    /**
     * TTS 생성 (배속 지정 가능).
     *
     * ttsSpeed = null이면 FastAPI 워커의 runtime_config 기본값(현재 1.3x)을 그대로 사용.
     * 태호님 피드백 "1.05배 정도 느려도 괜찮다"에 대응하려면 여기에 1.25 등을 지정하거나,
     * /pipeline/config API로 전역 기본값을 낮추면 됩니다.
     */
    public TtsGenerateResponse generateTts(Long jobId, String script, String voiceId, Double ttsSpeed) {
        return generateTts(jobId, script, voiceId, ttsSpeed, null, null);
    }

    public TtsGenerateResponse generateTts(Long jobId, String script, String voiceId, Double ttsSpeed,
                                           Integer targetMinutes, String autonomyMode) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            bodyMap.put("script", script);
            bodyMap.put("voice_id", voiceId);
            if (ttsSpeed != null) {
                bodyMap.put("tts_speed", ttsSpeed);
            }
            if (targetMinutes != null && targetMinutes > 0) {
                bodyMap.put("target_seconds", targetMinutes * 60.0);
            }
            if (autonomyMode != null) {
                bodyMap.put("autonomy_mode", autonomyMode);
            }
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/tts/generate", bodyMap),
                    TtsGenerateResponse.class);
        } catch (Exception e) {
            throw new RuntimeException("TTS 생성 오류: " + e.getMessage(), e);
        }
    }

    // Phase 3-4 — 이미지
    public ImagesGenerateResponse generateImages(Long jobId, String ttsMetaJson, String scriptMetaJson,
                                                  String characterImagePath, String characterStylePrompt,
                                                  String characterPosesDir) {
        return generateImages(jobId, ttsMetaJson, scriptMetaJson, characterImagePath,
                characterStylePrompt, characterPosesDir, null, null, null, null, null, null);
    }

    public ImagesGenerateResponse getImageBatchStatus(Long jobId) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/images/batch-status", bodyMap),
                    ImagesGenerateResponse.class);
        } catch (Exception e) {
            throw new RuntimeException("Gemini Pro Batch 상태 조회 오류: " + e.getMessage(), e);
        }
    }

    /**
     * [Sprint 3] LoRA 모델 지정 버전 이미지 생성.
     *
     * loraModelId가 null이 아닌 시 FastAPI는 fal-ai/flux-lora 엔드포인트를 사용하여
     * 캐릭터 일관성을 극대화합니다.
     */
    public ImagesGenerateResponse generateImages(Long jobId, String ttsMetaJson, String scriptMetaJson,
                                                  String characterImagePath, String characterStylePrompt,
                                                  String characterPosesDir,
                                                  String loraModelId, String loraTriggerWord,
                                                  Float loraScale, String autonomyMode,
                                                  java.math.BigDecimal budgetLimitKrw, String budgetPolicyVersion) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            bodyMap.put("tts_meta", ttsMetaJson);
            bodyMap.put("script_meta", scriptMetaJson);
            bodyMap.put("character_image_path", characterImagePath);
            bodyMap.put("character_style_prompt", characterStylePrompt);
            // [S2-4] 캐릭터 포즈 라이브러리 디렉토리
            if (characterPosesDir != null && !characterPosesDir.isBlank()) {
                bodyMap.put("character_poses_dir", characterPosesDir);
            }
            // [Sprint 3] LoRA 파라미터
            if (loraModelId != null && !loraModelId.isBlank()) {
                bodyMap.put("lora_model_id", loraModelId);
                if (loraTriggerWord != null && !loraTriggerWord.isBlank()) {
                    bodyMap.put("lora_trigger_word", loraTriggerWord);
                }
                bodyMap.put("lora_scale", loraScale != null ? loraScale : 1.0f);
            }
            if (autonomyMode != null && !autonomyMode.isBlank()) {
                bodyMap.put("autonomy_mode", autonomyMode);
            }
            if (budgetLimitKrw != null) {
                bodyMap.put("budget_limit_krw", budgetLimitKrw.intValue());
                bodyMap.put("budget_policy_version", budgetPolicyVersion);
            }
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/images/generate", bodyMap),
                    ImagesGenerateResponse.class);
        } catch (Exception e) {
            throw new RuntimeException("이미지 생성 오류: " + e.getMessage(), e);
        }
    }

    // Phase 3-5 — 롱폼
    /**
     * Regenerates a scene and returns the resulting image metadata. A caller
     * supplies promptEn only when it deliberately wants the exact existing
     * English prompt reused; otherwise the worker compiles a new one from the
     * Korean source sentence.
     */
    public SceneImageDto regenerateSceneImage(Long jobId, int index, String sourceText,
                                              String promptEn, String section,
                                              String characterImagePath, String characterStylePrompt,
                                              String characterPosesDir,
                                              SceneImageDto sceneMeta) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            bodyMap.put("index", index);
            bodyMap.put("source_text", sourceText);
            bodyMap.put("prompt_en", promptEn);
            bodyMap.put("section", section);
            bodyMap.put("character_image_path", characterImagePath);
            bodyMap.put("character_style_prompt", characterStylePrompt);
            // 단일 재생성도 전체 생성과 동일한 장면 로컬 문자·수치·표면·
            // 캐릭터 계약을 적용하도록 저장된 메타데이터 전체를 전달한다.
            bodyMap.put("scene_meta", objectMapper.convertValue(sceneMeta, Map.class));
            if (characterPosesDir != null && !characterPosesDir.isBlank()) {
                bodyMap.put("character_poses_dir", characterPosesDir);
            }
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/images/generate-single", bodyMap),
                    SceneImageDto.class);
        } catch (Exception e) {
            throw new RuntimeException("Scene image regeneration failed: " + e.getMessage(), e);
        }
    }

    public LongformGenerateResponse generateLongform(Long jobId, String ttsMetaJson,
                                                       String scenesJson, String gifsJson) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            bodyMap.put("tts_meta", ttsMetaJson);
            bodyMap.put("scenes_meta", scenesJson);
            bodyMap.put("gifs_meta", gifsJson);
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/longform/generate", bodyMap),
                    LongformGenerateResponse.class);
        } catch (Exception e) {
            throw new RuntimeException("롱폼 조립 오류: " + e.getMessage(), e);
        }
    }

    // Phase 2+ — BGM 생성
    public void generateBgm(Long jobId, String category, int durationSeconds) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            bodyMap.put("category", category != null ? category : "CUSTOM");
            bodyMap.put("duration_seconds", durationSeconds);
            postJson(fastApiUrl + "/workers/bgm/generate", bodyMap);
        } catch (Exception e) {
            log.error("BGM 생성 오류 (무시하고 계속 진행): {}", e.getMessage());
            // BGM 실패가 전체 파이프라인을 멈추게 하지 않음
        }
    }

    public List<TrendingVideoDto> getTrendingVideos(String keyword, int limit) {
        return getTrendingVideos(keyword, limit, "evidence", 0L);
    }

    public List<TrendingVideoDto> getTrendingVideos(String keyword, int limit, String ranking, long minSubscribers) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("keyword", keyword);
            bodyMap.put("limit", limit);
            bodyMap.put("ranking", ranking);
            bodyMap.put("min_subscribers", minSubscribers);
            
            String responseBody = postJson(fastApiUrl + "/workers/trending/youtube", bodyMap);
            Map<String, Object> response = objectMapper.readValue(responseBody, Map.class);
            List<Map<String, Object>> videosMap = (List<Map<String, Object>>) response.get("videos");
            
            List<TrendingVideoDto> videos = new ArrayList<>();
            if (videosMap != null) {
                for (Map<String, Object> map : videosMap) {
                    TrendingVideoDto dto = objectMapper.convertValue(map, TrendingVideoDto.class);
                    videos.add(dto);
                }
            }
            return videos;
        } catch (Exception e) {
            log.error("트렌딩 비디오 조회 오류: {}", e.getMessage());
            return List.of();
        }
    }

    public Map<String, Object> getChannelBenchmarks(List<String> channelIds) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/youtube/channels/benchmark")
                .queryParam("channel_ids", String.join(",", channelIds))
                .encode()
                .toUriString();
        try {
            return readMap(restTemplate.getForObject(url, String.class));
        } catch (Exception e) {
            log.error("채널 벤치마크 조회 오류: {}", e.getMessage());
            throw new IllegalStateException("YouTube 통계 서비스 연결 실패", e);
        }
    }

    public Map<String, Object> getHotKeywords(String category, String window) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/discovery/hot-keywords")
                .queryParam("category", category)
                .queryParam("window", window)
                .encode().toUriString();
        return getDiscovery(url);
    }

    public Map<String, Object> getRecentUploads(List<String> channelIds, int days) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/discovery/recent-uploads")
                .queryParam("channel_ids", String.join(",", channelIds))
                .queryParam("days", days)
                .encode().toUriString();
        return getDiscovery(url);
    }

    public Map<String, Object> getYoutubeVideo(String videoId) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/discovery/video/{videoId}")
                .buildAndExpand(videoId)
                .encode().toUriString();
        return getDiscovery(url);
    }

    public Map<String, Object> analyzeBenchmark(Map<String, Object> video) {
        try {
            return readMap(postJson(fastApiUrl + "/workers/benchmark/analyze", Map.of("video", video)));
        } catch (Exception e) {
            throw new IllegalStateException("벤치마크 분석 실패: " + e.getMessage(), e);
        }
    }

    private Map<String, Object> getDiscovery(String url) {
        try {
            return readMap(restTemplate.getForObject(url, String.class));
        } catch (HttpStatusCodeException e) {
            String message = "YouTube 발견 서비스 오류";
            try {
                Object detail = readMap(e.getResponseBodyAsString()).get("detail");
                if (detail != null) {
                    message = String.valueOf(detail);
                }
            } catch (Exception ignored) {
                // FastAPI 오류 본문이 JSON이 아니면 기본 메시지를 쓴다.
            }
            throw new ResponseStatusException(e.getStatusCode(), message);
        } catch (Exception e) {
            log.error("발견 서비스 호출 오류: {}", e.getMessage());
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "YouTube 발견 서비스 연결 실패");
        }
    }

    public Optional<ChannelCandidate> resolveChannel(String channelRef) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/youtube/channels/resolve")
                .queryParam("channel_ref", channelRef)
                .encode()
                .toUriString();
        try {
            Map<String, Object> response = readMap(restTemplate.getForObject(url, String.class));
            if ("ok".equals(response.get("status")) && response.get("channel") != null) {
                return Optional.of(objectMapper.convertValue(response.get("channel"), ChannelCandidate.class));
            }
        } catch (HttpClientErrorException.NotFound | HttpClientErrorException.BadRequest exception) {
            return Optional.empty();
        } catch (Exception exception) {
            log.error("YouTube 채널 검증 연결 오류: {}", exception.getClass().getSimpleName());
            throw new IllegalStateException("YouTube 채널 검증 서비스 연결 실패", exception);
        }
        return Optional.empty();
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> searchChannelCandidates(String query, int limit) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/youtube/channels/search-candidates")
                .encode()
                .toUriString();
        Map<String, Object> body = Map.of(
                "query", query,
                "limit", Math.min(Math.max(limit, 1), 3)
        );
        try {
            Map<String, Object> response = readMap(postJson(url, body));
            Object candidates = response.get("candidates");
            return candidates instanceof List<?> ? (List<Map<String, Object>>) candidates : List.of();
        } catch (Exception exception) {
            log.error("YouTube 채널 후보 검색 연결 오류: {}", exception.getClass().getSimpleName());
            throw new IllegalStateException("YouTube 채널 후보 검색 서비스 연결 실패", exception);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getManualKeywordContext(String keyword, int recentHours) {
        return getManualKeywordContext(keyword, recentHours, "KOSPI");
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getManualKeywordContext(String keyword, int recentHours, String category) {
        try {
            Map<String, Object> body = new HashMap<>();
            body.put("keyword", keyword);
            body.put("recent_hours", recentHours);
            body.put("category", category == null || category.isBlank() ? "KOSPI" : category);
            return objectMapper.readValue(postJson(fastApiUrl + "/workers/keyword/manual-context", body), Map.class);
        } catch (Exception e) {
            throw new RuntimeException("수동 키워드 최신 근거 조회 오류: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> buildKeywordMindMap(String keyword, List<Map<String, Object>> videos) {
        try {
            Map<String, Object> body = new HashMap<>();
            body.put("keyword", keyword);
            body.put("videos", videos != null ? videos : List.of());
            return objectMapper.readValue(postJson(fastApiUrl + "/ai/keyword-mindmap", body), Map.class);
        } catch (Exception e) {
            throw new RuntimeException("키워드 마인드맵 생성 오류: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> buildKeywordPlans(Map<String, Object> request) {
        try {
            return objectMapper.readValue(postJson(fastApiUrl + "/ai/keyword-plan", request), Map.class);
        } catch (Exception e) {
            throw new RuntimeException("키워드 기획 생성 오류: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> generateYoutubeMetadata(String scriptText, boolean isShorts) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("script_text", scriptText);
            bodyMap.put("is_shorts", isShorts);
            String responseBody = postJson(fastApiUrl + "/workers/youtube/metadata", bodyMap);
            return objectMapper.readValue(responseBody, Map.class);
        } catch (Exception e) {
            throw new RuntimeException("유튜브 메타데이터 생성 오류: " + e.getMessage(), e);
        }
    }

    public void generateThumbnailImage(Long jobId, String title, String format, String outputPath, 
                                       String characterImagePath, String characterStylePrompt,
                                       String loraModelId, String loraTriggerWord, Double loraScale) {
        generateThumbnailImage(jobId, title, format, outputPath, characterImagePath, characterStylePrompt,
                loraModelId, loraTriggerWord, loraScale, java.util.List.of(), java.util.Map.of(),
                java.util.Map.of(), java.util.List.of(), null, "black_han_sans_v1", null, false);
    }

    public java.util.Map<String, Object> generateThumbnailImage(Long jobId, String title, String format, String outputPath,
                                       String characterImagePath, String characterStylePrompt,
                                       String loraModelId, String loraTriggerWord, Double loraScale,
                                       java.util.List<java.util.Map<String, Object>> sceneCandidates,
                                       java.util.Map<String, Object> thumbnailBrief,
                                       java.util.Map<String, Object> characterIdentity,
                                       java.util.List<java.util.Map<String, Object>> personPhotos,
                                       String watermarkPath) {
        return generateThumbnailImage(jobId, title, format, outputPath, characterImagePath, characterStylePrompt,
                loraModelId, loraTriggerWord, loraScale, sceneCandidates, thumbnailBrief, characterIdentity,
                personPhotos, watermarkPath, "black_han_sans_v1", null, false);
    }

    public java.util.Map<String, Object> generateThumbnailImage(Long jobId, String title, String format, String outputPath,
                                       String characterImagePath, String characterStylePrompt,
                                       String loraModelId, String loraTriggerWord, Double loraScale,
                                       java.util.List<java.util.Map<String, Object>> sceneCandidates,
                                       java.util.Map<String, Object> thumbnailBrief,
                                       java.util.Map<String, Object> characterIdentity,
                                       java.util.List<java.util.Map<String, Object>> personPhotos,
                                       String watermarkPath, String referenceStyleProfile, String preset,
                                       boolean regeneration) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            bodyMap.put("title", title);
            bodyMap.put("format", format);
            bodyMap.put("output_path", outputPath);
            bodyMap.put("character_image_path", characterImagePath);
            bodyMap.put("character_style_prompt", characterStylePrompt);
            bodyMap.put("lora_model_id", loraModelId);
            bodyMap.put("lora_trigger_word", loraTriggerWord);
            bodyMap.put("lora_scale", loraScale);
            bodyMap.put("scene_candidates", sceneCandidates == null ? java.util.List.of() : sceneCandidates);
            bodyMap.put("thumbnail_brief", thumbnailBrief == null ? java.util.Map.of() : thumbnailBrief);
            bodyMap.put("character_identity", characterIdentity == null ? java.util.Map.of() : characterIdentity);
            bodyMap.put("person_photos", personPhotos == null ? java.util.List.of() : personPhotos);
            bodyMap.put("reference_style_profile", referenceStyleProfile == null || referenceStyleProfile.isBlank()
                    ? "black_han_sans_v1" : referenceStyleProfile);
            if (preset != null && !preset.isBlank()) bodyMap.put("preset", preset);
            if (watermarkPath != null && !watermarkPath.isBlank()) {
                bodyMap.put("watermark_path", watermarkPath);
            }
            String response = postJson(fastApiUrl + (regeneration ? "/workers/thumbnail/regenerate" : "/workers/youtube/thumbnail"), bodyMap);
            return objectMapper.readValue(response, Map.class);
        } catch (Exception e) {
            throw new RuntimeException("유튜브 썸네일 생성 오류: " + e.getMessage(), e);
        }
    }

    public void stopJob(Long jobId) {
        try {
            Map<String, Object> bodyMap = new HashMap<>();
            bodyMap.put("job_id", jobId);
            postJson(fastApiUrl + "/workers/jobs/" + jobId + "/stop", bodyMap);
        } catch (Exception e) {
            log.error("FastAPI 작업 중지 통지 실패: jobId={}, error={}", jobId, e.getMessage());
        }
    }

    // ============================
    // [Sprint 3] LoRA 캐릭터 파인튜닝 API
    // ============================

    /**
     * [Sprint 3] LoRA 학습 시작.
     * ZIP 파일을 FastAPI에 multipart/form-data로 전송하고 학습 request_id를 반환.
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> startLoraTraining(
            String channelId, byte[] zipBytes, String triggerWord, int steps, boolean isStyle)
            throws IOException {
        String urlStr = String.format(
                "%s/workers/lora/train?channel_id=%s&trigger_word=%s&steps=%d&is_style=%b",
                fastApiUrl, channelId, triggerWord, steps, isStyle
        );
        String boundary = UUID.randomUUID().toString().replace("-", "");
        URL url = new URL(urlStr);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(10_000);
        conn.setReadTimeout(300_000); // 5분 (파일 업로드 + 큐 등록)
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);

        try (OutputStream os = conn.getOutputStream()) {
            String partHeader = "--" + boundary + "\r\n"
                    + "Content-Disposition: form-data; name=\"zip_file\"; filename=\"reference_images.zip\"\r\n"
                    + "Content-Type: application/zip\r\n\r\n";
            os.write(partHeader.getBytes(StandardCharsets.UTF_8));
            os.write(zipBytes);
            os.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
            os.flush();
        }
        int code = conn.getResponseCode();
        InputStream is = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String body = new String(is.readAllBytes(), StandardCharsets.UTF_8);
        if (code < 200 || code >= 300) {
            throw new RuntimeException("LoRA 학습 시작 실패 (" + code + "): " + body);
        }
        return objectMapper.readValue(body, Map.class);
    }

    /**
     * [Sprint 3] LoRA 학습 진행 상태 조회.
     * 학습 완료 시 lora_model_url(safetensors URL) 포함.
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> getLoraStatus(String requestId) {
        try {
            String urlStr = fastApiUrl + "/workers/lora/status/" + requestId;
            URL url = new URL(urlStr);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(10_000);
            conn.setReadTimeout(30_000);
            int code = conn.getResponseCode();
            InputStream is = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
            String body = new String(is.readAllBytes(), StandardCharsets.UTF_8);
            if (code < 200 || code >= 300) {
                throw new RuntimeException("LoRA 상태 조회 실패 (" + code + "): " + body);
            }
            return objectMapper.readValue(body, Map.class);
        } catch (Exception e) {
            throw new RuntimeException("LoRA 상태 조회 오류: " + e.getMessage(), e);
        }
    }

    public void deleteJob(Long jobId) {
        try {
            restTemplate.delete(fastApiUrl + "/workers/jobs/" + jobId);
            log.info("FastAPI 작업 리소스 삭제 통지 성공: jobId={}", jobId);
        } catch (Exception e) {
            log.error("FastAPI 작업 리소스 삭제 통지 실패: jobId={}, error={}", jobId, e.getMessage());
        }
    }

    // ============================
    // 공통 POST helper — UTF-8 charset 명시
    // ============================
    @SuppressWarnings("unchecked")
    private Map<String, Object> readMap(String responseBody) throws IOException {
        return objectMapper.readValue(responseBody, Map.class);
    }

    private String postJson(String urlStr, Map<String, Object> bodyMap) throws IOException {
        String jsonBody = objectMapper.writeValueAsString(bodyMap);
        log.info("FastAPI POST: {} bodyLen={}", urlStr, jsonBody.length());

        URL url = new URL(urlStr);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(10_000);
        conn.setReadTimeout(1_800_000); // 30분
        // UTF-8 charset 명시 — 한글 깨짐 방지 핵심
        // Direct Pro image rendering is sequential and can exceed 30 minutes.
        // The Temporal activity allows two hours, so keep this client timeout aligned.
        if (urlStr.contains("/workers/images/generate")
                || urlStr.contains("/workers/longform/generate")) {
            conn.setReadTimeout(21_600_000);
        }
        conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
        conn.setRequestProperty("Accept", "application/json");

        // UTF-8 바이트로 명시적 인코딩
        byte[] bodyBytes = jsonBody.getBytes(StandardCharsets.UTF_8);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(bodyBytes);
            os.flush();
        }
        int code = conn.getResponseCode();
        InputStream is = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String responseBody = new String(is.readAllBytes(), StandardCharsets.UTF_8);
        if (code < 200 || code >= 300) throw new FastApiHttpException(code, responseBody);
        log.info("FastAPI 응답: code={}, bodyLen={}", code, responseBody.length());
        if (code < 200 || code >= 300) throw new RuntimeException("FastAPI 실패: " + code + " " + responseBody);
        return responseBody;
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> getElevenLabsVoices() {
        try {
            return restTemplate.getForObject(fastApiUrl + "/workers/tts/voices", List.class);
        } catch (Exception e) {
            log.error("ElevenLabs 목소리 목록 조회 실패: {}", e.getMessage());
            return List.of();
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getProviderStatus() {
        try {
            return restTemplate.getForObject(fastApiUrl + "/providers/status", Map.class);
        } catch (Exception e) {
            log.warn("Provider status 조회 실패: {}", e.getMessage());
            return Map.of("youtube", Map.of("configured", false), "elevenlabs", Map.of("configured", false));
        }
    }

    public byte[] previewTts(String voiceId, String text) {
        try {
            Map<String, Object> body = new HashMap<>();
            body.put("voice_id", voiceId);
            body.put("text", text);
            // Keep this request on the explicit UTF-8 transport used by the
            // other FastAPI calls. RestTemplate's byte[] converter can omit
            // the JSON body here, which FastAPI reports as HTTP 422.
            byte[] requestBytes = objectMapper.writeValueAsString(body).getBytes(StandardCharsets.UTF_8);
            URL url = new URL(fastApiUrl + "/workers/tts/preview");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setConnectTimeout(10_000);
            conn.setReadTimeout(60_000);
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            conn.setRequestProperty("Accept", "audio/mpeg");
            conn.setFixedLengthStreamingMode(requestBytes.length);
            try (OutputStream output = conn.getOutputStream()) {
                output.write(requestBytes);
            }
            int code = conn.getResponseCode();
            InputStream stream = code >= 200 && code < 300
                    ? conn.getInputStream() : conn.getErrorStream();
            byte[] responseBytes = stream != null ? stream.readAllBytes() : new byte[0];
            if (code < 200 || code >= 300) {
                throw new RuntimeException("FastAPI TTS preview failed: " + code + " "
                        + new String(responseBytes, StandardCharsets.UTF_8));
            }
            return responseBytes;
        } catch (Exception e) {
            throw new RuntimeException("TTS 미리듣기 생성 오류: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getCharacterLibraryStatus(String channelId) {
        try {
            return restTemplate.getForObject(
                    fastApiUrl + "/workers/character-library/" + channelId, Map.class);
        } catch (Exception e) {
            throw new RuntimeException("캐릭터 라이브러리 상태 조회 실패: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> generateCharacterLibrary(
            String channelId, String characterDescription, boolean regenerate) {
        return generateCharacterLibrary(channelId, characterDescription, regenerate, false);
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getWorkerCostLedger(Long jobId) {
        try {
            Map<String, Object> response = restTemplate.getForObject(
                    fastApiUrl + "/workers/jobs/" + jobId + "/cost-ledger", Map.class);
            return response == null ? Map.of() : response;
        } catch (Exception e) {
            log.warn("FastAPI 비용 원장 조회 실패: jobId={}, error={}", jobId, e.getMessage());
            return Map.of();
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> generateCharacterLibrary(
            String channelId, String characterDescription, boolean regenerate, boolean includeRoleCostumes) {
        try {
            Map<String, Object> body = new HashMap<>();
            body.put("channel_id", channelId);
            body.put("character_description", characterDescription);
            body.put("regenerate", regenerate);
            body.put("include_role_costumes", includeRoleCostumes);
            return objectMapper.readValue(
                    postJson(fastApiUrl + "/workers/character-library/generate", body), Map.class);
        } catch (Exception e) {
            throw new RuntimeException("캐릭터 포즈 생성 실패: " + e.getMessage(), e);
        }
    }

    public ResponseEntity<byte[]> getCharacterPose(String channelId, String pose) {
        try {
            return restTemplate.exchange(
                    fastApiUrl + "/workers/character-library/" + channelId + "/pose/" + pose,
                    HttpMethod.GET,
                    null,
                    byte[].class);
        } catch (Exception e) {
            throw new RuntimeException("캐릭터 포즈 조회 실패: " + e.getMessage(), e);
        }
    }
}
