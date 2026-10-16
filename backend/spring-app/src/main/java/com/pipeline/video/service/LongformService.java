package com.pipeline.video.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.*;
import com.pipeline.video.dto.LongformGenerateResponse;
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
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.ArrayList;
import java.util.Comparator;
import com.pipeline.video.dto.SceneImageDto;
import com.pipeline.video.dto.TtsGenerateResponse;

/**
 * Phase 3-5A — 롱폼 영상 조립 서비스
 *
 * 흐름:
 *   ASSEMBLING 진입 (IMAGES 게이트 통과 후)
 *     ↓
 *   generate(): TTS 음성 + 씬 이미지 + GIF → FFmpeg 조립 → MP4
 *     ↓
 *   상태 → PREVIEW_PENDING
 *     ↓
 *   confirm(): PREVIEW 게이트 통과 → SHORTS_SEGMENTS_PENDING (쇼츠 모드)
 *              또는 READY (쇼츠 미생성)
 */
@Service
@Slf4j
public class LongformService {

    private final VideoJobRepository jobRepository;
    private final AssetRepository assetRepository;
    private final ChannelProfileRepository channelProfileRepository;
    private final FastApiClient fastApiClient;
    private final GateService gateService;
    private final AutonomyService autonomyService;
    private final CostService costService;
    private final JobService jobService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public LongformService(
            VideoJobRepository jobRepository,
            AssetRepository assetRepository,
            ChannelProfileRepository channelProfileRepository,
            FastApiClient fastApiClient,
            GateService gateService,
            AutonomyService autonomyService,
            CostService costService,
            @org.springframework.context.annotation.Lazy JobService jobService) {
        this.jobRepository = jobRepository;
        this.assetRepository = assetRepository;
        this.channelProfileRepository = channelProfileRepository;
        this.fastApiClient = fastApiClient;
        this.gateService = gateService;
        this.autonomyService = autonomyService;
        this.costService = costService;
        this.jobService = jobService;
    }

    static Map<String, Object> buildReassembledScriptMetadata(
            String script,
            List<Map<String, Object>> sections,
            Map<String, Object> existingScriptMeta) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("script", script);
        metadata.put("final", true);
        metadata.put("char_count", script.length());
        metadata.put("sections", sections);
        ScriptService.copyNonEmptyAuditFields(existingScriptMeta, metadata);
        metadata.putIfAbsent("verified_facts", List.of());
        return metadata;
    }

    /**
     * TtsService.generate()와 동일한 규칙으로 채널 목소리를 조회합니다.
     * (기존에는 이 로직이 TtsService에만 있고 LongformService.rebuild()에는 없어서,
     * 씬을 편집하고 재조립할 때마다 채널에서 지정한 목소리가 아니라 기본 목소리로
     * 되돌아가는 버그가 있었습니다. 초기 생성 때는 채널 목소리로 나오다가,
     * 씬 하나 고치고 재조립하면 목소리가 바뀌어 버리는 것처럼 느껴졌을 겁니다.)
     */
    private String resolveVoiceId(VideoJob job) {
        String defaultVoiceId = "default_ko";
        if (job.getChannelId() == null) return defaultVoiceId;
        ChannelProfile profile = channelProfileRepository.findById(job.getChannelId()).orElse(null);
        if (profile != null && profile.getVoiceId() != null && !profile.getVoiceId().isBlank()) {
            log.info("재조립 시 채널 목소리 로드: channelId={}, voiceId={}", job.getChannelId(), profile.getVoiceId());
            return profile.getVoiceId();
        }
        return defaultVoiceId;
    }

    @Transactional
    public LongformGenerateResponse generate(Long jobId, String username) {
        return generate(jobId, username, true);
    }

    @Transactional
    public LongformGenerateResponse generate(Long jobId, String username, boolean extractShortsScenario) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING || job.getStatus() == JobStatus.SCRIPT_PENDING || job.getStatus() == JobStatus.TTS_PENDING || job.getStatus() == JobStatus.IMAGES_PENDING) {
            throw new IllegalStateException("이미지 확정 전에는 롱폼을 조립할 수 없습니다. 현재: " + job.getStatus());
        }

        // TTS Asset 로드 (audio_path + chunks)
        String ttsMetaJson = loadAssetMeta(jobId, AssetType.TTS_AUDIO);
        // SCENE_IMAGE Asset 목록 로드 — index 오름차순 정렬 필수
        // (씬 편집/분할 후 DB 삽입 순서와 index 순서가 달라질 수 있음)
        List<Asset> sceneAssets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);
        sceneAssets.sort(Comparator.comparingInt(a -> {
            try {
                SceneImageDto dto = objectMapper.readValue(a.getMetaJson(), SceneImageDto.class);
                return dto.getIndex() != null ? dto.getIndex() : Integer.MAX_VALUE;
            } catch (Exception e) { return Integer.MAX_VALUE; }
        }));
        // GIF_CLIP Asset 목록 로드
        List<Asset> gifAssets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.GIF_CLIP);

        // 2026-10-13 job 14 씬5 재현: SCENE_IMAGE 자산의 text가 비어 있으면
        // (예: 수동 승인 경로가 narration을 빠뜨린 경우) 조립 직전
        // "대본 원문과 장면 내레이션 불일치"로 전체 조립이 막힌다. rebuild()에만
        // 있던 SCRIPT 자산 기반 복구를 최초 조립 경로에도 동일하게 적용한다.
        recoverMissingSceneTextFromScript(jobId, sceneAssets);

        log.info("롱폼 조립 시작: jobId={}, scenes={}, gifs={}, autonomy={}",
                jobId, sceneAssets.size(), gifAssets.size(), job.getAutonomy());

        // scenes와 gifs의 metaJson 목록 전송 (정렬된 순서 그대로)
        String scenesJson = safeJson(sceneAssets.stream()
                .map(Asset::getMetaJson).toList());
        String gifsJson = safeJson(gifAssets.stream()
                .map(Asset::getMetaJson).toList());

        // BGM 생성 (FastAPI 호출 - 실패해도 메인 파이프라인 진행)
        try {
            com.fasterxml.jackson.databind.JsonNode ttsNode = objectMapper.readTree(ttsMetaJson);
            int durationSeconds = ttsNode.path("total_duration").asInt(60);
            String category = job.getCategory() != null ? job.getCategory().name() : "CUSTOM";
            fastApiClient.generateBgm(jobId, category, durationSeconds);
        } catch (Exception e) {
            log.error("BGM 생성 준비 오류: {}", e.getMessage());
        }

        // FastAPI 호출
        LongformGenerateResponse result = fastApiClient.generateLongform(
                jobId, ttsMetaJson, scenesJson, gifsJson);

        // 인트로 Kling 움짤 비용 (Fal.ai 유료)
        double introSeconds = 0.0;
        if (result.getQualityReport() != null && result.getQualityReport().containsKey("kling_clip_count")) {
            try {
                Object clipsVal = result.getQualityReport().get("kling_clip_count");
                if (clipsVal instanceof Number) {
                    introSeconds = ((Number) clipsVal).intValue() * 5.0;
                }
            } catch (Exception ex) {
                log.warn("Failed to parse kling_clip_count from qualityReport: {}", ex.getMessage());
            }
        }
        // Fallback to estimation based on duration if kling_clip_count not found
        if (introSeconds <= 0.0) {
            double totalMin = result.getDurationSeconds() / 60.0;
            if (totalMin <= 5) introSeconds = 30;
            else if (totalMin <= 10) introSeconds = 45;
            else introSeconds = 60;
        }

        // 조립 자체는 무료 (FFmpeg)
        costService.record(jobId, "FFMPEG_ASSEMBLE", java.math.BigDecimal.ZERO, "USD",
                String.format("롱폼 조립: %.0f초, %d씬",
                        result.getDurationSeconds(), result.getSceneCount()));

        // Asset 저장
        Asset asset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.LONGFORM_VIDEO)
                .localPath(result.getVideoPath())
                .metaJson(safeJson(result))
                .build();
        assetRepository.save(asset);
        if (result.getAssemblyManifestPath() != null && !result.getAssemblyManifestPath().isBlank()) {
            Asset manifest = Asset.builder()
                    .jobId(jobId)
                    .assetType(AssetType.ASSEMBLY_MANIFEST)
                    .localPath(result.getAssemblyManifestPath())
                    .metaJson("{\"video_path\":\"" + result.getVideoPath().replace("\"", "\\\"") + "\"}")
                    .build();
            assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.ASSEMBLY_MANIFEST)
                    .ifPresent(assetRepository::delete);
            assetRepository.save(manifest);
        }
        saveQcReport(jobId, result);

        // outputPath 업데이트
        job.setOutputPath(result.getVideoPath());
        // ASSEMBLING → PREVIEW_PENDING
        job.setStatus(JobStatus.PREVIEW_PENDING);
        jobRepository.save(job);
        PhonePushNotifier.stageCompleted(jobId, job.getTitle(), "영상 조립 완료", "미리보기 확인 후 승인");

        // [체크리스트 피드백 반영] 유튜브 패키지(썸네일, 제목, 더보기글, 태그) 자동 생성 추가
        try {
            log.info("유튜브 패키지(썸네일, 제목, 더보기글) 자동 생성 시작: jobId={}", jobId);
            log.info("미리보기 승인 전 썸네일 자동 생성을 건너뜁니다: jobId={}", jobId);
        } catch (Exception e) {
            log.error("유튜브 패키지 자동 생성 실패: {}", e.getMessage());
        }

        // 영상 조립까지만 승인된 실행에서는 쇼츠용 LLM 호출을 시작하지 않는다.
        if (extractShortsScenario) {
            try {
                log.info("자동 쇼츠 시나리오 추출 시작: jobId={}", jobId);
            
                // 최신 업데이트된 sceneAsset을 가져옵니다.
                List<Asset> latestSceneAssets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);
                List<Map<String, Object>> parsedScenes = latestSceneAssets.stream()
                    .map(a -> {
                        try {
                            return (Map<String, Object>) objectMapper.readValue(a.getMetaJson(), Map.class);
                        } catch(Exception e) {
                            return null;
                        }
                    }).filter(java.util.Objects::nonNull).collect(java.util.stream.Collectors.toList());

                if (!parsedScenes.isEmpty()) {
                    Object scenarios = fastApiClient.extractShortsScenarios(jobId, parsedScenes);
                
                    Asset scenarioAsset = Asset.builder()
                            .jobId(jobId)
                            .assetType(AssetType.SHORTS_SCENARIO)
                            .metaJson(objectMapper.writeValueAsString(scenarios))
                            .build();
                    assetRepository.save(scenarioAsset);
                    log.info("자동 쇼츠 시나리오 추출 완료 및 저장 성공: jobId={}", jobId);
                }
            } catch(Exception e) {
                log.error("쇼츠 시나리오 자동 추출 실패: {}", e.getMessage());
            }
        } else {
            log.info("영상 조립 전용 실행 — 쇼츠 시나리오 추출 건너뜀: jobId={}", jobId);
        }

        // AUTO 모드: 자동 confirm
        if (autonomyService.isAuto(job)) {
            log.info("AUTO 모드 — 롱폼 미리보기 자동 확정");
            confirm(jobId, "AUTO");
        }

        return result;
    }

    @Transactional
    public void confirm(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING || job.getStatus() == JobStatus.SCRIPT_PENDING || job.getStatus() == JobStatus.TTS_PENDING || job.getStatus() == JobStatus.IMAGES_PENDING || job.getStatus() == JobStatus.ASSEMBLING) {
            throw new IllegalStateException("롱폼 조립 완료 전에는 확정할 수 없습니다. 현재: " + job.getStatus());
        }

        if (job.getStatus() == JobStatus.PREVIEW_PENDING) {
            gateService.approve(jobId, GateName.PREVIEW, username, "롱폼 미리보기 확정");
        } else {
            log.info("롱폼 수정/재확정 완료 (상태 유지: {}): jobId={}", job.getStatus(), jobId);
        }

        // 게이트 통과 후: makeShorts=true → SHORTS_SEGMENTS_PENDING
        //                 makeShorts=false → READY (PREVIEW 다음이 SHORTS_SEGMENTS_PENDING)
        // GateService.NEXT_STATUS_ON_APPROVE에 의해 SHORTS_SEGMENTS_PENDING으로 전이됨
        // makeShorts가 false이면 쇼츠 단계를 건너뛰고 READY로 직접 변경
        if (!job.isMakeShorts()) {
            job.setStatus(JobStatus.READY);
            jobRepository.save(job);
            log.info("쇼츠 미생성 → READY: jobId={}", jobId);
        } else {
            log.info("쇼츠 생성 예정 → SHORTS_SEGMENTS_PENDING: jobId={}", jobId);
        }
    }

    // ============================
    // helpers
    // ============================
    private String loadAssetMeta(Long jobId, AssetType type) {
        return assetRepository
                .findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, type)
                .map(Asset::getMetaJson)
                .orElseThrow(() -> new RuntimeException(type + " Asset이 없습니다: " + jobId));
    }

    private String safeJson(Object obj) {
        try {
            return objectMapper.writeValueAsString(obj);
        } catch (JsonProcessingException e) {
            return "[]";
        }
    }

    /**
     * 2026-10-13 job 14 씬5 재현: SCENE_IMAGE 자산의 text가 비어 있으면
     * longform 조립 직전 "대본 원문과 장면 내레이션 불일치"로 전체 조립이
     * 막힌다. rebuild()에 이미 있던 SCRIPT 자산 기반 복구를 generate()와
     * 공유해, 한쪽만 고쳤을 때 다른 쪽이 조용히 다시 어긋나지 않게 한다.
     */
    @SuppressWarnings("unchecked")
    private void recoverMissingSceneTextFromScript(Long jobId, List<Asset> sceneAssets) {
        try {
            java.util.Optional<Asset> scriptAssetOpt =
                    assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT);
            if (scriptAssetOpt.isEmpty()) return;
            Map<String, Object> scriptMeta = objectMapper.readValue(scriptAssetOpt.get().getMetaJson(), Map.class);
            List<Map<String, Object>> sections = (List<Map<String, Object>>) scriptMeta.get("sections");
            if (sections == null) return;
            for (int i = 0; i < sceneAssets.size(); i++) {
                Asset asset = sceneAssets.get(i);
                SceneImageDto dto = objectMapper.readValue(asset.getMetaJson(), SceneImageDto.class);
                if ((dto.getText() == null || dto.getText().isBlank()) && i < sections.size()) {
                    Map<String, Object> section = sections.get(i);
                    String content = (String) section.get("text_for_tts");
                    if (content == null || content.isBlank()) content = (String) section.get("content");
                    if (content == null || content.isBlank()) content = (String) section.get("text");
                    if (content != null && !content.isBlank()) {
                        dto.setText(content);
                        asset.setMetaJson(safeJson(dto));
                        assetRepository.save(asset);
                        log.info("씬 {} 내레이션 원문을 SCRIPT 자산에서 복구: jobId={}", dto.getIndex(), jobId);
                    }
                }
            }
        } catch (Exception e) {
            log.warn("SCRIPT 에셋으로부터 null 텍스트 복구 실패: jobId={}, err={}", jobId, e.getMessage());
        }
    }

    @Transactional
    public LongformGenerateResponse rebuild(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        // 1. 모든 SCENE_IMAGE 에셋 조회 및 index 순서대로 정렬
        List<Asset> sceneAssets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);
        if (sceneAssets.isEmpty()) {
            throw new IllegalStateException("재조립할 씬 이미지 에셋이 존재하지 않습니다.");
        }

        List<SceneImageDto> scenes = new ArrayList<>();
        for (Asset a : sceneAssets) {
            try {
                SceneImageDto dto = objectMapper.readValue(a.getMetaJson(), SceneImageDto.class);
                scenes.add(dto);
            } catch (Exception e) {
                log.warn("씬 이미지 에셋 파싱 실패: {}", e.getMessage());
            }
        }
        scenes.sort(Comparator.comparing(SceneImageDto::getIndex));

        // [사전 검증] 씬 인덱스가 0부터 연속적인지 확인 (FastAPI도 같은 검증을 함)
        // DB에서 인덱스 충돌/구멍이 있으면 여기서 명확한 메시지로 중단시킴
        java.util.List<Integer> actualIndices = scenes.stream()
                .map(SceneImageDto::getIndex)
                .collect(java.util.stream.Collectors.toList());
        java.util.Set<Integer> expectedIndices = new java.util.HashSet<>();
        for (int i = 0; i < scenes.size(); i++) expectedIndices.add(i);
        java.util.Set<Integer> actualSet = new java.util.HashSet<>(actualIndices);
        if (!actualSet.equals(expectedIndices) || actualIndices.size() != actualSet.size()) {
            throw new IllegalStateException(
                String.format("씬 인덱스가 연속적이지 않습니다 (중복 또는 구멍 존재). " +
                    "expected=0..%d, actual=%s. splitScene() 버그로 인한 인덱스 오염 가능성 있음.",
                    scenes.size() - 1, actualIndices.subList(0, Math.min(20, actualIndices.size())))
            );
        }

        // [하위 호환성 복구] 만약 기존 씬의 text가 null인 경우 SCRIPT 에셋의 sections를 기반으로 복구
        try {
            java.util.Optional<Asset> scriptAssetOpt = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT);
            if (scriptAssetOpt.isPresent()) {
                Asset scriptAsset = scriptAssetOpt.get();
                Map<String, Object> scriptMeta = objectMapper.readValue(scriptAsset.getMetaJson(), Map.class);
                List<Map<String, Object>> sections = (List<Map<String, Object>>) scriptMeta.get("sections");
                if (sections != null) {
                    for (int i = 0; i < scenes.size(); i++) {
                        SceneImageDto scene = scenes.get(i);
                        if (scene.getText() == null || scene.getText().isBlank()) {
                            if (i < sections.size()) {
                                Map<String, Object> sec = sections.get(i);
                                String content = (String) sec.get("text");
                                if (content == null) {
                                    content = (String) sec.get("content");
                                }
                                scene.setText(content);
                                
                                // DB에도 업데이트
                                for (Asset a : sceneAssets) {
                                    try {
                                        SceneImageDto dto = objectMapper.readValue(a.getMetaJson(), SceneImageDto.class);
                                        if (dto.getIndex().equals(scene.getIndex())) {
                                            dto.setText(content);
                                            a.setMetaJson(safeJson(dto));
                                            assetRepository.save(a);
                                            break;
                                        }
                                    } catch (Exception ignore) {}
                                }
                            }
                        }
                    }
                }
            }
        } catch (Exception e) {
            log.warn("SCRIPT 에셋으로부터 null 텍스트 복구 실패: {}", e.getMessage());
        }

        // 2. 각 씬의 text(한국어 대사)를 순서대로 이어붙여 새로운 전체 스크립트 작성
        StringBuilder fullScriptBuilder = new StringBuilder();
        for (SceneImageDto scene : scenes) {
            if (fullScriptBuilder.length() > 0) {
                fullScriptBuilder.append("\n");
            }
            String pText = scene.getText() != null ? scene.getText() : (scene.getPrompt() != null ? scene.getPrompt() : "");
            fullScriptBuilder.append(pText);
        }
        String newScript = fullScriptBuilder.toString();

        // 3. SCRIPT 에셋 업데이트 (마지막 스크립트 상태 갱신)
        List<Map<String, Object>> newSections = new ArrayList<>();
        for (SceneImageDto scene : scenes) {
            Map<String, Object> sec = new HashMap<>();
            sec.put("title", "Scene " + scene.getIndex());
            String pText = scene.getText() != null ? scene.getText() : (scene.getPrompt() != null ? scene.getPrompt() : "");
            sec.put("text", pText);
            sec.put("content", pText);
            sec.put("char_count", pText.length());
            sec.put("section", scene.getSection() != null ? scene.getSection() : "scene_" + scene.getIndex());
            newSections.add(sec);
        }
        Map<String, Object> existingScriptMeta = new LinkedHashMap<>();
        try {
            java.util.Optional<Asset> existingScriptAsset = assetRepository
                    .findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT);
            if (existingScriptAsset.isPresent()) {
                existingScriptMeta = objectMapper.readValue(existingScriptAsset.get().getMetaJson(), Map.class);
            }
        } catch (Exception e) {
            log.warn("재조립 전 SCRIPT 감사 계보 복원 실패: {}", e.getMessage());
        }

        Asset finalScriptAsset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.SCRIPT)
                .metaJson(safeJson(buildReassembledScriptMetadata(
                        newScript, newSections, existingScriptMeta)))
                .build();
        assetRepository.save(finalScriptAsset);

        // 4. TTS 재생성 — 채널 프로필의 목소리를 사용해서 초기 생성과 톤 일관성 유지
        String finalVoiceId = resolveVoiceId(job);
        log.info("재조립을 위한 TTS 재생성 시작: jobId={}, scriptLength={}자, voice={}",
                jobId, newScript.length(), finalVoiceId);
        TtsGenerateResponse ttsResult = fastApiClient.generateTts(jobId, newScript, finalVoiceId);

        // TTS Asset 저장
        Asset ttsAsset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.TTS_AUDIO)
                .localPath(ttsResult.getAudioPath())
                .metaJson(safeJson(ttsResult))
                .build();
        assetRepository.save(ttsAsset);

        // 5. TTS-씬 타이밍 매칭은 FastAPI의 _assign_scene_durations_from_chunks()에 위임.
        //
        // [이전 문제] Spring에서 TTS 청크 텍스트가 '다음 씬 텍스트에 포함'되는지
        // contains()로 검사하던 방식은 짧은 단어 청크가 여러 씬에 걸쳐 나타날 때
        // 잘못된 씬에 배정되어 특정 씬의 duration=0 또는 15.0 폴백이 발생했음.
        //
        // [수정] FastAPI longform_worker의 _assign_scene_durations_from_chunks()는
        // 문자 수 비례 기반의 안정적인 알고리즘을 사용하므로 여기서 중복 계산하지 않음.
        // sceneAssets DB에는 기존 start/duration을 유지하고, FastAPI 조립 시 TTS meta와
        // 함께 전달되면 longform_worker가 최종 타이밍을 결정함.
        log.info("TTS 타이밍 매칭을 FastAPI에 위임: jobId={}, scenes={}, chunks={}",
                jobId, scenes.size(), ttsResult.getChunks() != null ? ttsResult.getChunks().size() : 0);


        // 6. 롱폼 영상 재조립 (FastAPI 호출)
        log.info("재조립을 위한 롱폼 인코딩 시작: jobId={}", jobId);
        
        // 새로 업데이트된 씬 에셋 목록을 다시 로드
        List<Asset> updatedSceneAssets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);
        updatedSceneAssets.sort((a, b) -> {
            try {
                SceneImageDto dtoA = objectMapper.readValue(a.getMetaJson(), SceneImageDto.class);
                SceneImageDto dtoB = objectMapper.readValue(b.getMetaJson(), SceneImageDto.class);
                return Integer.compare(dtoA.getIndex(), dtoB.getIndex());
            } catch (Exception e) {
                return 0;
            }
        });
        
        List<Asset> gifAssets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.GIF_CLIP);

        String scenesJson = safeJson(updatedSceneAssets.stream()
                .map(Asset::getMetaJson).toList());
        String gifsJson = safeJson(gifAssets.stream()
                .map(Asset::getMetaJson).toList());

        // BGM 생성 (재조립 시에도 BGM 재성성)
        try {
            int durationSeconds = ttsResult.getTotalDuration() != null ? ttsResult.getTotalDuration().intValue() : 60;
            String category = job.getCategory() != null ? job.getCategory().name() : "CUSTOM";
            fastApiClient.generateBgm(jobId, category, durationSeconds);
        } catch (Exception e) {
            log.error("재조립 시 BGM 생성 오류: {}", e.getMessage());
        }

        LongformGenerateResponse result = fastApiClient.generateLongform(
                jobId, safeJson(ttsResult), scenesJson, gifsJson);

        // 7. LONGFORM_VIDEO 에셋 저장 및 job outputPath 업데이트
        Asset videoAsset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.LONGFORM_VIDEO)
                .localPath(result.getVideoPath())
                .metaJson(safeJson(result))
                .build();
        assetRepository.save(videoAsset);
        saveQcReport(jobId, result);

        job.setOutputPath(result.getVideoPath());
        jobRepository.save(job);

        // [체크리스트 피드백 반영] 유튜브 패키지(썸네일, 제목, 더보기글, 태그) 자동 생성 추가
        try {
            log.info("수정 반영(재조립): 유튜브 패키지(썸네일, 제목, 더보기글) 자동 생성 시작: jobId={}", jobId);
            // 기존 메타데이터/썸네일이 있을 수 있으므로 덮어쓰기 생성
            log.info("재조립 후 썸네일 자동 생성을 건너뜁니다: jobId={}", jobId);
        } catch (Exception e) {
            log.error("수정 반영(재조립): 유튜브 패키지 자동 생성 실패: {}", e.getMessage());
        }

        // 비용 기록 (재조립 추가 비용)
        costService.record(jobId, "FFMPEG_REASSEMBLE", BigDecimal.ZERO, "USD",
                String.format("롱폼 재조립: %.0f초, %d씬", 
                        result.getDurationSeconds(), result.getSceneCount()));

        log.info("롱폼 동영상 재조립 완료: jobId={}, path={}", jobId, result.getVideoPath());

        // 8. 쇼츠 시나리오 자동 추출 (수정 반영 시)
        try {
            log.info("수정 반영(재조립): 자동 쇼츠 시나리오 추출 시작: jobId={}", jobId);
            List<Map<String, Object>> parsedScenes = updatedSceneAssets.stream()
                .map(a -> {
                    try {
                        return (Map<String, Object>) objectMapper.readValue(a.getMetaJson(), Map.class);
                    } catch(Exception e) {
                        return null;
                    }
                }).filter(java.util.Objects::nonNull).collect(java.util.stream.Collectors.toList());

            if (!parsedScenes.isEmpty()) {
                Object scenarios = fastApiClient.extractShortsScenarios(jobId, parsedScenes);
                
                // 기존 시나리오 삭제 후 새로 추가 (중복 방지)
                assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SHORTS_SCENARIO)
                    .ifPresent(a -> assetRepository.delete(a));
                
                Asset scenarioAsset = Asset.builder()
                        .jobId(jobId)
                        .assetType(AssetType.SHORTS_SCENARIO)
                        .metaJson(objectMapper.writeValueAsString(scenarios))
                        .build();
                assetRepository.save(scenarioAsset);
                log.info("수정 반영(재조립): 쇼츠 시나리오 자동 추출 완료 및 저장 성공: jobId={}", jobId);
            }
        } catch(Exception e) {
            log.error("수정 반영(재조립): 쇼츠 시나리오 자동 추출 실패: {}", e.getMessage());
        }

        return result;
    }

    private void saveQcReport(Long jobId, LongformGenerateResponse result) {
        if (result.getQualityReport() == null) return;
        Object outputQc = result.getQualityReport().get("output_qc");
        if (outputQc == null) return;
        Asset qcAsset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.QC_REPORT)
                .metaJson(safeJson(outputQc))
                .build();
        assetRepository.save(qcAsset);
    }
}
