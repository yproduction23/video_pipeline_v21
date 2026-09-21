package com.pipeline.video.service;

import com.pipeline.video.domain.*;
import com.pipeline.video.config.PricingConfig;
import com.pipeline.video.dto.*;
import com.pipeline.video.repository.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
@Slf4j
public class JobService {

    private final VideoJobRepository jobRepository;
    private final AssetRepository assetRepository;
    private final ChannelProfileRepository channelProfileRepository;
    private final CostLedgerRepository costLedgerRepository;
    private final ApprovalRepository approvalRepository;
    private final FastApiClient fastApiClient;
    private final CharacterAssetResolver characterAssetResolver;
    private final ThumbnailPersonResolver thumbnailPersonResolver;
    private final WorkflowOrchestrator workflowOrchestrator;
    private final CostService costService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Transactional
    public JobResponse createJob(CreateJobRequest request, String username) {
        // category null이면 CUSTOM
        Category category = request.getCategory() != null ? request.getCategory() : Category.CUSTOM;

        // 영상 길이: null이면 20분 default
        Integer targetMinutes = request.getLongformTargetMinutes() != null
                ? request.getLongformTargetMinutes() : 20;
        BigDecimal policyBudgetCap = PricingConfig.budgetCapForTargetMinutes(targetMinutes);

        Autonomy requestedAutonomy = request.getAutonomy() == Autonomy.AUTO
                ? Autonomy.AUTO : Autonomy.GUIDED;

        BigDecimal effectiveVideoBudget = (request.getBudgetCap() != null && request.getBudgetCap().compareTo(BigDecimal.ZERO) > 0)
                ? request.getBudgetCap()
                : policyBudgetCap;

        VideoJob job = VideoJob.builder()
                .title(request.getTitle())
                .keyword(request.getKeyword())
                .keywordPlanId(request.getKeywordPlanId())
                .category(category)
                .status(JobStatus.DRAFT)
                .autonomy(requestedAutonomy)
                .format(request.getFormat())
                .renderProfile(request.getRenderProfile())
                .makeShorts(request.isMakeShorts())
                .shortsCount(request.getShortsCount())
                .longformTargetMinutes(targetMinutes)
                .budgetCap(effectiveVideoBudget)
                .geminiImageBudgetCap(PricingConfig.geminiImageBudgetCapFor(
                        request.getGeminiImageBudgetCap(), effectiveVideoBudget))
                .costAccumulated(BigDecimal.ZERO)
                .policyJson(request.getPolicyJson())
                .channelId(request.getChannelId())
                .contentNature(resolveContentNature(request))
                .characterOverride(request.getCharacterOverride())
                .dataVisualsEnabled(request.isDataVisualsEnabled())
                .createdBy(username)
                .build();

        return JobResponse.from(jobRepository.save(job));
    }

    /** 요청값 → 채널 기본값 → FACTUAL 순으로 콘텐츠 성격을 정한다. */
    ContentNature resolveContentNature(CreateJobRequest request) {
        if (request.getContentNature() != null) {
            return request.getContentNature();
        }
        if (request.getChannelId() != null && !request.getChannelId().isBlank()) {
            return channelProfileRepository.findById(request.getChannelId())
                    .map(ChannelProfile::getContentNature)
                    .orElse(ContentNature.FACTUAL);
        }
        return ContentNature.FACTUAL;
    }

    public List<JobResponse> getMyJobs(String username) {
        return jobRepository.findByCreatedByOrderByCreatedAtDesc(username)
                .stream()
                .peek(this::syncJobCost)
                .map(JobResponse::from)
                .collect(Collectors.toList());
    }

    /** Resume a job that failed before any script asset was produced.
     *
     * The keyword selection remains valid, so the restarted workflow receives
     * the KEYWORD signal and resumes at GenerateScript rather than repeating
     * discovery or creating a second job.
     */
    private void runAfterCommit(Runnable runnable) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    runnable.run();
                }
            });
        } else {
            runnable.run();
        }
    }

    /**
     * 실패하거나 중단된 작업을 스마트 재개(Smart Resume)합니다.
     *
     * 이미 완성/승인된 이전 단계 결과물(키워드/대본/TTS/이미지)이 존재하면
     * 손상시키지 않고 그대로 보존한 채, 실패한/멈춘 해당 단계부터 즉시 재개합니다.
     */
    @Transactional
    public JobResponse retryFromScript(Long jobId) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        if (job.getStatus() != JobStatus.FAILED && job.getStatus() != JobStatus.IMAGES_RETRY_REQUIRED) {
            throw new IllegalStateException("Only failed jobs can be resumed. Current status: " + job.getStatus());
        }

        boolean hasScript = !assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCRIPT).isEmpty();
        boolean hasTts = !assetRepository.findByJobIdAndAssetType(jobId, AssetType.TTS_AUDIO).isEmpty();
        boolean hasImages = !assetRepository.findByJobIdAndAssetType(jobId, AssetType.IMAGE_BATCH).isEmpty()
                || !assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE).isEmpty();

        // 1. 대본, TTS, 이미지까지 모두 정상 생성된 경우 -> 롱폼 조립(ASSEMBLING) 단계부터 재개
        if (hasScript && hasTts && hasImages) {
            log.info("기존 대본·TTS·이미지 보존 — 롱폼 조립 단계부터 즉시 재개: jobId={}", jobId);
            job.setStatus(JobStatus.ASSEMBLING);
            VideoJob saved = jobRepository.save(job);
            runAfterCommit(() -> {
                workflowOrchestrator.startPipeline(jobId);
                workflowOrchestrator.sendApproveSignal(jobId, GateName.KEYWORD.name());
                workflowOrchestrator.sendApproveSignal(jobId, GateName.SCRIPT.name());
                workflowOrchestrator.sendApproveSignal(jobId, GateName.TTS.name());
                workflowOrchestrator.sendApproveSignal(jobId, GateName.IMAGES.name());
            });
            return JobResponse.from(saved);
        }

        // 2. 이미 대본과 TTS가 확정된 상태인 경우 -> 이미지 생성(IMAGES_PENDING) 단계부터 재개
        if (hasScript && hasTts) {
            log.info("기존 대본·TTS 보존 — 이미지 생성 단계부터 즉시 재개: jobId={}", jobId);
            assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.IMAGE_BATCH);
            assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);

            job.setStatus(JobStatus.IMAGES_PENDING);
            VideoJob saved = jobRepository.save(job);
            runAfterCommit(() -> {
                workflowOrchestrator.startPipeline(jobId);
                workflowOrchestrator.sendApproveSignal(jobId, GateName.KEYWORD.name());
                workflowOrchestrator.sendApproveSignal(jobId, GateName.SCRIPT.name());
                workflowOrchestrator.sendApproveSignal(jobId, GateName.TTS.name());
            });
            return JobResponse.from(saved);
        }

        // 3. 대본은 확정 및 승인되었으나 TTS 생성이 되지 않은 경우 -> 기존 대본 보존, TTS(TTS_PENDING)부터 재개
        if (hasScript) {
            log.info("기존 승인 대본 보존 — TTS 생성 단계부터 즉시 재개: jobId={}", jobId);
            assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.TTS_AUDIO);
            assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.IMAGE_BATCH);
            assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);

            job.setStatus(JobStatus.TTS_PENDING);
            VideoJob saved = jobRepository.save(job);
            runAfterCommit(() -> {
                workflowOrchestrator.startPipeline(jobId);
                workflowOrchestrator.sendApproveSignal(jobId, GateName.KEYWORD.name());
                workflowOrchestrator.sendApproveSignal(jobId, GateName.SCRIPT.name());
            });
            return JobResponse.from(saved);
        }

        // 4. 대본 이전(키워드 선택만 됨)에서 실패한 경우 -> 선택 키워드 보존, 대본 생성(SCRIPT_PENDING)부터 재개
        log.info("선택 키워드 보존 — 대본 생성 단계부터 재개: jobId={}", jobId);
        assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.SCRIPT);
        assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.TTS_AUDIO);
        assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.IMAGE_BATCH);
        assetRepository.deleteByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);

        job.setStatus(JobStatus.SCRIPT_PENDING);
        VideoJob saved = jobRepository.save(job);
        runAfterCommit(() -> {
            workflowOrchestrator.startPipeline(jobId);
            workflowOrchestrator.sendApproveSignal(jobId, GateName.KEYWORD.name());
        });
        return JobResponse.from(saved);
    }

    public JobResponse getJob(Long id) {
        return jobRepository.findById(id)
                .map(job -> {
                    syncJobCost(job);
                    return JobResponse.from(job);
                })
                .orElseThrow(() -> new RuntimeException("Job not found: " + id));
    }

    public List<JobResponse> getAllJobs() {
        return jobRepository.findAll()
                .stream()
                .peek(this::syncJobCost)
                .map(JobResponse::from)
                .collect(Collectors.toList());
    }

    private void syncJobCost(VideoJob job) {
        try {
            BigDecimal actualTotal = costService.calculateTotalCostKrw(job.getId());
            if (actualTotal != null && (job.getCostAccumulated() == null || job.getCostAccumulated().compareTo(actualTotal) != 0)) {
                job.setCostAccumulated(actualTotal);
                jobRepository.save(job);
            }
        } catch (Exception e) {
            log.warn("Job cost sync failed for jobId={}: {}", job.getId(), e.getMessage());
        }
    }

    @Transactional
    public JobResponse publishVideo(Long jobId) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        Optional<Asset> existingMeta = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.YOUTUBE_METADATA);
        if (existingMeta.isEmpty()) {
            generateYoutubePackage(jobId);
        }

        // 실제 OAuth 업로드와 YouTube 응답 검증이 연결되기 전에는 가짜 URL이나
        // PUBLISHED 상태를 만들지 않는다. 사용자는 명시적으로 게시 대기 상태를 본다.
        job.setYoutubeUrl(null);
        job.setStatus(JobStatus.PUBLISH_PENDING);
        jobRepository.save(job);
        
        log.info("유튜브 게시 대기: 실제 업로드 연동이 아직 구성되지 않았습니다. jobId={}", jobId);
        return JobResponse.from(job);
    }

    @Transactional
    @SuppressWarnings("unchecked")
    public void generateYoutubePackage(Long jobId) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        
        log.info("유튜브 패키지(메타데이터, 썸네일) 생성 시작: jobId={}", jobId);
        
        Optional<Asset> scriptAssetOpt = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT);
        if (scriptAssetOpt.isEmpty()) {
            log.warn("대본 에셋이 없어 유튜브 패키지 생성을 건너뜁니다. jobId={}", jobId);
            IllegalStateException exception = new IllegalStateException("유튜브 패키지 생성에 대본 에셋이 필요합니다.");
            saveYoutubePackageFailure(jobId, "SCRIPT_ASSET", exception);
            throw exception;
        }
        
        String scriptText = "";
        try {
            ScriptGenerateResponse scriptDto = objectMapper.readValue(scriptAssetOpt.get().getMetaJson(), ScriptGenerateResponse.class);
            scriptText = scriptDto.getScript();
        } catch (Exception e) {
            scriptText = scriptAssetOpt.get().getMetaJson();
        }
        
        Map<String, Object> longformMeta = null;
        Map<String, Object> shortsMeta = null;
        try {
            longformMeta = fastApiClient.generateYoutubeMetadata(scriptText, false);
        } catch (Exception e) {
            log.error("롱폼 유튜브 메타데이터 생성 실패: {}", e.getMessage());
            saveYoutubePackageFailure(jobId, "LONGFORM_METADATA", e);
            longformMeta = fallbackYoutubeMetadata(job, false);
        }
        
        if (job.isMakeShorts()) {
            String shortsScriptText = scriptText;
            Optional<Asset> shortsScenarioOpt = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SHORTS_SCENARIO);
            if (shortsScenarioOpt.isPresent()) {
                shortsScriptText = shortsScenarioOpt.get().getMetaJson();
            }
            try {
                shortsMeta = fastApiClient.generateYoutubeMetadata(shortsScriptText, true);
            } catch (Exception e) {
                log.error("쇼츠 유튜브 메타데이터 생성 실패: {}", e.getMessage());
            saveYoutubePackageFailure(jobId, "SHORTS_METADATA", e);
                shortsMeta = fallbackYoutubeMetadata(job, true);
            }
        }
        
        Map<String, Object> youtubePackage = new java.util.HashMap<>();
        youtubePackage.put("longform", longformMeta);
        youtubePackage.put("shorts", shortsMeta);
        
        assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.YOUTUBE_METADATA)
                .ifPresent(assetRepository::delete);
        
        Asset metadataAsset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.YOUTUBE_METADATA)
                .metaJson(safeJson(youtubePackage))
                .build();
        assetRepository.save(metadataAsset);
        
        CharacterAssetResolver.ResolvedCharacter character = characterAssetResolver.resolve(job);
        String referenceStyleProfile = job.getChannelId() == null || job.getChannelId().isBlank()
                ? "black_han_sans_v1"
                : channelProfileRepository.findById(job.getChannelId())
                    .map(ChannelProfile::getReferenceStyleProfile)
                    .filter(value -> value != null && !value.isBlank())
                    .orElse("black_han_sans_v1");
        List<Map<String, Object>> sceneCandidates = buildThumbnailSceneCandidates(jobId);
        Map<String, Object> thumbnailBrief = loadThumbnailBrief(scriptAssetOpt.get().getMetaJson(), longformTitleFallback(job));
        Map<String, Object> characterIdentity = new java.util.HashMap<>();
        characterIdentity.put("profile_id", character.profileId());
        characterIdentity.put("identity_hash", character.identityHash());
        characterIdentity.put("character_key", character.profileId());
        
        String longformTitle = job.getTitle();
        if (longformMeta != null && longformMeta.containsKey("titles")) {
            List<String> titles = (List<String>) longformMeta.get("titles");
            if (titles != null && !titles.isEmpty()) longformTitle = titles.get(0);
        }
        List<Map<String, Object>> personPhotos = thumbnailPersonResolver.resolve(
                thumbnailBrief, longformTitle, job.getKeyword(), scriptText
        );
        log.info("자동 썸네일용 승인 인물 사진 연결: jobId={}, count={}", jobId, personPhotos.size());
        
        String longformThumbPath = "/app/data/jobs/" + jobId + "/longform_thumbnail.png";
        String shortsThumbPath = "/app/data/jobs/" + jobId + "/shorts_thumbnail.png";
        
        Map<String, Object> longformThumbnailResult = java.util.Map.of();
        try {
            longformThumbnailResult = fastApiClient.generateThumbnailImage(jobId, longformTitle, "longform", longformThumbPath,
                    character.imagePath(), character.stylePrompt(), character.loraModelId(), character.loraTriggerWord(),
                    character.loraScale() == null ? 1.0 : character.loraScale().doubleValue(),
                    sceneCandidates, thumbnailBrief, characterIdentity, personPhotos, character.watermarkPath(),
                    referenceStyleProfile, null, false);
        } catch (Exception e) {
            log.error("롱폼 썸네일 생성 실패: {}", e.getMessage());
            saveYoutubePackageFailure(jobId, "LONGFORM_THUMBNAIL", e);
            return;
        }
        
        if (job.isMakeShorts()) {
            String shortsTitle = longformTitle;
            if (shortsMeta != null && shortsMeta.containsKey("titles")) {
                List<String> sTitles = (List<String>) shortsMeta.get("titles");
                if (sTitles != null && !sTitles.isEmpty()) shortsTitle = sTitles.get(0);
            }
            try {
                fastApiClient.generateThumbnailImage(jobId, shortsTitle, "shorts", shortsThumbPath,
                        character.imagePath(), character.stylePrompt(), character.loraModelId(), character.loraTriggerWord(),
                        character.loraScale() == null ? 1.0 : character.loraScale().doubleValue(),
                        sceneCandidates, thumbnailBrief, characterIdentity, personPhotos, character.watermarkPath(),
                        referenceStyleProfile, null, false);
            } catch (Exception e) {
                log.error("쇼츠 썸네일 생성 실패: {}", e.getMessage());
                saveYoutubePackageFailure(jobId, "SHORTS_THUMBNAIL", e);
            }
        }
        
        Map<String, Object> thumbPaths = new java.util.HashMap<>();
        thumbPaths.put("longform_path", "/api/jobs/" + jobId + "/thumbnail/longform");
        thumbPaths.put("shorts_path", "/api/jobs/" + jobId + "/thumbnail/shorts");
        thumbPaths.put("longform_result", longformThumbnailResult);
        thumbPaths.put("character_identity", characterIdentity);
        thumbPaths.put("source_mode", sceneCandidates.isEmpty() ? "ai_fallback" : "scene");
        thumbPaths.put("person_matches", personPhotos.stream().map(photo -> java.util.Map.of(
                "person_id", String.valueOf(photo.getOrDefault("person_id", "")),
                "person_name", String.valueOf(photo.getOrDefault("person_name", "")),
                "photo_id", String.valueOf(photo.getOrDefault("photo_id", "")),
                "match_term", String.valueOf(photo.getOrDefault("match_term", "")),
                "match_source", String.valueOf(photo.getOrDefault("match_source", ""))
        )).toList());
        
        assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.THUMBNAIL_IMAGE)
                .ifPresent(assetRepository::delete);
        
        Asset thumbnailAsset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.THUMBNAIL_IMAGE)
                .localPath(longformThumbPath)
                .metaJson(safeJson(thumbPaths))
                .build();
        assetRepository.save(thumbnailAsset);
        
        log.info("유튜브 패키지 생성 완료: jobId={}", jobId);
    }

    private String longformTitleFallback(VideoJob job) {
        return job.getTitle() == null ? "시장 핵심 이슈" : job.getTitle();
    }

    /** 공급자 메타데이터 API가 불가해도 완성된 영상·썸네일 배포 흐름을 유지한다. */
    private Map<String, Object> fallbackYoutubeMetadata(VideoJob job, boolean shorts) {
        String title = longformTitleFallback(job);
        if (shorts) title = title + " 쇼츠";
        Map<String, Object> fallback = new java.util.HashMap<>();
        fallback.put("titles", java.util.List.of(title));
        fallback.put("description", "자동 생성 메타데이터를 사용할 수 없어 작업 제목을 기본값으로 사용했습니다.");
        fallback.put("tags", job.getKeyword() == null || job.getKeyword().isBlank()
                ? java.util.List.of() : java.util.List.of(job.getKeyword()));
        fallback.put("generation_status", "fallback_metadata");
        return fallback;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> loadThumbnailBrief(String scriptMetaJson, String fallbackTitle) {
        try {
            Map<String, Object> parsed = objectMapper.readValue(scriptMetaJson, Map.class);
            Object brief = parsed.get("thumbnail_brief");
            if (brief instanceof Map<?, ?> map) return new java.util.HashMap<>((Map<String, Object>) map);
        } catch (Exception exception) {
            log.warn("썸네일 브리프 복원 실패: {}", exception.getMessage());
        }
        Map<String, Object> fallback = new java.util.HashMap<>();
        fallback.put("hook_line", "{y:" + fallbackTitle + "}");
        fallback.put("punch_line", "{y:핵심 정리}");
        fallback.put("source_scene_ids", java.util.List.of("0"));
        return fallback;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> buildThumbnailSceneCandidates(Long jobId) {
        List<Map<String, Object>> candidates = new java.util.ArrayList<>();
        Optional<Asset> manifestAsset = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.ASSEMBLY_MANIFEST);
        if (manifestAsset.isPresent() && manifestAsset.get().getLocalPath() != null) {
            try {
                String payload = Files.readString(Path.of(manifestAsset.get().getLocalPath()));
                Map<String, Object> manifest = objectMapper.readValue(payload, Map.class);
                Object rawScenes = manifest.get("scenes");
                if (rawScenes instanceof List<?> scenes) {
                    for (Object value : scenes) {
                        if (!(value instanceof Map<?, ?> rawScene)) continue;
                        Map<String, Object> candidate = new java.util.HashMap<>((Map<String, Object>) rawScene);
                        if (!Boolean.TRUE.equals(candidate.get("used_in_final_video"))) continue;
                        Object imagePath = candidate.get("image_path");
                        if (!(imagePath instanceof String path) || path.isBlank()) continue;
                        candidate.putIfAbsent("scene_id", String.valueOf(candidate.getOrDefault("index", candidates.size())));
                        candidates.add(candidate);
                    }
                }
            } catch (Exception exception) {
                log.warn("조립 매니페스트에서 썸네일 후보 복원 실패: {}", exception.getMessage());
            }
            if (!candidates.isEmpty()) return candidates;
        }
        for (Asset asset : assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE)) {
            try {
                Map<String, Object> scene = objectMapper.readValue(asset.getMetaJson(), Map.class);
                String path = scene.get("image_path") instanceof String value ? value : asset.getLocalPath();
                if (path == null || path.isBlank()) continue;
                Map<String, Object> candidate = new java.util.HashMap<>(scene);
                candidate.put("image_path", path);
                candidate.put("used_in_final_video", true);
                candidate.putIfAbsent("scene_id", String.valueOf(scene.getOrDefault("index", candidates.size())));
                candidates.add(candidate);
            } catch (Exception exception) {
                log.warn("썸네일 후보 씬 메타데이터 복원 실패: assetId={}, error={}", asset.getId(), exception.getMessage());
            }
        }
        return candidates;
    }

    /**
     * Promotes an already-rendered, scene-backed thumbnail candidate.  No
     * image generation runs here: the selected file is copied to the legacy
     * primary path consumed by the YouTube package and download endpoint.
     */
    @Transactional
    public Map<String, Object> selectThumbnailVariant(Long jobId, String format, int variant) {
        if (!"longform".equals(format) && !"shorts".equals(format)) {
            throw new IllegalArgumentException("thumbnail format must be longform or shorts");
        }
        if (variant < 1 || variant > 3) {
            throw new IllegalArgumentException("thumbnail variant must be between 1 and 3");
        }
        String baseName = format + "_thumbnail";
        Path jobDirectory = Path.of("/app/data/jobs", String.valueOf(jobId));
        Path source = jobDirectory.resolve(baseName + "_v" + variant + ".png");
        if (!Files.isRegularFile(source) && variant == 1) source = jobDirectory.resolve(baseName + ".png");
        Path target = jobDirectory.resolve(baseName + ".png");
        if (!Files.isRegularFile(source)) {
            throw new IllegalStateException("thumbnail variant not found: " + variant);
        }
        try {
            if (!source.equals(target)) {
                Files.copy(source, target, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (java.io.IOException exception) {
            throw new IllegalStateException("thumbnail variant promotion failed", exception);
        }

        assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.THUMBNAIL_IMAGE)
                .ifPresent(asset -> {
                    Map<String, Object> metadata;
                    try {
                        metadata = objectMapper.readValue(asset.getMetaJson(), Map.class);
                    } catch (Exception ignored) {
                        metadata = new java.util.HashMap<>();
                    }
                    metadata.put(format + "_selected_variant", variant);
                    metadata.put(format + "_path", "/api/jobs/" + jobId + "/thumbnail/" + format);
                    asset.setLocalPath(target.toString());
                    asset.setMetaJson(safeJson(metadata));
                    assetRepository.save(asset);
                });
        return Map.of("format", format, "selected_variant", variant, "path", target.toString());
    }

    /** Re-render only the approved thumbnail candidates; script/video assets stay untouched. */
    @Transactional
    public Map<String, Object> regenerateThumbnail(Long jobId, String format, String preset) {
        if (!"longform".equals(format) && !"shorts".equals(format)) {
            throw new IllegalArgumentException("thumbnail format must be longform or shorts");
        }
        if (preset != null && !preset.isBlank() && !List.of("person_led", "mascot_led", "chart_led").contains(preset)) {
            throw new IllegalArgumentException("unsupported thumbnail preset");
        }
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new IllegalArgumentException("Job not found"));
        Optional<Asset> scriptAsset = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT);
        if (scriptAsset.isEmpty()) throw new IllegalStateException("thumbnail regeneration requires a script asset");
        List<Map<String, Object>> candidates = buildThumbnailSceneCandidates(jobId);
        if (candidates.isEmpty()) throw new IllegalStateException("thumbnail regeneration requires final-video scene candidates");

        Map<String, Object> brief = loadThumbnailBrief(scriptAsset.get().getMetaJson(), longformTitleFallback(job));
        String scriptText = scriptAsset.get().getMetaJson();
        CharacterAssetResolver.ResolvedCharacter character = characterAssetResolver.resolve(job);
        Map<String, Object> identity = new java.util.HashMap<>();
        identity.put("profile_id", character.profileId());
        identity.put("identity_hash", character.identityHash());
        identity.put("character_key", character.profileId());
        List<Map<String, Object>> people = thumbnailPersonResolver.resolve(brief, job.getTitle(), job.getKeyword(), scriptText);
        String styleProfile = job.getChannelId() == null || job.getChannelId().isBlank()
                ? "black_han_sans_v1"
                : channelProfileRepository.findById(job.getChannelId()).map(ChannelProfile::getReferenceStyleProfile)
                    .filter(value -> value != null && !value.isBlank()).orElse("black_han_sans_v1");
        Path jobDirectory = Path.of("/app/data/jobs", String.valueOf(jobId));
        String outputPath = jobDirectory.resolve(format + "_thumbnail.png").toString();
        archiveThumbnailVariants(jobDirectory, format);
        Map<String, Object> result = fastApiClient.generateThumbnailImage(
                jobId, job.getTitle(), format, outputPath,
                character.imagePath(), character.stylePrompt(), character.loraModelId(), character.loraTriggerWord(),
                character.loraScale() == null ? 1.0 : character.loraScale().doubleValue(),
                candidates, brief, identity, people, character.watermarkPath(), styleProfile, preset, true);

        assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.THUMBNAIL_IMAGE).ifPresent(asset -> {
            Map<String, Object> metadata;
            try { metadata = objectMapper.readValue(asset.getMetaJson(), Map.class); }
            catch (Exception ignored) { metadata = new java.util.HashMap<>(); }
            int version = ((Number) metadata.getOrDefault("thumbnail_regeneration_version", 0)).intValue() + 1;
            metadata.put("thumbnail_regeneration_version", version);
            metadata.put(format + "_result", result);
            metadata.put(format + "_selected_variant", ((Number) result.getOrDefault("selected_variant", 0)).intValue() + 1);
            asset.setMetaJson(safeJson(metadata));
            assetRepository.save(asset);
        });
        return Map.of("format", format, "result", result, "preset", preset == null ? "auto" : preset);
    }

    private void archiveThumbnailVariants(Path jobDirectory, String format) {
        try {
            Path archive = jobDirectory.resolve("thumbnail_history").resolve(format + "-" + System.currentTimeMillis());
            Files.createDirectories(archive);
            for (int variant = 1; variant <= 3; variant++) {
                String suffix = variant == 1 ? ".png" : "_v" + variant + ".png";
                Path source = jobDirectory.resolve(format + "_thumbnail" + suffix);
                if (Files.isRegularFile(source)) Files.copy(source, archive.resolve(source.getFileName()));
            }
        } catch (java.io.IOException exception) {
            throw new IllegalStateException("thumbnail history archive failed", exception);
        }
    }

    @Transactional
    public JobResponse stopJob(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.READY || job.getStatus() == JobStatus.PUBLISHED || job.getStatus() == JobStatus.FAILED) {
            log.info("Job {} is already in terminal state {}, skip stop request.", jobId, job.getStatus());
            return JobResponse.from(job);
        }

        log.info("Job {} 중지 요청 (by {}). 현재 상태: {}", jobId, username, job.getStatus());
        job.setStatus(JobStatus.FAILED);
        VideoJob savedJob = jobRepository.save(job);

        // [긴급 수정] 기존에는 FastAPI 워커에만 중지 명령을 보냈는데, Temporal
        // Workflow가 파이프라인 실행을 담당하게 된 지금은 Workflow 자체도
        // 취소해야 실제로 다음 단계(TTS/이미지/조립)로 안 넘어갑니다.
        // FastAPI stopJob()은 이미 실행 중인 개별 프로세스(ffmpeg 등)를 죽이는
        // 역할이고, Temporal cancelPipeline()은 "다음 단계로 진행하지 않게"
        // 막는 역할이라 둘 다 필요합니다.
        workflowOrchestrator.cancelPipeline(jobId);

        // FastAPI 워커에 중지 명령 전송
        fastApiClient.stopJob(jobId);

        return JobResponse.from(savedJob);
    }

    @Transactional
    public void deleteJob(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() != JobStatus.DRAFT && job.getStatus() != JobStatus.READY && job.getStatus() != JobStatus.FAILED) {
            throw new IllegalStateException("진행 중인 작업(현재 상태: " + job.getStatus() + ")은 삭제할 수 없습니다. 먼저 중지해 주세요.");
        }

        log.info("Job {} 삭제 시작 (by {})", jobId, username);

        assetRepository.deleteByJobId(jobId);
        costLedgerRepository.deleteByJobId(jobId);
        approvalRepository.deleteByJobId(jobId);
        jobRepository.delete(job);

        // FastAPI 워커에 리소스 삭제 통지
        fastApiClient.deleteJob(jobId);

        log.info("Job {} 삭제 완료", jobId);
    }

    private void saveYoutubePackageFailure(Long jobId, String stage, Exception error) {
        Asset failure = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.YOUTUBE_PACKAGE_FAILURE)
                .metaJson(safeJson(Map.of(
                        "stage", stage,
                        "retryable", true,
                        "message", error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage()
                )))
                .build();
        assetRepository.save(failure);
    }

    private String safeJson(Object obj) {
        try {
            return objectMapper.writeValueAsString(obj);
        } catch (Exception e) {
            return "{}";
        }
    }
}
