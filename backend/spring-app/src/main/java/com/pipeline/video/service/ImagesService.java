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
    private final OperatorAlertService operatorAlertService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Transactional(noRollbackFor = {
            ImageProviderRetryRequiredException.class,
            ImageProviderTemporarilyUnavailableException.class
    })
    public ImagesGenerateResponse generate(Long jobId, String username) {
        return generate(jobId, username, null);
    }

    /**
     * 2026-10-16 운영자 요청: 씬별 생성 전에 편집 계획서만 먼저 만든다. 이미지·영상은 만들지 않는다.
     * 슬라이드형 작업에서만 허용하며, TTS가 확정된 이후여야 한다(타임라인이 필요하다).
     */
    @Transactional(readOnly = true)
    public ImagesGenerateResponse buildEditPlan(Long jobId) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        if (job.getVisualMode() != VisualMode.SLIDE) {
            throw new IllegalStateException("편집 계획서는 슬라이드형 작업에서만 만들 수 있습니다.");
        }
        JobStatus status = job.getStatus();
        if (status == JobStatus.DRAFT || status == JobStatus.KEYWORD_PENDING
                || status == JobStatus.SCRIPT_PENDING || status == JobStatus.TTS_PENDING) {
            throw new IllegalStateException("TTS 확정 전에는 편집 계획서를 만들 수 없습니다. 현재: " + status);
        }
        log.info("편집 계획서 생성 시작(이미지 생성 없음): jobId={}", jobId);
        return fastApiClient.planSlideEdit(
                jobId, loadAssetMeta(jobId, AssetType.TTS_AUDIO), loadAssetMeta(jobId, AssetType.SCRIPT));
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
        return generate(jobId, username, sceneIndices, false);
    }

    /** klingUpgrade가 true면 선택한 장면의 Gemini 정지 이미지를 Kling 클립으로 바꾼다(슬라이드형 후작업). */
    @Transactional(noRollbackFor = {
            ImageProviderRetryRequiredException.class,
            ImageProviderTemporarilyUnavailableException.class
    })
    public ImagesGenerateResponse generate(Long jobId, String username, List<Integer> sceneIndices, boolean klingUpgrade) {
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

        // 슬라이드형: 수동·가이드 모드는 편집 계획서를 확인한 뒤에만 이미지를 만든다(비용이 나가는 단계).
        // AUTO는 자동화 목적상 워커가 계획서를 만들고 이어서 생성한다. 일부 장면 재생성은 기존 계획서를 쓴다.
        if (klingUpgrade && (job.getVisualMode() != VisualMode.SLIDE || sceneIndices == null || sceneIndices.isEmpty())) {
            throw new IllegalStateException("Kling 후작업은 슬라이드형 작업에서 바꿀 장면을 골라야 실행할 수 있습니다.");
        }
        if (job.getVisualMode() == VisualMode.SLIDE && !autonomyService.isAuto(job)
                && (sceneIndices == null || sceneIndices.isEmpty())) {
            Map<String, Object> planResponse = fastApiClient.getSlideEditPlan(jobId);
            if (planResponse == null || planResponse.get("plan") == null) {
                throw new IllegalStateException(
                        "편집 계획서를 먼저 만들어 주세요. 계획서를 확인한 뒤 씬별 생성을 눌러야 이미지 비용이 나갑니다.");
            }
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
        // 2026-10-12 job 14 재현: 여기서 costService.getTotal()(Spring+Worker
        // 합산)을 쓰면, FastAPI가 자기 장부(Worker 누적)를 다시 더해 비교하므로
        // 같은 금액이 이중으로 빠져 Worker 누적이 전체 예산의 절반을 넘는
        // 순간부터 재시도가 구조적으로 불가능해진다. Worker 쪽 누적 비용은
        // FastAPI 자신의 장부가 단독으로 추적하므로, 여기서는 Spring이 직접
        // 추적하는 비용(TTS/Claude 등)만 뺀다.
        BigDecimal remainingBudget = null;
        if (job.getBudgetCap() != null) {
            BigDecimal spent = costService.getSpringOnlyTotal(jobId);
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
            String visualModeName = job.getVisualMode() == null ? null : job.getVisualMode().name();
            String autonomyName = job.getAutonomy() == null ? null : job.getAutonomy().name();
            result = klingUpgrade
                    ? fastApiClient.generateImages(
                            jobId, ttsMetaJson, scriptMetaJson, character.imagePath(), character.stylePrompt(), character.posesDir(),
                            character.loraModelId(), character.loraTriggerWord(), character.loraScale(), autonomyName,
                            remainingBudget, PricingConfig.GEMINI_IMAGE_ONLY_POLICY_VERSION, sceneIndices, visualModeName, true)
                    : fastApiClient.generateImages(
                            jobId, ttsMetaJson, scriptMetaJson, character.imagePath(), character.stylePrompt(), character.posesDir(),
                            character.loraModelId(), character.loraTriggerWord(), character.loraScale(), autonomyName,
                            remainingBudget, PricingConfig.GEMINI_IMAGE_ONLY_POLICY_VERSION, sceneIndices, visualModeName);
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
                operatorAlertService.notifyJobNeedsAttention(jobId, job.getTitle(), "이미지 공급자 크레딧/쿼터 부족");
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

        persistGenerateResult(jobId, job, result);
        return result;
    }

    /**
     * SCENE_IMAGE/GIF_CLIP 자산 저장, IMAGE_QC_REPORT 갱신, AUTO 모드 자동 확정을
     * 하나의 공통 경로로 묶는다. generate()의 배치 재시도와 acceptHeldScene()의
     * 단일 보류 장면 수동 승인이 서로 다른 코드로 각자 이 로직을 반복하면, 한쪽만
     * 고쳤을 때 다른 쪽이 조용히 어긋난다(2026-10-13 "검토 이미지 그대로 사용"
     * 기능 추가 시 발견).
     */
    /** SCENE_IMAGE 자산 메타의 장면 번호. 읽을 수 없으면 null(교체 대상에서 제외). */
    private Integer sceneIndexOf(Asset asset) {
        try {
            if (asset.getMetaJson() == null || asset.getMetaJson().isBlank()) return null;
            com.fasterxml.jackson.databind.JsonNode node = objectMapper.readTree(asset.getMetaJson()).get("index");
            return node != null && node.canConvertToInt() ? node.asInt() : null;
        } catch (Exception e) {
            return null;
        }
    }

    private void persistGenerateResult(Long jobId, VideoJob job, ImagesGenerateResponse result) {
        if (result.getScenes() != null) {
            for (SceneImageDto scene : result.getScenes()) {
                // 슬라이드형은 한 장면의 생성이 실패하면 image_path 없이 오류만 담아 돌려준다
                // (배치 전체를 막지 않기 위함). 그런 장면은 경로 중복 판정을 건너뛰고 그대로
                // 저장해 검수·재생성 대상으로 남긴다 — 여기서 NPE가 나면 나머지 장면 결과까지
                // 저장되지 못한다.
                String scenePath = scene.getImagePath();
                List<Asset> existingScenes = assetRepository.findByJobIdAndAssetType(jobId, AssetType.SCENE_IMAGE);
                boolean alreadyRegistered = scenePath != null
                        && existingScenes.stream().anyMatch(existing -> scenePath.equals(existing.getLocalPath()));
                // 2026-10-16 20번 Job 재현: 실패했던 장면(경로 없음)을 다시 생성하면 새 자산이 추가만
                // 되어 같은 장면 번호가 두 개로 남았다. 같은 장면 번호의 이전 자산은 새 결과로 교체한다.
                // 새 결과가 또 실패(경로 없음)면 좋은 이전 이미지를 지키고, 실패 자리표시자는 하나만 둔다.
                if (scene.getIndex() != null) {
                    for (Asset existing : existingScenes) {
                        if (!scene.getIndex().equals(sceneIndexOf(existing))) continue;
                        boolean samePath = scenePath != null && scenePath.equals(existing.getLocalPath());
                        if (samePath) continue;
                        if (scenePath != null) {
                            assetRepository.delete(existing);
                        } else if (existing.getLocalPath() == null) {
                            alreadyRegistered = true;
                        } else {
                            alreadyRegistered = true; // 좋은 이전 이미지가 있으면 실패 결과로 덮지 않는다
                        }
                    }
                }
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
        PhonePushNotifier.stageCompleted(jobId, job.getTitle(), "씬별 생성 완료",
                String.format("%d장면%s", result.getSceneCount(),
                        result.isRequiresManualReview() ? " · 검토 필요 장면 있음" : " · 확인 후 확정"));

        if (autonomyService.isAuto(job)) {
            if (result.isRequiresManualReview()) {
                // AUTO 모드라도 이미지 검수 실패 시 자동 확정 차단 — MANUAL/GUIDED와 동일 품질 게이트 적용
                // "no silent quality-degrading fallbacks" 원칙 준수 (AGENTS.md)
                job.setStatus(JobStatus.IMAGES_RETRY_REQUIRED);
                jobRepository.save(job);
                log.warn("AUTO 모드 — 이미지 검수 실패로 자동 확정 차단, 수동 검토 대기: jobId={}, reasons={}",
                        jobId, result.getReviewReasons());
                List<String> reasons = result.getReviewReasons();
                operatorAlertService.notifyJobNeedsAttention(
                        jobId, job.getTitle(),
                        "AUTO 모드 이미지 검수 실패: " + (reasons == null || reasons.isEmpty() ? "사유 미기록" : String.join(", ", reasons)));
                // confirm() 호출하지 않음 — 사람이 직접 UI에서 검수 후 확정 필요
            } else {
                log.info("AUTO 모드 — 이미지 자동 확정");
                confirm(jobId, "AUTO");
            }
        } else if (result.isRequiresManualReview()) {
            log.info("OCR 추정 사실 포함 등 수동 검토 필요: jobId={}, reasons={}",
                    jobId, result.getReviewReasons());
        }
    }

    /**
     * 2026-10-13 사용자 요청: 자동 게이트가 보류시킨 장면이라도 사용자가 실물
     * 후보 이미지를 직접 보고 괜찮다고 판단하면, 재생성 없이 그대로 최종
     * 이미지로 승격한다. FastAPI는 공급자를 다시 호출하지 않으므로 예산·요청
     * 상한 게이트와 무관하다.
     */
    @Transactional
    public ImagesGenerateResponse acceptHeldScene(Long jobId, int index, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        SceneNarration narration = resolveSceneNarration(jobId, index);
        ImagesGenerateResponse result = fastApiClient.acceptHeldScene(
                jobId, index, username, narration.text(), narration.section());
        persistGenerateResult(jobId, job, result);
        log.info("보류 장면 수동 승인 완료: jobId={}, scene={}, by={}", jobId, index, username);
        return result;
    }

    /**
     * 2026-10-14 사용자 재현(job 18 scene 11): 콘텐츠 거부 반복으로
     * needs_review에 들어간 씬은 재시도 상한을 아무리 올려도 풀리지 않았다
     * (기존 재도전 승인 경로는 단일 503 전용이라 콘텐츠 거부 케이스에
     * 호출되지 않았음). 이 메서드는 사람이 명시적으로 승인한 경우에만
     * 요청 상한 카운터를 연다 — 실제 이미지 재생성은 별도의 일반
     * generate() 호출로 이어서 진행한다.
     */
    public Map<String, Object> approveSceneRetry(Long jobId, int index, String username, boolean overrideCooldown) {
        Map<String, Object> result = fastApiClient.approveSceneRetry(jobId, index, username, overrideCooldown);
        log.info("씬 재도전 승인 완료: jobId={}, scene={}, by={}, overrideCooldown={}",
                jobId, index, username, overrideCooldown);
        return result;
    }

    private record SceneNarration(String text, String section) {}

    /**
     * 2026-10-13 job 14 씬5 재현: 승인 이미지만 저장하고 내레이션 원문을 함께
     * 보내지 않으면 SCENE_IMAGE 자산의 text가 비어, longform 조립 직전 "대본
     * 원문과 장면 내레이션 불일치"로 전체 조립이 막힌다. 승인 SCRIPT 자산에서
     * 해당 인덱스의 원문을 직접 가져와 FastAPI로 함께 보낸다.
     */
    @SuppressWarnings("unchecked")
    private SceneNarration resolveSceneNarration(Long jobId, int index) {
        String scriptMetaJson = loadAssetMeta(jobId, AssetType.SCRIPT);
        try {
            Map<String, Object> scriptMeta = objectMapper.readValue(scriptMetaJson, Map.class);
            List<Map<String, Object>> sections = (List<Map<String, Object>>) scriptMeta.get("sections");
            if (sections == null || index < 0 || index >= sections.size()) {
                throw new IllegalStateException("승인 대본에서 scene " + index + "의 내레이션을 찾을 수 없습니다.");
            }
            Map<String, Object> section = sections.get(index);
            String text = (String) section.get("text_for_tts");
            if (text == null || text.isBlank()) text = (String) section.get("content");
            if (text == null || text.isBlank()) text = (String) section.get("text");
            if (text == null || text.isBlank()) {
                throw new IllegalStateException("승인 대본의 scene " + index + " 내레이션이 비어 있습니다.");
            }
            return new SceneNarration(text, (String) section.get("section"));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("승인 대본 파싱 실패: " + e.getMessage(), e);
        }
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
            operatorAlertService.notifyJobNeedsAttention(jobId, job.getTitle(), "Gemini Pro 배치 영구 실패: " + reason);
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
