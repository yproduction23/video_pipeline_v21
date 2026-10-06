package com.pipeline.video.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.*;
import com.pipeline.video.dto.TtsGenerateResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import com.pipeline.video.repository.ChannelProfileRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/**
 * Phase 3-3 — TTS 음성 합성 서비스
 *
 *  - generate(): 최종 스크립트 → 청크 분할 → mp3 + 청크별 타이밍 정보 반환
 *  - confirm(): TTS 게이트 통과 → IMAGES_PENDING
 *
 *  핵심: chunks 정보가 Phase 3-4(이미지 매칭), 3-5(자막 동기화)에 활용됨
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class TtsService {

    private final VideoJobRepository jobRepository;
    private final AssetRepository assetRepository;
    private final ChannelProfileRepository channelProfileRepository;
    private final FastApiClient fastApiClient;
    private final GateService gateService;
    private final AutonomyService autonomyService;
    private final CostService costService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Transactional
    public TtsGenerateResponse generate(Long jobId, String voiceId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING || job.getStatus() == JobStatus.SCRIPT_PENDING) {
            throw new IllegalStateException("스크립트 확정 전에는 TTS를 생성할 수 없습니다. 현재: " + job.getStatus());
        }

        // 최종 스크립트 조회 (가장 최근 SCRIPT Asset)
        String script = loadFinalScript(jobId);
        if (script == null || script.isBlank()) {
            throw new IllegalStateException("최종 스크립트가 없습니다. 스크립트 확정을 먼저 진행하세요.");
        }

        // 채널 프로필 로드 (커스텀 ElevenLabs 목소리가 설정되어 있는지 확인)
        String finalVoiceId = voiceId;
        Double ttsSpeed = null;
        if (job.getChannelId() != null) {
            ChannelProfile profile = channelProfileRepository.findById(job.getChannelId()).orElse(null);
            if (profile != null) {
                if (profile.getVoiceId() != null && !profile.getVoiceId().isBlank()) {
                    finalVoiceId = profile.getVoiceId();
                    log.info("채널 목소리 로드 완료: channelId={}, voiceId={}", job.getChannelId(), finalVoiceId);
                }
                if (profile.getTtsSpeedOverride() != null) {
                    ttsSpeed = profile.getTtsSpeedOverride().doubleValue();
                    log.info("채널 속도 오버라이드 로드 완료: channelId={}, speed={}", job.getChannelId(), ttsSpeed);
                }
            }
        }
        // 작업별 선택은 채널 기본값보다 우선합니다.
        if (job.getTtsVoiceId() != null && !job.getTtsVoiceId().isBlank()) {
            finalVoiceId = job.getTtsVoiceId();
            log.info("작업별 GUIDED 목소리 로드: jobId={}, voiceId={}", jobId, finalVoiceId);
        }

        log.info("TTS 생성 시작: jobId={}, scriptLength={}자, voice={}, speed={}, autonomy={}",
                jobId, script.length(), finalVoiceId, ttsSpeed, job.getAutonomy());

        // FastAPI 호출
        TtsGenerateResponse result;
        try {
            result = fastApiClient.generateTts(
                    jobId, script, finalVoiceId, ttsSpeed, job.getLongformTargetMinutes(), job.getAutonomy().name());
        } catch (RuntimeException e) {
            if (e.getMessage() != null && e.getMessage().contains("TTS duration is outside the allowed")) {
                // 같은 승인 대본으로 TTS를 재시도해도 실측 발화 속도가 바뀌지 않아
                // 같은 초과 오류가 반복된다. 대본 단계로 되돌려 재생성 UI를 다시 연다.
                job.setStatus(JobStatus.SCRIPT_PENDING);
                jobRepository.save(job);
                log.warn("TTS 분량 초과로 jobId={}를 SCRIPT_PENDING으로 되돌립니다: {}", jobId, e.getMessage());
                throw new IllegalStateException(
                        "TTS 음성 길이가 목표 분량을 벗어나 대본을 다시 생성해야 합니다. " +
                        "스크립트 단계로 되돌렸으니 대본을 다시 생성해주세요.", e);
            }
            throw e;
        }

        // [버그 수정] 기존에는 BigDecimal.ZERO 하드코딩. 실제 ElevenLabs API를
        // 호출한 경우에만 요금이 실제로 발생하므로, used_elevenlabs=true 일 때만
        // 문자 수 기반 요금을 기록합니다. gTTS 폴백 시엔 무료이므로 $0.
        boolean usedPaidTts = Boolean.TRUE.equals(result.getUsedElevenlabs());
        java.math.BigDecimal ttsCost = usedPaidTts
                ? CostEstimator.elevenLabs(script.length())
                : java.math.BigDecimal.ZERO;
        costService.record(jobId, usedPaidTts ? "ELEVENLABS_TTS" : "GTTS_FREE", ttsCost, "USD",
                String.format("TTS 합성: %d자, %.1f초 (%s)",
                        script.length(), result.getTotalDuration(),
                        usedPaidTts ? "ElevenLabs" : "gTTS 무료 폴백"));

        // Asset 저장
        Asset asset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.TTS_AUDIO)
                .localPath(result.getAudioPath())
                .metaJson(safeJson(result))
                .build();
        assetRepository.save(asset);

        // AUTO 모드: 자동 confirm → IMAGES_PENDING
        if (autonomyService.isAuto(job)) {
            log.info("AUTO 모드 — TTS 자동 확정");
            confirm(jobId, "AUTO");
        }

        return result;
    }

    @Transactional
    public void selectVoice(Long jobId, String voiceId) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        if (job.getStatus() != JobStatus.TTS_PENDING) {
            throw new IllegalStateException("목소리 선택은 TTS_PENDING 상태에서만 가능합니다. 현재: " + job.getStatus());
        }
        if (voiceId == null || voiceId.isBlank() || "default_ko".equals(voiceId)) {
            throw new IllegalArgumentException("ElevenLabs 목소리를 선택하세요.");
        }
        job.setTtsVoiceId(voiceId.trim());
        jobRepository.save(job);
        log.info("GUIDED 목소리 선택 저장: jobId={}, voiceId={}", jobId, voiceId);
    }

    @Transactional
    public void confirm(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING || job.getStatus() == JobStatus.SCRIPT_PENDING) {
            throw new IllegalStateException("스크립트 확정 전에는 TTS를 확정할 수 없습니다. 현재: " + job.getStatus());
        }

        if (job.getStatus() == JobStatus.TTS_PENDING) {
            // GUIDED 게이트 승인이 실제 생성(generate())을 거치지 않고도 눌릴 수
            // 있어(목소리 선택 → 바로 승인), TTS_AUDIO Asset 없이 IMAGES_PENDING으로
            // 넘어가 이미지 단계가 "TTS_AUDIO Asset이 없습니다"로 실패하는 문제가 있었다.
            assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.TTS_AUDIO)
                    .orElseThrow(() -> new IllegalStateException("TTS 음성을 먼저 생성해주세요. TTS_AUDIO 자산이 없습니다."));
            gateService.approve(jobId, GateName.TTS, username, "TTS 확정");
        } else {
            log.info("TTS 수정/재확정 완료 (상태 유지: {}): jobId={}", job.getStatus(), jobId);
        }
        log.info("TTS 확정 완료: jobId={}", jobId);
    }

    /**
     * 2026-10-06 사용자 요청: 잘못 생성된 음성(예: 대본 분할 버그로 문장이
     * 중복 낭독된 TTS)을 그대로 둔 채 다음 단계로 넘어갈 수 없게, 생성된
     * TTS_AUDIO 자산을 지운다. 이미 TTS_PENDING을 지난 상태였다면 되돌려
     * 다음 단계(이미지)가 더 이상 유효하지 않은 음성을 참조하지 않게 한다.
     */
    @Transactional
    public void deleteAudio(Long jobId) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.TTS_AUDIO);
        if (job.getStatus() != JobStatus.DRAFT && job.getStatus() != JobStatus.KEYWORD_PENDING
                && job.getStatus() != JobStatus.SCRIPT_PENDING) {
            job.setStatus(JobStatus.TTS_PENDING);
            jobRepository.save(job);
        }
        log.info("TTS 음성 삭제 완료: jobId={}", jobId);
    }

    // ============================
    // helpers
    // ============================
    @SuppressWarnings("unchecked")
    private String loadFinalScript(Long jobId) {
        Asset asset = assetRepository
                .findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT)
                .orElseThrow(() -> new RuntimeException("스크립트 Asset이 없습니다: " + jobId));

        try {
            Map<String, Object> meta = objectMapper.readValue(asset.getMetaJson(), Map.class);
            return narrationFromMeta(meta);
        } catch (Exception e) {
            log.error("스크립트 파싱 실패: {}", e.getMessage());
            return null;
        }
    }

    /**
     * Build the provider copy from narration fields only.  The final SCRIPT
     * asset intentionally contains an editor-friendly Markdown script with
     * repeated scene headings; sending that value to ElevenLabs both speaks
     * the headings and nearly doubles the requested duration.
     */
    static String narrationFromMeta(Map<String, Object> meta) {
        Object rawSections = meta.get("sections");
        if (rawSections instanceof List<?> sections && !sections.isEmpty()) {
            StringBuilder narration = new StringBuilder();
            for (Object rawSection : sections) {
                if (!(rawSection instanceof Map<?, ?> section)) continue;
                // text_for_tts는 최종 승인된 발화 원문이다. content/text는
                // 편집용 문구 또는 정규화 이전 초안일 수 있으므로 후순위로 둔다.
                Object value = section.get("text_for_tts") != null
                        ? section.get("text_for_tts")
                        : (section.get("content") != null
                            ? section.get("content") : section.get("text"));
                if (value == null || value.toString().isBlank()) continue;
                if (!narration.isEmpty()) narration.append("\n\n");
                narration.append(value.toString().trim());
            }
            if (!narration.isEmpty()) return narration.toString();
        }
        Object scriptVal = meta.get("script");
        return scriptVal == null ? null : scriptVal.toString();
    }

    private String safeJson(Object obj) {
        try {
            return objectMapper.writeValueAsString(obj);
        } catch (JsonProcessingException e) {
            return "{}";
        }
    }
}
