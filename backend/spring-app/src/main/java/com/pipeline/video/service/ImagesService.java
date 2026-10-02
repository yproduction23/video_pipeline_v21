package com.pipeline.video.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.*;
import com.pipeline.video.dto.GifClipDto;
import com.pipeline.video.dto.ImagesGenerateResponse;
import com.pipeline.video.dto.SceneImageDto;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import com.pipeline.video.repository.ChannelProfileRepository;
import com.pipeline.video.config.PricingConfig;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Map;
import java.util.List;

/**
 * Phase 3-4 — 이미지 + GIF 생성 서비스
 *
 *  - generate(): TTS chunks 기반 씬 이미지 + 섹션 전환점 GIF 생성
 *  - confirm(): IMAGES 게이트 통과 → ASSEMBLING
 *
 *  산출물은 Phase 3-5 롱폼 조립에서 직접 참조됨.
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class ImagesService {

    private static final String PROVIDER_CREDIT_ERROR_CODE = "IMAGE_PROVIDER_CREDIT_REQUIRED";
    private static final String PROVIDER_TEMPORARILY_UNAVAILABLE_ERROR_CODE =
            "IMAGE_PROVIDER_TEMPORARILY_UNAVAILABLE";
    private static final String IMAGE_GENERATION_ALREADY_RUNNING = "Image generation is already running";

    /**
     * A provider billing/quota failure is terminal for this attempt, but not
     * for the user's job: previously rendered scenes are resumable after the
     * account is funded.  Do not roll back the retry-required job state.
     */
    public static class ImageProviderRetryRequiredException extends RuntimeException {
        public ImageProviderRetryRequiredException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    public static class ImageProviderTemporarilyUnavailableException extends RuntimeException {
        public ImageProviderTemporarilyUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private final VideoJobRepository jobRepository;
    private final AssetRepository assetRepository;
    private final ChannelProfileRepository channelProfileRepository;
    private final CharacterAssetResolver characterAssetResolver;
    private final FastApiClient fastApiClient;
    private final GateService gateService;
    private final AutonomyService autonomyService;
    private final CostService costService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Transactional(noRollbackFor = {
            ImageProviderRetryRequiredException.class,
            ImageProviderTemporarilyUnavailableException.class
    })
    public ImagesGenerateResponse generate(Long jobId, String username) {
        return generate(jobId, username, null);
    }

    /**
     * sceneIndices를 지정하면 검토 필요 목록에서 사용자가 고른 씬만 재시도한다.
     * 비워두면(null) 기존처럼 전체 씬을 처리한다.
     */
    @Transactional(noRollbackFor = {
            ImageProviderRetryRequiredException.class,
            ImageProviderTemporarilyUnavailableException.class
    })
    public ImagesGenerateResponse generate(Long jobId, String username, List<Integer> sceneIndices) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING || job.getStatus() == JobStatus.SCRIPT_PENDING || job.getStatus() == JobStatus.TTS_PENDING) {
            throw new IllegalStateException("TTS 확정 전에는 이미지를 생성할 수 없습니다. 현재: " + job.getStatus());
        }
        if (job.getStatus() == JobStatus.IMAGES_RETRY_REQUIRED) {
            // Keep approved keyword/script/TTS assets; only the image gate is
            // reopened after a terminal provider batch error.
            job.setStatus(JobStatus.IMAGES_PENDING);
            jobRepository.save(job);
        }
        if (job.getStatus() == JobStatus.FAILED) {
            // 이미지 전 검증 오류처럼 이미 확정된 TTS를 손상시키지 않은 실패는
            // 같은 API 재시도로 이미지 게이트부터 안전하게 재개할 수 있다.
            job.setStatus(JobStatus.IMAGES_PENDING);
            jobRepository.save(job);
        }

        // TTS chunks 로드
        String ttsMetaJson = loadAssetMeta(jobId, AssetType.TTS_AUDIO);
        // 스크립트 로드
        String scriptMetaJson = loadAssetMeta(jobId, AssetType.SCRIPT);

        CharacterAssetResolver.ResolvedCharacter character = characterAssetResolver.resolve(job);
        log.info("단일 캐릭터 정체성 해석 완료: jobId={}, profileId={}, hash={}",
                jobId, character.profileId(), character.identityHash().substring(0, 12));

        log.info("이미지 생성 시작: jobId={}, autonomy={}", jobId, job.getAutonomy());

        // FastAPI 호출
        // 선행 단계에서 이미 확정된 실제 비용을 제외한 잔여 KRW만 워커에 전달한다.
        // 워커의 이미지·Kling 요청 감사 원장은 이 금액을 넘는 외부 호출을 차단한다.
        BigDecimal remainingBudget = null;
        if (job.getBudgetCap() != null) {
            BigDecimal spent = costService.getTotal(jobId);
            remainingBudget = job.getBudgetCap().subtract(spent);
            if (remainingBudget.compareTo(BigDecimal.ZERO) <= 0) {
                throw new IllegalStateException("이전 단계 비용으로 이미지 생성 잔여 예산이 없습니다.");
            }
        }

        // Gemini 정지 이미지 상한은 Fal 모션 비용과 분리한다. 다만 전체 영상
        // 잔여 예산보다 클 수는 없으므로 둘 중 작은 값을 이미지 워커에 전달한다.
        BigDecimal geminiImageBudget = job.getGeminiImageBudgetCap();
        if (geminiImageBudget != null) {
            remainingBudget = remainingBudget == null
                    ? geminiImageBudget
                    : remainingBudget.min(geminiImageBudget);
        }

        ImagesGenerateResponse result;
        try {
            result = fastApiClient.generateImages(
                    jobId, ttsMetaJson, scriptMetaJson, character.imagePath(), character.stylePrompt(), character.posesDir(),
                    character.loraModelId(), character.loraTriggerWord(), character.loraScale(),
                    job.getAutonomy() == null ? null : job.getAutonomy().name(),
                    remainingBudget, PricingConfig.GEMINI_IMAGE_ONLY_POLICY_VERSION, sceneIndices);
        } catch (RuntimeException e) {
            if (isImageGenerationAlreadyRunning(e)) {
                // GUIDED 화면의 명시적 생성 요청과 Temporal 재개가 동시에 도착할
                // 수 있다. Redis 잠금이 이미 실제 한 건을 보호하고 있으므로 이를
                // 작업 실패로 바꾸지 말고, 먼저 시작한 요청의 완료를 기다린다.
                ImagesGenerateResponse alreadyRunning = new ImagesGenerateResponse();
                alreadyRunning.setStatus("ALREADY_RUNNING");
                alreadyRunning.setJobId(jobId);
                alreadyRunning.setReviewReasons(List.of("동일 작업의 이미지 생성이 이미 진행 중입니다."));
                log.info("중복 이미지 생성 요청을 정상 대기 상태로 처리: jobId={}", jobId);
                return alreadyRunning;
            }
            if (isProviderCreditRequired(e)) {
                job.setStatus(JobStatus.IMAGES_RETRY_REQUIRED);
                jobRepository.save(job);
                log.warn("이미지 공급자 크레딧/쿼터 부족으로 재시도 대기: jobId={}", jobId);
                throw new ImageProviderRetryRequiredException(
                        "이미지 공급자 크레딧 또는 쿼터가 부족합니다. 충전 후 이미지 생성만 다시 시도해 주세요.", e);
            }
            if (isProviderTemporarilyUnavailable(e)) {
                job.setStatus(JobStatus.IMAGES_PENDING);
                jobRepository.save(job);
                log.warn("Gemini Pro 과부하로 이미지 생성 재개 대기: jobId={}", jobId);
                throw new ImageProviderTemporarilyUnavailableException(
                        "Gemini Pro 이미지 생성 서비스가 현재 과부하입니다. 완료된 씬은 보존됐으며 잠시 후 이미지 생성만 다시 시도할 수 있습니다.",
                        e);
            }
            throw e;
        }

        if ("BATCH_PENDING".equals(result.getStatus())) {
            assetRepository.findByJobIdAndAssetType(jobId, AssetType.IMAGE_BATCH)
                    .forEach(assetRepository::delete);
            assetRepository.save(Asset.builder()
                    .jobId(jobId)
                    .assetType(AssetType.IMAGE_BATCH)
                    .localPath(result.getBatchJobName())
                    .metaJson(safeJson(result))
                    .build());
            log.info("Gemini Pro Batch submitted: jobId={}, batch={}", jobId, result.getBatchJobName());
            return result;
        }

        if (result.getScenes() != null) {
            for (SceneImageDto scene : result.getScenes()) {
                boolean alreadyRegistered = assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE)
                        .stream().anyMatch(existing -> scene.getImagePath().equals(existing.getLocalPath()));
                if (alreadyRegistered) continue;
                Asset asset = Asset.builder()
                        .jobId(jobId)
                        .assetType(AssetType.SCENE_IMAGE)
                        .localPath(scene.getImagePath())
                        .metaJson(safeJson(scene))
                        .build();
                assetRepository.save(asset);
            }
        }

        // Asset 저장 — GIF 클립
        if (result.getGifs() != null) {
            for (GifClipDto gif : result.getGifs()) {
                Asset asset = Asset.builder()
                        .jobId(jobId)
                        .assetType(AssetType.GIF_CLIP)
                        .localPath(gif.getGifPath())
                        .metaJson(safeJson(gif))
                        .build();
                assetRepository.save(asset);
            }
        }

        // AUTO 모드: 자동 confirm → ASSEMBLING
        assetRepository.findByJobIdAndAssetType(jobId, AssetType.IMAGE_QC_REPORT)
                .forEach(assetRepository::delete);
        assetRepository.save(Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.IMAGE_QC_REPORT)
                .metaJson(safeJson(result))
                .build());

        if (autonomyService.isAuto(job)) {
            if (result.isRequiresManualReview()) {
                // AUTO 모드라도 이미지 검수 실패 시 자동 확정 차단 — MANUAL/GUIDED와 동일 품질 게이트 적용
                // "no silent quality-degrading fallbacks" 원칙 준수 (AGENTS.md)
                job.setStatus(JobStatus.IMAGES_RETRY_REQUIRED);
                jobRepository.save(job);
                log.warn("AUTO 모드 — 이미지 검수 실패로 자동 확정 차단, 수동 검토 대기: jobId={}, reasons={}",
                        jobId, result.getReviewReasons());
                // confirm() 호출하지 않음 — 사람이 직접 UI에서 검수 후 확정 필요
            } else {
                log.info("AUTO 모드 — 이미지 자동 확정");
                confirm(jobId, "AUTO");
            }
        } else if (result.isRequiresManualReview()) {
            log.info("OCR 추정 사실 포함 등 수동 검토 필요: jobId={}, reasons={}",
                    jobId, result.getReviewReasons());
        }

        return result;
    }

    private static boolean isProviderCreditRequired(Throwable error) {
        for (Throwable current = error; current != null; current = current.getCause()) {
            String message = current.getMessage();
            if (message != null && message.contains(PROVIDER_CREDIT_ERROR_CODE)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isProviderTemporarilyUnavailable(Throwable error) {
        for (Throwable current = error; current != null; current = current.getCause()) {
            String message = current.getMessage();
            if (message != null && message.contains(PROVIDER_TEMPORARILY_UNAVAILABLE_ERROR_CODE)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isImageGenerationAlreadyRunning(Throwable error) {
        for (Throwable current = error; current != null; current = current.getCause()) {
            String message = current.getMessage();
            if (message != null && message.contains(IMAGE_GENERATION_ALREADY_RUNNING)) {
                return true;
            }
        }
        return false;
    }

    @Transactional
    public void completeBatch(Long jobId, Long batchAssetId, ImagesGenerateResponse result) {
        if (!"BATCH_COMPLETE".equals(result.getStatus()) || assetRepository.findById(batchAssetId).isEmpty()) {
            return;
        }
        if (result.getScenes() != null) {
            for (SceneImageDto scene : result.getScenes()) {
                Asset asset = Asset.builder()
                        .jobId(jobId)
                        .assetType(AssetType.SCENE_IMAGE)
                        .localPath(scene.getImagePath())
                        .metaJson(safeJson(scene))
                        .build();
                assetRepository.save(asset);
            }
        }
        assetRepository.deleteById(batchAssetId);
        assetRepository.findByJobIdAndAssetType(jobId, AssetType.IMAGE_QC_REPORT)
                .forEach(assetRepository::delete);
        assetRepository.save(Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.IMAGE_QC_REPORT)
                .metaJson(safeJson(result))
                .build());
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        if (autonomyService.shouldAutoApprove(job, GateName.IMAGES)) {
            gateService.tryAutoApproveAtCurrentStatus(jobId);
        }
        log.info("Gemini Pro Batch completed: jobId={}, scenes={}", jobId, result.getSceneCount());
    }

    /**
     * Retire a terminal Gemini batch failure.  Leaving IMAGE_BATCH behind
     * would make the scheduler poll the same failed remote batch forever.
     */
    @Transactional
    public void failBatch(Long jobId, Long batchAssetId, String reason) {
        assetRepository.findById(batchAssetId).ifPresent(asset -> {
            asset.setMetaJson(safeJson(Map.of(
                    "status", "BATCH_FAILED",
                    "batchJobName", asset.getLocalPath() == null ? "" : asset.getLocalPath(),
                    "error", reason == null ? "unknown batch failure" : reason
            )));
            assetRepository.save(asset);
        });
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        if (job.getStatus() == JobStatus.IMAGES_PENDING) {
            job.setStatus(JobStatus.IMAGES_RETRY_REQUIRED);
            jobRepository.save(job);
        }
        log.error("Gemini Pro Batch marked retry-required after terminal failure: jobId={}, reason={}", jobId, reason);
    }

    @Transactional
    public void confirm(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING || job.getStatus() == JobStatus.SCRIPT_PENDING || job.getStatus() == JobStatus.TTS_PENDING) {
            throw new IllegalStateException("TTS 확정 전에는 이미지를 확정할 수 없습니다. 현재: " + job.getStatus());
        }

        Asset imageQc = assetRepository
                .findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.IMAGE_QC_REPORT)
                .orElseThrow(() -> new IllegalStateException("이미지 검수 결과가 없습니다. 이미지를 다시 생성하세요."));
        try {
            ImagesGenerateResponse qc = objectMapper.readValue(imageQc.getMetaJson(), ImagesGenerateResponse.class);
            // AUTO 모드는 변경 1에서 isRequiresManualReview()=true 시 이미 차단되어 이 경로에 진입하지 않음.
            // username="AUTO" 는 내부 자동 확정 경로이며 별도 게이트를 거친 것으로 간주 (현재 범위 내 신뢰 경계).
            if (qc.isRequiresManualReview() && !"AUTO".equals(username)) {
                List<String> reasons = qc.getReviewReasons() == null ? List.of("상세 사유 없음") : qc.getReviewReasons();
                log.info("사용자 수동 승인 진행 (리뷰 사유 감지): jobId={}, reasons={}", jobId, reasons);
            }
        } catch (JsonProcessingException e) {
            log.warn("이미지 검수 결과 파싱 경고 (진행 허용): jobId={}, err={}", jobId, e.getMessage());
        }

        if (job.getStatus() == JobStatus.IMAGES_PENDING || job.getStatus() == JobStatus.IMAGES_RETRY_REQUIRED) {
            gateService.approve(jobId, GateName.IMAGES, username, "이미지/GIF 확정");
        } else {
            log.info("이미지 수정/재확정 완료 (상태 유지: {}): jobId={}", job.getStatus(), jobId);
        }
        log.info("이미지 확정 완료: jobId={}", jobId);
    }

    @Transactional
    public void updateScene(Long jobId, int index, String text, String subtitleText, String section, String mode) {
        updateSceneV2(jobId, index, text, subtitleText, section, mode);
    }

    /**
     * 이미지 편집은 두 동작만 허용한다. image_only는 승인된 프롬프트를
     * 재사용하고, text_and_image는 원문에서 프롬프트와 이미지를 다시 만든다.
     * 자막 단독 수정은 스크립트·TTS 계약을 깨므로 명시적으로 거부한다.
     */
    private void updateSceneV2(Long jobId, int index, String text, String subtitleText,
                               String section, String mode) {
        Asset target = null;
        SceneImageDto scene = null;
        for (Asset asset : assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE)) {
            try {
                SceneImageDto parsed = objectMapper.readValue(asset.getMetaJson(), SceneImageDto.class);
                if (parsed.getIndex() != null && parsed.getIndex() == index) {
                    target = asset;
                    scene = parsed;
                    break;
                }
            } catch (Exception ignored) {
                // Keep looking through legacy/malformed scene records.
            }
        }
        if (target == null || scene == null) {
            throw new IllegalArgumentException("Scene image not found: index=" + index);
        }

        if ("caption_only".equalsIgnoreCase(mode)) {
            throw new IllegalArgumentException(
                    "자막만 따로 수정할 수 없습니다. 스크립트를 수정한 뒤 TTS와 자막을 함께 다시 생성하세요."
            );
        }

        if ("text_and_image".equalsIgnoreCase(mode)) {
            if (text == null || text.isBlank()) {
                throw new IllegalArgumentException("Korean source text is required.");
            }
            scene.setText(text.trim());
            scene.setPromptKo(text.trim());
        }
        if (section != null && !section.isBlank()) {
            scene.setSection(section);
        }

        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        String characterImagePath = null;
        String characterStylePrompt = null;
        String characterPosesDir = null;
        String profileId = job.getCharacterOverride() != null && !job.getCharacterOverride().isBlank()
                ? job.getCharacterOverride() : job.getChannelId();
        if (profileId != null) {
            ChannelProfile profile = channelProfileRepository.findById(profileId).orElse(null);
            if (profile != null) {
                characterImagePath = profile.getCharacterImagePath();
                characterStylePrompt = profile.getCharacterStylePrompt();
                characterPosesDir = profile.getCharacterPosesDir();
            }
        }

        String approvedEnglishPrompt = "image_only".equalsIgnoreCase(mode) ? scene.getPromptEn() : null;
        SceneImageDto rendered = fastApiClient.regenerateSceneImage(
                jobId, index, scene.getText(), approvedEnglishPrompt, scene.getSection(),
                characterImagePath, characterStylePrompt, characterPosesDir, scene);
        if (rendered.getImagePath() != null && !rendered.getImagePath().isBlank()) {
            scene.setImagePath(rendered.getImagePath());
            target.setLocalPath(rendered.getImagePath());
        }
        if (rendered.getPromptEn() != null && !rendered.getPromptEn().isBlank()) {
            scene.setPromptEn(rendered.getPromptEn());
            scene.setPrompt(rendered.getPromptEn());
        }
        target.setMetaJson(safeJson(scene));
        assetRepository.save(target);
        log.info("Scene image updated: jobId={}, index={}, mode={}", jobId, index, mode);
    }

    @Transactional
    public void splitScene(Long jobId, int index, String part1, String part2) {
        List<Asset> assets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);
        
        java.util.List<Asset> sortedAssets = new java.util.ArrayList<>(assets);
        sortedAssets.sort((a, b) -> {
            try {
                SceneImageDto dtoA = objectMapper.readValue(a.getMetaJson(), SceneImageDto.class);
                SceneImageDto dtoB = objectMapper.readValue(b.getMetaJson(), SceneImageDto.class);
                return Integer.compare(dtoA.getIndex(), dtoB.getIndex());
            } catch (Exception e) {
                return 0;
            }
        });
        
        Asset targetAsset = null;
        SceneImageDto targetDto = null;
        for (Asset asset : sortedAssets) {
            try {
                SceneImageDto dto = objectMapper.readValue(asset.getMetaJson(), SceneImageDto.class);
                if (dto.getIndex() == index) {
                    targetAsset = asset;
                    targetDto = dto;
                    break;
                }
            } catch (Exception e) {
                // ignore
            }
        }
        
        if (targetAsset == null) {
            throw new IllegalArgumentException("Cannot find scene to split: index=" + index);
        }
        
        // [BUG FIX] Shift indices of all assets with index > targetIndex by 1.
        // MUST iterate in DESCENDING order so that e.g. index=3 becomes 4 BEFORE
        // index=2 becomes 3; otherwise the former index=3 and the newly-shifted
        // former index=2 both land on 3 at the same time, creating a duplicate.
        java.util.List<Asset> shiftTargets = new java.util.ArrayList<>();
        for (Asset asset : sortedAssets) {
            try {
                SceneImageDto dto = objectMapper.readValue(asset.getMetaJson(), SceneImageDto.class);
                if (dto.getIndex() != null && dto.getIndex() > index) {
                    shiftTargets.add(asset);
                }
            } catch (Exception e) {
                // ignore
            }
        }
        // Reverse so we shift highest indices first, avoiding transient duplicates.
        java.util.Collections.sort(shiftTargets, (a, b) -> {
            try {
                SceneImageDto dtoA = objectMapper.readValue(a.getMetaJson(), SceneImageDto.class);
                SceneImageDto dtoB = objectMapper.readValue(b.getMetaJson(), SceneImageDto.class);
                return Integer.compare(dtoB.getIndex(), dtoA.getIndex()); // descending
            } catch (Exception e) { return 0; }
        });
        for (Asset asset : shiftTargets) {
            try {
                SceneImageDto dto = objectMapper.readValue(asset.getMetaJson(), SceneImageDto.class);
                dto.setIndex(dto.getIndex() + 1);
                asset.setMetaJson(objectMapper.writeValueAsString(dto));
                assetRepository.save(asset);
            } catch (Exception e) {
                log.warn("씬 인덱스 시프트 중 오류: {}", e.getMessage());
            }
        }

        // Update target asset (part 1)
        targetDto.setPrompt(part1);
        targetDto.setText(part1);   // keep text in sync so rebuild() can reconstruct the script
        double origDuration = targetDto.getDuration() != null ? targetDto.getDuration() : 10.0;
        double origStart = targetDto.getStart() != null ? targetDto.getStart() : 0.0;

        targetDto.setDuration(origDuration / 2.0);
        targetAsset.setMetaJson(safeJson(targetDto));
        assetRepository.save(targetAsset);

        // Create new asset (part 2) at index + 1
        SceneImageDto newDto = new SceneImageDto();
        newDto.setIndex(index + 1);
        newDto.setPrompt(part2);
        newDto.setText(part2);      // populate text so rebuild() sees correct narration
        newDto.setSection(targetDto.getSection());
        newDto.setImagePath(targetDto.getImagePath()); // Copy image path to maintain character profile
        newDto.setDuration(origDuration / 2.0);
        newDto.setStart(origStart + (origDuration / 2.0));
        
        Asset newAsset = Asset.builder()
            .jobId(jobId)
            .assetType(AssetType.SCENE_IMAGE)
            .localPath(targetAsset.getLocalPath())
            .metaJson(safeJson(newDto))
            .build();
            
        assetRepository.save(newAsset);
        log.info("씬 분할 완료: jobId={}, index={} → {} & {}", jobId, index, index, index + 1);
    }

    /**
     * Once a scene has an explicit value, the renderer uses only explicitly
     * selected scenes for Kling. The worker still enforces the first-minute cap.
     */
    @Transactional
    public void setSceneKling(Long jobId, int index, boolean enabled) {
        for (Asset asset : assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE)) {
            try {
                SceneImageDto dto = objectMapper.readValue(asset.getMetaJson(), SceneImageDto.class);
                if (dto.getIndex() != null && dto.getIndex() == index) {
                    dto.setUseKling(enabled);
                    asset.setMetaJson(safeJson(dto));
                    assetRepository.save(asset);
                    log.info("Kling scene setting saved: jobId={}, index={}, enabled={}", jobId, index, enabled);
                    return;
                }
            } catch (Exception ignored) {
                // Continue searching assets with malformed legacy metadata.
            }
        }
        throw new IllegalArgumentException("해당 씬 이미지를 찾을 수 없습니다: index=" + index);
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
            return "{}";
        }
    }
}
