package com.pipeline.video.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.*;
import com.pipeline.video.dto.ScriptGenerateResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.List;

@Service
@Slf4j
@RequiredArgsConstructor
public class ScriptService {

    static final List<String> SCRIPT_AUDIT_FIELDS = List.of(
            "verified_facts",
            "suspect_facts",
            "fact_check_summary",
            "fact_check_log",
            "fact_check_rounds",
            "news_articles",
            "news_cross_check_status",
            "used_real_llm",
            "requires_manual_review",
            "source_ref",
            "source_videos",
            "flow_qa",
            "keyword_validation",
            "unit_validation",
            "length_contract",
            "quality_report",
            "narrative_plan",
            "narration_contract",
            "synopsis",
            "market_snapshot",
            "market_snapshot_used",
            "llm_call_count",
            "llm_provider_log",
            "job_id",
            "keyword",
            "estimated_minutes"
    );

    private final VideoJobRepository jobRepository;
    private final AssetRepository assetRepository;
    private final FastApiClient fastApiClient;
    private final GateService gateService;
    private final AutonomyService autonomyService;
    private final CostService costService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Transactional
    public ScriptGenerateResponse generate(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING) {
            throw new IllegalStateException("키워드 확정 전에는 스크립트를 생성할 수 없습니다. 현재: " + job.getStatus());
        }
        if (job.getKeyword() == null || job.getKeyword().isBlank()) {
            throw new IllegalStateException("키워드가 선택되지 않음.");
        }

        int targetMinutes = job.getLongformTargetMinutes() != null
                ? job.getLongformTargetMinutes() : 20;
        // The worker already calculates the narration budget from the selected
        // ElevenLabs speed. Passing an inflated duration here caused a 5-minute
        // job to ask the LLM for six minutes of speech.
        int llmTargetMinutes = targetMinutes;
        String categoryName = job.getCategory() != null ? job.getCategory().name() : "CUSTOM";

        log.info("스크립트 생성: jobId={}, keyword={}, target={}분 (LLM 타겟: {}분), category={}",
                jobId, job.getKeyword(), targetMinutes, llmTargetMinutes, categoryName);

        String marketSnapshotJson = null;
        Map<String, Object> candidateEvidence = null;
        try {
            // 해당 jobId의 KEYWORD 에셋 조회
            java.util.List<Asset> keywordAssets = assetRepository.findByJobIdAndAssetType(jobId, AssetType.KEYWORD);
            for (Asset a : keywordAssets) {
                if (a.getMetaJson() == null || a.getMetaJson().isBlank()) {
                    continue;
                }
                Map<String, Object> metaMap = objectMapper.readValue(a.getMetaJson(), Map.class);
                if (marketSnapshotJson == null && metaMap.containsKey("market_snapshot")) {
                    marketSnapshotJson = objectMapper.writeValueAsString(metaMap.get("market_snapshot"));
                    log.info("KEYWORD 에셋에서 market_snapshot 추출 성공");
                }
                if (candidateEvidence == null && metaMap.containsKey("candidates")) {
                    candidateEvidence = extractCandidateEvidence(metaMap.get("candidates"), job.getKeyword());
                    if (candidateEvidence != null) {
                        log.info("KEYWORD 에셋에서 선택 후보 근거 추출 성공: keyword={}", job.getKeyword());
                    }
                }
                if (marketSnapshotJson != null && candidateEvidence != null) {
                    break;
                }
            }
        } catch (Exception ex) {
            log.warn("KEYWORD 에셋에서 시장 데이터·후보 근거 추출 오류: {}", ex.getMessage());
        }

        ScriptGenerateResponse result = fastApiClient.generateScript(
                jobId, job.getKeyword(), llmTargetMinutes, categoryName, marketSnapshotJson,
                job.isDataVisualsEnabled(), job.getTtsVoiceId(), job.getAutonomy().name(),
                candidateEvidence,
                job.getContentNature() != null ? job.getContentNature().name() : null);

        // 실제 Claude 호출 수를 워커가 반환한다. 팩트체크뿐 아니라 내러티브 플랜과
        // 흐름 QA도 비용에 반영해야 영상별 예산 상한을 우회하지 않는다.
        int outputChars = result.getCharCount() != null ? result.getCharCount() : 0;
        int inputChars = marketSnapshotJson != null ? marketSnapshotJson.length() : 500;
        int llmCallCount = result.getLlmCallCount() != null ? result.getLlmCallCount() : 3;
        java.math.BigDecimal claudeCost = CostEstimator.claude(inputChars, outputChars, llmCallCount);
        costService.record(jobId, "CLAUDE_LLM", claudeCost, "USD",
                String.format("스크립트 목표 %d분 (LLM %d분) %d자, Claude %d회 호출", targetMinutes, llmTargetMinutes,
                        outputChars, llmCallCount));

        Asset asset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.SCRIPT)
                .metaJson(safeJson(result))
                .build();
        assetRepository.save(asset);

        if (autonomyService.isAuto(job)) {
            if (Boolean.TRUE.equals(result.getRequiresManualReview())) {
                log.error(
                        "AUTO 모드: 스크립트 품질 검사 실패 — requires_manual_review=true. " +
                                "자동 확정 차단. Job {}을 SCRIPT_PENDING 상태로 유지합니다.",
                        jobId
                );
                return result;
            }
            log.info("AUTO 모드 — 스크립트 자동 확정");
            confirm(jobId, result.getScript(), result.getSections(), "AUTO");
        } else if (Boolean.TRUE.equals(result.getRequiresManualReview())) {
            log.warn("스크립트 수동 검토 대기: jobId={}, reason=mock-or-provider-fallback", jobId);
        }

        return result;
    }

    /**
     * 선택 키워드와 정확히 일치하는 후보의 근거 필드만 다음 단계로 전달한다.
     * 일치 후보가 없거나 입력 구조가 잘못된 경우 기존 생성 경로를 유지하도록 null을 반환한다.
     */
    Map<String, Object> extractCandidateEvidence(Object candidatesRaw, String jobKeyword) {
        if (!(candidatesRaw instanceof List<?> candidates) || jobKeyword == null) {
            return null;
        }
        String expected = jobKeyword.trim();
        for (Object item : candidates) {
            if (!(item instanceof Map<?, ?> candidate)) {
                continue;
            }
            Object rawKeyword = candidate.get("keyword");
            if (rawKeyword == null || !expected.equalsIgnoreCase(String.valueOf(rawKeyword).trim())) {
                continue;
            }
            Map<String, Object> evidence = new LinkedHashMap<>();
            evidence.put("keyword", rawKeyword);
            evidence.put("news_articles", candidate.get("news_articles"));
            evidence.put("source_videos", candidate.get("source_videos"));
            evidence.put("evidence_video_ids", candidate.get("evidence_video_ids"));
            evidence.put("youtube_score", candidate.get("youtube_score"));
            evidence.put("news_cross_check_status", candidate.get("news_cross_check_status"));
            evidence.put("evidence", candidate.get("evidence"));
            evidence.put("benchmark_analysis", candidate.get("benchmark_analysis"));
            return evidence;
        }
        return null;
    }

    /**
     * Temporal uses this variant so an evidence-validation 422 returns the
     * job to keyword selection instead of failing the entire workflow.
     */
    @Transactional
    public String generateRecoverably(Long jobId, String username) {
        try {
            generate(jobId, username);
            return "OK";
        } catch (ScriptResearchRequiredException evidenceFailure) {
            VideoJob job = jobRepository.findById(jobId)
                    .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
            job.setStatus(JobStatus.TOPIC_EVIDENCE_REQUIRED);
            jobRepository.save(job);
            assetRepository.save(Asset.builder()
                    .jobId(jobId)
                    .assetType(AssetType.KEYWORD)
                    .metaJson(safeJson(Map.of(
                            "script_research_required", true,
                            "error_code", "SCRIPT_RESEARCH_REQUIRED",
                            "message", evidenceFailure.getMessage(),
                            "missing_terms", evidenceFailure.getMissingTerms(),
                            "recoverable", true
                    )))
                    .build());
            log.warn("Script evidence missing; returning to keyword selection: jobId={}, missingTerms={}",
                    jobId, evidenceFailure.getMissingTerms());
            return "RESEARCH_REQUIRED";
        }
    }

    @Transactional
    public void confirm(Long jobId, String finalScript, String username) {
        confirm(jobId, finalScript, null, username);
    }

    /**
     * 대본 문구나 사실을 다시 생성하지 않고 최신 문장·리듬 계약으로 재검증한다.
     * 이전 Claude 의미 검토가 존재하고 나머지 하드 게이트도 통과한 자산만
     * 수동 검토 플래그를 해제한다.
     */
    @Transactional
    @SuppressWarnings("unchecked")
    public Map<String, Object> revalidate(Long jobId, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));
        if (job.getStatus() != JobStatus.SCRIPT_PENDING) {
            throw new IllegalStateException("스크립트 대기 상태에서만 재검증할 수 있습니다. 현재: " + job.getStatus());
        }
        Asset latest = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT)
                .orElseThrow(() -> new IllegalStateException("재검증할 SCRIPT 자산이 없습니다."));
        try {
            Map<String, Object> previous = objectMapper.readValue(latest.getMetaJson(), Map.class);
            String script = String.valueOf(previous.getOrDefault("script", "")).trim();
            if (script.isBlank() || !Boolean.TRUE.equals(previous.get("used_real_llm"))) {
                throw new IllegalStateException("실제 Claude 대본이 아니므로 재검증할 수 없습니다.");
            }
            Map<String, Object> unitValidation = previous.get("unit_validation") instanceof Map<?, ?> value
                    ? (Map<String, Object>) value : Map.of();
            Map<String, Object> keywordValidation = previous.get("keyword_validation") instanceof Map<?, ?> value
                    ? (Map<String, Object>) value : Map.of();
            Map<String, Object> qualityReport = previous.get("quality_report") instanceof Map<?, ?> value
                    ? new LinkedHashMap<>((Map<String, Object>) value) : new LinkedHashMap<>();
            Map<String, Object> screenText = qualityReport.get("screen_text") instanceof Map<?, ?> value
                    ? (Map<String, Object>) value : Map.of();
            if (!Boolean.TRUE.equals(unitValidation.get("passed"))
                    || !Boolean.TRUE.equals(keywordValidation.get("passed"))
                    || !Boolean.TRUE.equals(screenText.get("passed"))) {
                throw new IllegalStateException("숫자 단위·키워드·화면 문구 하드 게이트가 통과되지 않았습니다.");
            }
            Object providerLog = previous.get("llm_provider_log");
            if (providerLog instanceof List<?> entries && entries.stream()
                    .filter(Map.class::isInstance)
                    .map(Map.class::cast)
                    .anyMatch(entry -> Boolean.TRUE.equals(entry.get("fallback")))) {
                throw new IllegalStateException("LLM 폴백 호출이 포함되어 재검증만으로 승인할 수 없습니다.");
            }
            Map<String, Object> previousFlow = previous.get("flow_qa") instanceof Map<?, ?> value
                    ? (Map<String, Object>) value : Map.of();
            Map<String, Object> narrativePlan = previous.get("narrative_plan") instanceof Map<?, ?> value
                    ? (Map<String, Object>) value : Map.of();
            Map<String, Object> flow = fastApiClient.revalidateScriptFlow(script, narrativePlan, previousFlow);
            if (!Boolean.TRUE.equals(flow.get("passed"))) {
                throw new IllegalStateException("최신 문장·리듬 계약 재검증 실패: " + flow.get("revision_instruction"));
            }
            Map<String, Object> refreshed = new LinkedHashMap<>(previous);
            refreshed.put("flow_qa", flow);
            qualityReport.put("flow", flow);
            refreshed.put("quality_report", qualityReport);
            refreshed.put("requires_manual_review", false);
            refreshed.put("revalidated_by", username != null ? username : "system");
            refreshed.put("revalidation_reason", "최신 한국어 문장 글자·단어·리듬 계약 적용");
            assetRepository.save(Asset.builder()
                    .jobId(jobId)
                    .assetType(AssetType.SCRIPT)
                    .metaJson(safeJson(refreshed))
                    .build());
            return Map.of(
                    "status", "READY_FOR_APPROVAL",
                    "flow_passed", true,
                    "char_count", previous.getOrDefault("char_count", script.length())
            );
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("SCRIPT 자산 JSON 재검증 실패: " + e.getMessage(), e);
        }
    }

    @Transactional
    public void confirm(Long jobId, String finalScript, List<Map<String, Object>> inputSections, String username) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getStatus() == JobStatus.DRAFT || job.getStatus() == JobStatus.KEYWORD_PENDING) {
            throw new IllegalStateException("키워드 확정 전에는 스크립트를 확정할 수 없습니다. 현재: " + job.getStatus());
        }
        if (finalScript == null || finalScript.isBlank()) {
            throw new IllegalStateException("최종 스크립트가 비어있습니다.");
        }

        // 최종 스크립트 텍스트를 파싱하여 섹션 분리 (사용자 수정 사항 반영)
        List<Map<String, Object>> sections = new java.util.ArrayList<>();
        List<Map<String, Object>> verifiedFacts = List.of();
        Map<String, Object> previousScriptMeta = new LinkedHashMap<>();
        
        try {
            // 기존 에셋에서 검증 사실과 감사 계보를 함께 복원한다.
            java.util.Optional<Asset> prevAssetOpt = assetRepository.findTopByJobIdAndAssetTypeOrderByCreatedAtDesc(jobId, AssetType.SCRIPT);
            if (prevAssetOpt.isPresent()) {
                previousScriptMeta = objectMapper.readValue(prevAssetOpt.get().getMetaJson(), Map.class);
                Object rawFacts = previousScriptMeta.get("verified_facts");
                if (rawFacts instanceof List<?> facts && !facts.isEmpty()) {
                    verifiedFacts = facts.stream()
                            .filter(Map.class::isInstance)
                            .map(item -> (Map<String, Object>) item)
                            .toList();
                }
            }
        } catch (Exception e) {
            log.warn("이전 스크립트 에셋 메타데이터 파싱 실패: {}", e.getMessage());
        }

        if (inputSections != null && !inputSections.isEmpty()) {
            sections = inputSections;
        } else {
            try {
                String[] parts = finalScript.split("(?m)^##\\s*");
                if (parts.length <= 1) {
                    parts = finalScript.split("(?m)^\\s*\\n+");
                }
                for (String part : parts) {
                    part = part.trim();
                    if (part.isEmpty()) continue;
                    
                    int firstNewline = part.indexOf('\n');
                    String title;
                    String rawContent;
                    if (firstNewline != -1) {
                        title = part.substring(0, firstNewline).trim();
                        rawContent = part.substring(firstNewline + 1).trim();
                    } else {
                        title = "섹션";
                        rawContent = part;
                    }
                    
                    // [대사]와 [비주얼] 분리 파싱
                    String narration = "";
                    String prompt = "";
                    
                    int daesaIdx = rawContent.indexOf("[대사]");
                    int visualIdx = rawContent.indexOf("[비주얼]");
                    
                    if (daesaIdx != -1) {
                        if (visualIdx != -1 && visualIdx > daesaIdx) {
                            narration = rawContent.substring(daesaIdx + 4, visualIdx).trim();
                        } else {
                            narration = rawContent.substring(daesaIdx + 4).trim();
                        }
                    } else {
                        if (visualIdx != -1) {
                            narration = rawContent.substring(0, visualIdx).trim();
                        } else {
                            narration = rawContent;
                        }
                    }

                    narration = cleanScriptCommasAndPct(narration);
                    
                    if (visualIdx != -1) {
                        prompt = rawContent.substring(visualIdx + 5).trim();
                    } else {
                        // [버그 수정] 여기 하드코딩된 4번째 캐릭터 설명 사본을 제거합니다.
                        // 파이썬 워커 쪽 script_worker._generate_visual_prompt()가 이미
                        // 씬 텍스트 기반의 정확한 프롬프트를 만들어주므로, 여기서는 그것에
                        // 위임하는 게 맞습니다. 스크립트 수정 → 재저장 경로에서만 이 분기가
                        // 타는데, 그때는 씬 텍스트만 넘겨주고 실제 프롬프트 생성은 이미지
                        // 재생성 시 FastAPI 쪽에서 다시 만들어집니다.
                        prompt = "";
                    }
                    
                    Map<String, Object> secMap = new java.util.HashMap<>();
                    secMap.put("title", title);
                    secMap.put("text", narration);
                    secMap.put("content", narration);
                    secMap.put("prompt", prompt);
                    secMap.put("char_count", narration.length());
                    
                    // section key 매핑
                    String sectionKey = "background";
                    if (title.contains("인트로") || title.toLowerCase().contains("intro")) {
                        sectionKey = "intro";
                    } else if (title.contains("배경") || title.toLowerCase().contains("background")) {
                        sectionKey = "background";
                    } else if (title.contains("데이터") || title.toLowerCase().contains("data")) {
                        sectionKey = "data";
                    } else if (title.contains("시나리오") || title.toLowerCase().contains("scenario")) {
                        sectionKey = "scenario";
                    } else if (title.contains("가이드") || title.toLowerCase().contains("action") || title.toLowerCase().contains("guide")) {
                        sectionKey = "action";
                    } else if (title.contains("결론") || title.toLowerCase().contains("conclusion")) {
                        sectionKey = "conclusion";
                    }
                    secMap.put("section", sectionKey);
                    
                    sections.add(secMap);
                }
            } catch (Exception parseEx) {
                log.warn("최종 스크립트 섹션 파싱 실패, 이전 에셋 복원 폴백: {}", parseEx.getMessage());
            }
        }

        // Rich scripts are stored for editing, but production needs compact
        // narration-only scenes.  This also removes a channel title or visual
        // prompt accidentally parsed as the first scene.
        sections = normalizeSectionsForProduction(sections);

        if (sections.isEmpty()) {
            // 폴백: 이전 에셋에서 복원
            try {
                Object rawSections = previousScriptMeta.get("sections");
                if (rawSections instanceof List<?> storedSections) {
                    sections = storedSections.stream()
                            .filter(Map.class::isInstance)
                            .map(item -> (Map<String, Object>) item)
                            .toList();
                }
            } catch (Exception e) {
                log.warn("이전 스크립트 에셋 복원 실패: {}", e.getMessage());
            }
        }

        // 스크립트 UI 노출용 마크다운 형식 재구성 (작업자에게는 깨끗한 한국어 대사만 제공)
        String scriptToSave = finalScript;
        if (!sections.isEmpty() && (finalScript == null || !finalScript.contains("##"))) {
            StringBuilder sb = new StringBuilder();
            for (Map<String, Object> sec : sections) {
                sb.append("## ").append(sec.get("title")).append("\n");
                String content = sec.get("content") != null ? sec.get("content").toString() : (sec.get("text") != null ? sec.get("text").toString() : "");
                sb.append(content).append("\n\n");
            }
            scriptToSave = sb.toString().trim();
        }

        // 수동 편집 후에도 생성 경로의 하드 게이트를 우회하지 못하도록, 확정 직전에
        // 정제된 내레이션과 검증 사실을 워커에 다시 전달한다. 비활성화 상태에서는
        // 워커가 통과 결과를 돌려 기존 작업 흐름을 보존한다.
        StringBuilder narrationForGate = new StringBuilder();
        for (Map<String, Object> section : sections) {
            Object raw = section.get("content") != null ? section.get("content") : section.get("text");
            if (raw == null || raw.toString().isBlank()) continue;
            if (narrationForGate.length() > 0) narrationForGate.append(' ');
            narrationForGate.append(raw.toString());
        }
        String formatName = job.getLongformTargetMinutes() != null && job.getLongformTargetMinutes() <= 1
                ? "shorts" : "longform";
        Map<String, Object> houseStyleGate = fastApiClient.assessScriptHouseStyle(
                narrationForGate.length() > 0 ? narrationForGate.toString() : scriptToSave,
                formatName,
                verifiedFacts
        );
        if (!Boolean.TRUE.equals(houseStyleGate.get("passed"))) {
            Object failures = houseStyleGate.getOrDefault("hard_failures", List.of());
            throw new IllegalStateException("스크립트 하우스 스타일 하드 게이트 실패: " + failures);
        }

        Asset finalAsset = Asset.builder()
                .jobId(jobId)
                .assetType(AssetType.SCRIPT)
                .metaJson(safeJson(buildConfirmedScriptMetadata(
                        scriptToSave, sections, verifiedFacts, houseStyleGate, previousScriptMeta)))
                .build();
        assetRepository.save(finalAsset);

        if (job.getStatus() == JobStatus.SCRIPT_PENDING) {
            gateService.approve(jobId, GateName.SCRIPT, username, "스크립트 확정");
        } else {
            log.info("스크립트 수정/재확정 완료 (상태 유지: {}): jobId={}", job.getStatus(), jobId);
        }
        log.info("스크립트 확정: jobId={}, length={}자, sections={}개", jobId, scriptToSave.length(), sections.size());
    }

    Map<String, Object> buildConfirmedScriptMetadata(
            String script,
            List<Map<String, Object>> sections,
            List<Map<String, Object>> verifiedFacts,
            Map<String, Object> houseStyleGate,
            Map<String, Object> previousScriptMeta) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("script", script);
        metadata.put("final", true);
        metadata.put("char_count", script.length());
        metadata.put("sections", sections);
        metadata.put("house_style_gate", houseStyleGate);
        copyNonEmptyAuditFields(previousScriptMeta, metadata);
        metadata.putIfAbsent("verified_facts", verifiedFacts != null ? verifiedFacts : List.of());
        return metadata;
    }

    static void copyNonEmptyAuditFields(Map<String, Object> source, Map<String, Object> target) {
        if (source == null || target == null) {
            return;
        }
        for (String field : SCRIPT_AUDIT_FIELDS) {
            Object value = source.get(field);
            if (value == null || value instanceof String text && text.isBlank()) {
                continue;
            }
            if (value instanceof List<?> list && list.isEmpty()) {
                continue;
            }
            target.putIfAbsent(field, value);
        }
    }

    private List<Map<String, Object>> normalizeSectionsForProduction(List<Map<String, Object>> sourceSections) {
        List<Map<String, Object>> normalized = new java.util.ArrayList<>();
        final int maxNarrationChars = 78;
        for (Map<String, Object> source : sourceSections) {
            String raw = source.get("content") != null ? source.get("content").toString()
                    : (source.get("text") != null ? source.get("text").toString() : "");
            String narration = extractNarrationOnly(raw);
            if (narration.isBlank()) continue;

            List<String> parts = splitNarrationForVisualPacing(narration, maxNarrationChars);
            int partNumber = 0;
            for (String part : parts) {
                if (part.isBlank()) continue;
                Map<String, Object> scene = new java.util.LinkedHashMap<>(source);
                String title = String.valueOf(source.getOrDefault("title", "Scene"));
                scene.put("title", parts.size() > 1 ? title + " · " + (++partNumber) : title);
                scene.put("content", part);
                scene.put("text", part);
                scene.put("char_count", part.length());
                // The FastAPI scene director creates the visual brief from this
                // exact text.  Do not pass a stale prompt from the parent scene.
                scene.put("prompt", "");
                normalized.add(scene);
            }
        }

        String[] flow = {"intro", "background", "data", "scenario", "action", "conclusion"};
        for (int index = 0; index < normalized.size(); index++) {
            int bucket = normalized.size() <= 1 ? 0
                    : Math.min((index * flow.length) / normalized.size(), flow.length - 1);
            normalized.get(index).put("section", flow[bucket]);
        }
        return normalized;
    }

    private String extractNarrationOnly(String raw) {
        if (raw == null || raw.isBlank()) return "";
        String dialogueTag = "[\uB300\uC0AC]";
        int dialogue = raw.indexOf(dialogueTag);
        if (dialogue >= 0) {
            raw = raw.substring(dialogue + dialogueTag.length());
            int end = raw.length();
            for (String marker : List.of("[\uBE44\uC8FC\uC5BC", "[\uC774\uBBF8\uC9C0", "[\uD504\uB86C\uD504\uD2B8", "[\uAC10\uC815]")) {
                int markerIndex = raw.indexOf(marker);
                if (markerIndex >= 0) end = Math.min(end, markerIndex);
            }
            raw = raw.substring(0, end);
        }
        StringBuilder spoken = new StringBuilder();
        for (String line : raw.replace("\r", "").split("\n")) {
            String value = line.trim();
            if (value.isEmpty() || value.startsWith("#") || value.matches("[-\\-─—]{3,}")) continue;
            if (value.matches("^(?:\uC8FC\uC81C|\uC50C\\s*\\d+|scene\\s*\\d+)\\s*[:：].*")) continue;
            if (value.startsWith("[")) break;
            if (spoken.length() > 0) spoken.append(' ');
            spoken.append(value);
        }
        return cleanScriptCommasAndPct(spoken.toString().replaceAll("\\s+", " ").trim());
    }

    private List<String> splitNarrationForVisualPacing(String narration, int maxChars) {
        List<String> output = new java.util.ArrayList<>();
        StringBuilder current = new StringBuilder();
        for (String sentence : narration.split("(?<=[.!?])\\s+")) {
            for (String word : sentence.trim().split("\\s+")) {
                if (word.isBlank()) continue;
                String candidate = current.length() == 0 ? word : current + " " + word;
                if (current.length() > 0 && candidate.replace(" ", "").length() > maxChars) {
                    output.add(current.toString());
                    current.setLength(0);
                    current.append(word);
                } else {
                    if (current.length() > 0) current.append(' ');
                    current.append(word);
                }
            }
        }
        if (current.length() > 0) output.add(current.toString());
        return output;
    }

    private String cleanScriptCommasAndPct(String text) {
        if (text == null) return "";
        return text;
    }

    private String safeJson(Object obj) {
        try {
            return objectMapper.writeValueAsString(obj);
        } catch (JsonProcessingException e) {
            return "{}";
        }
    }
}
