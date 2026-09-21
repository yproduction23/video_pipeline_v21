package com.pipeline.video.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.Autonomy;
import com.pipeline.video.domain.Category;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.ScriptGenerateResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ScriptEvidencePreservationTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final ScriptService scriptService = new ScriptService(null, null, null, null, null, null);

    @Test
    void initialScriptAsset_containsNewsCrossCheckStatus() throws Exception {
        VideoJobRepository jobRepository = mock(VideoJobRepository.class);
        AssetRepository assetRepository = mock(AssetRepository.class);
        FastApiClient fastApiClient = mock(FastApiClient.class);
        GateService gateService = mock(GateService.class);
        AutonomyService autonomyService = mock(AutonomyService.class);
        CostService costService = mock(CostService.class);
        ScriptService service = new ScriptService(
                jobRepository, assetRepository, fastApiClient,
                gateService, autonomyService, costService);
        VideoJob job = VideoJob.builder()
                .id(1L)
                .title("테스트")
                .keyword("삼성전자 실적")
                .category(Category.INDIVIDUAL_STOCK)
                .status(JobStatus.SCRIPT_PENDING)
                .autonomy(Autonomy.GUIDED)
                .longformTargetMinutes(5)
                .build();
        ScriptGenerateResponse response = new ScriptGenerateResponse();
        response.setScript("검증된 대본");
        response.setSections(List.of());
        response.setCharCount(7);
        response.setLlmCallCount(3);
        response.setRequiresManualReview(false);
        response.setNewsArticles(List.of(Map.of(
                "title", "테스트 기사",
                "link", "https://yna.co.kr/1",
                "outlet", "연합뉴스"
        )));
        response.setSuspectFacts(List.of(Map.of(
                "fact", "모순 사실",
                "contradiction_detected", true
        )));
        response.setFactCheckSummary(Map.of(
                "total", 2,
                "cross_verified", 1,
                "contradicted", 1
        ));
        response.setNewsCrossCheckStatus("finance_outlet_articles_found");
        response.setSourceRef(List.of("한국경제"));
        response.setSourceVideos(List.of(Map.of("video_id", "video-1")));
        when(jobRepository.findById(1L)).thenReturn(Optional.of(job));
        when(assetRepository.findByJobIdAndAssetType(1L, AssetType.KEYWORD)).thenReturn(List.of());
        when(fastApiClient.generateScript(
                eq(1L), eq("삼성전자 실적"), eq(5), eq("INDIVIDUAL_STOCK"),
                isNull(), eq(false), isNull(), eq("GUIDED"), isNull(), isNull()
        )).thenReturn(response);
        when(autonomyService.isAuto(job)).thenReturn(false);

        service.generate(1L, "SONG");

        ArgumentCaptor<Asset> assetCaptor = ArgumentCaptor.forClass(Asset.class);
        verify(assetRepository).save(assetCaptor.capture());

        Map<String, Object> stored = objectMapper.readValue(
                assetCaptor.getValue().getMetaJson(), new TypeReference<>() {});

        assertThat(assetCaptor.getValue().getAssetType()).isEqualTo(AssetType.SCRIPT);
        assertThat(stored.get("news_articles")).isEqualTo(List.of(Map.of(
                "title", "테스트 기사",
                "link", "https://yna.co.kr/1",
                "outlet", "연합뉴스"
        )));
        assertThat(stored.get("suspect_facts")).isEqualTo(List.of(Map.of(
                "fact", "모순 사실",
                "contradiction_detected", true
        )));
        assertThat(stored.get("fact_check_summary")).isEqualTo(Map.of(
                "total", 2,
                "cross_verified", 1,
                "contradicted", 1
        ));
        assertThat(stored.get("news_cross_check_status")).isEqualTo("finance_outlet_articles_found");
        assertThat(stored.get("source_ref")).isEqualTo(List.of("한국경제"));
        assertThat(stored.get("source_videos")).isEqualTo(List.of(Map.of("video_id", "video-1")));
    }

    @Test
    void scriptResponse_deserializesNewsArticles() throws Exception {
        String json = """
                {"verified_facts": [], "news_articles": [
                    {"title": "테스트 기사", "link": "https://yna.co.kr/1", "outlet": "연합뉴스"}
                ], "suspect_facts": [], "fact_check_summary": {"total": 0}}
                """;

        ScriptGenerateResponse dto = objectMapper.readValue(json, ScriptGenerateResponse.class);

        assertThat(dto.getNewsArticles()).hasSize(1);
        assertThat(dto.getNewsArticles().get(0)).containsEntry("link", "https://yna.co.kr/1");
    }

    @Test
    void scriptResponse_deserializesSuspectFacts() throws Exception {
        String json = """
                {"verified_facts": [], "suspect_facts": [
                    {"fact": "모순 사실", "contradiction_detected": true}
                ], "fact_check_summary": {}}
                """;

        ScriptGenerateResponse dto = objectMapper.readValue(json, ScriptGenerateResponse.class);

        assertThat(dto.getSuspectFacts()).hasSize(1);
        assertThat(dto.getSuspectFacts().get(0)).containsEntry("contradiction_detected", true);
    }

    @Test
    void scriptResponse_deserializesFactCheckSummary() throws Exception {
        String json = """
                {"fact_check_summary": {
                    "total": 5, "cross_verified": 3, "single_source": 1, "contradicted": 1
                }}
                """;

        ScriptGenerateResponse dto = objectMapper.readValue(json, ScriptGenerateResponse.class);

        assertThat(dto.getFactCheckSummary()).containsEntry("total", 5);
    }

    @Test
    void scriptResponse_preservesNarrationContractForLaterCaptionImageSync() throws Exception {
        String json = """
                {"narration_contract": {
                    "version": "narration-source-v1",
                    "canonical_text_sha256": "abc123",
                    "section_count": 2
                }}
                """;

        ScriptGenerateResponse dto = objectMapper.readValue(json, ScriptGenerateResponse.class);

        assertThat(dto.getNarrationContract())
                .containsEntry("version", "narration-source-v1")
                .containsEntry("section_count", 2);
    }

    @Test
    void auditFields_includeNewsArticlesSuspectFactsAndFactCheckSummary() {
        assertThat(ScriptService.SCRIPT_AUDIT_FIELDS)
                .contains(
                        "news_articles",
                        "suspect_facts",
                        "fact_check_summary",
                        "flow_qa",
                        "requires_manual_review",
                        "narration_contract");
    }

    @Test
    void confirmedAndReassembledScriptAssets_preserveNarrationAndQualityContracts() {
        Map<String, Object> narrationContract = Map.of(
                "version", "narration-source-v1",
                "canonical_text_sha256", "abc123",
                "section_count", 49
        );
        Map<String, Object> flowQa = Map.of("passed", true);
        Map<String, Object> previous = Map.of(
                "narration_contract", narrationContract,
                "flow_qa", flowQa,
                "requires_manual_review", false,
                "keyword_validation", Map.of("passed", true),
                "unit_validation", Map.of("passed", true)
        );

        Map<String, Object> confirmed = scriptService.buildConfirmedScriptMetadata(
                "확정 대본", List.of(), List.of(), Map.of("passed", true), previous);
        Map<String, Object> rebuilt = LongformService.buildReassembledScriptMetadata(
                "재조립 대본", List.of(), confirmed);

        assertThat(confirmed.get("narration_contract")).isEqualTo(narrationContract);
        assertThat(confirmed.get("flow_qa")).isEqualTo(flowQa);
        assertThat(confirmed.get("requires_manual_review")).isEqualTo(false);
        assertThat(rebuilt.get("narration_contract")).isEqualTo(narrationContract);
        assertThat(rebuilt.get("flow_qa")).isEqualTo(flowQa);
        assertThat(rebuilt.get("requires_manual_review")).isEqualTo(false);
    }

    @Test
    void confirmedAndReassembledScriptAssets_preserveNewsAuditLineage() {
        List<Map<String, Object>> newsArticles = List.of(Map.of(
                "title", "테스트 기사",
                "link", "https://yna.co.kr/1",
                "outlet", "연합뉴스"
        ));
        List<Map<String, Object>> suspectFacts = List.of(Map.of(
                "fact", "모순 사실",
                "contradiction_detected", true
        ));
        Map<String, Object> factCheckSummary = Map.of(
                "total", 2,
                "cross_verified", 1,
                "contradicted", 1
        );
        Map<String, Object> previous = Map.of(
                "news_articles", newsArticles,
                "suspect_facts", suspectFacts,
                "fact_check_summary", factCheckSummary
        );

        Map<String, Object> confirmed = scriptService.buildConfirmedScriptMetadata(
                "확정 대본", List.of(), List.of(), Map.of("passed", true), previous);
        Map<String, Object> rebuilt = LongformService.buildReassembledScriptMetadata(
                "재조립 대본", List.of(), confirmed);

        assertThat(confirmed.get("news_articles")).isEqualTo(newsArticles);
        assertThat(confirmed.get("suspect_facts")).isEqualTo(suspectFacts);
        assertThat(confirmed.get("fact_check_summary")).isEqualTo(factCheckSummary);
        assertThat(rebuilt.get("news_articles")).isEqualTo(newsArticles);
        assertThat(rebuilt.get("suspect_facts")).isEqualTo(suspectFacts);
        assertThat(rebuilt.get("fact_check_summary")).isEqualTo(factCheckSummary);
    }

    @Test
    void confirmedScriptAsset_preservesVerifiedFacts() {
        List<Map<String, Object>> facts = List.of(Map.of(
                "fact", "검증된 실적",
                "source_ref", List.of("DART", "한국경제")
        ));
        Map<String, Object> previous = Map.of("verified_facts", facts);

        Map<String, Object> confirmed = scriptService.buildConfirmedScriptMetadata(
                "확정 대본", List.of(), facts, Map.of("passed", true), previous);

        assertThat(confirmed.get("verified_facts")).isEqualTo(facts);
    }

    @Test
    void confirmedScriptAsset_preservesNewsCrossCheckStatus() {
        Map<String, Object> previous = Map.of(
                "news_cross_check_status", "finance_outlet_articles_found",
                "used_real_llm", true,
                "source_ref", List.of("한국경제"),
                "source_videos", List.of(Map.of("video_id", "video-1"))
        );

        Map<String, Object> confirmed = scriptService.buildConfirmedScriptMetadata(
                "확정 대본", List.of(), List.of(), Map.of("passed", true), previous);

        assertThat(confirmed.get("news_cross_check_status")).isEqualTo("finance_outlet_articles_found");
        assertThat(confirmed.get("used_real_llm")).isEqualTo(true);
        assertThat(confirmed.get("source_ref")).isEqualTo(List.of("한국경제"));
        assertThat(confirmed.get("source_videos")).isEqualTo(List.of(Map.of("video_id", "video-1")));
    }

    @Test
    void longformReassembly_doesNotWipeVerifiedFacts() {
        List<Map<String, Object>> facts = List.of(Map.of("fact", "유지할 검증 사실"));
        Map<String, Object> existing = Map.of(
                "verified_facts", facts,
                "news_cross_check_status", "finance_outlet_articles_found"
        );

        Map<String, Object> rebuilt = LongformService.buildReassembledScriptMetadata(
                "재조립 대본", List.of(), existing);

        assertThat(rebuilt.get("verified_facts")).isEqualTo(facts);
        assertThat(rebuilt.get("news_cross_check_status")).isEqualTo("finance_outlet_articles_found");
    }
}
