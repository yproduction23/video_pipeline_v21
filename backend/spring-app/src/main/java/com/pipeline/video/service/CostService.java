package com.pipeline.video.service;

import com.pipeline.video.domain.CostLedger;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.CostEstimateDto;
import com.pipeline.video.config.PricingConfig;
import com.pipeline.video.exception.BudgetExceededException;
import com.pipeline.video.repository.CostLedgerRepository;
import com.pipeline.video.repository.VideoJobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 비용 원장 기록 + 누적 + 예산 가드 통합 서비스.
 *
 *  - record(): 워커가 비용을 발생시킨 후 호출. 예산 초과 시 BUDGET_BLOCKED 전이 + 예외.
 *  - precheck(): 워커가 비용을 발생시키기 전에 호출 (선택). 예산 초과 예상이면 즉시 차단.
 *  - getSummaryDto(): Spring 원장(Claude, TTS)과 FastAPI Worker 원장(Gemini, Fal)을
 *                    단일 unified items 및 groupedItems로 합산 집계하여 1원 오차 없이 반환.
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class CostService {

    private final VideoJobRepository jobRepository;
    private final CostLedgerRepository ledgerRepository;
    private final FastApiClient fastApiClient;

    /**
     * 비용 발생 후 기록. 누적 비용 업데이트. 예산 초과 시 BUDGET_BLOCKED 전이.
     */
    @Transactional
    public BigDecimal record(Long jobId, String category, BigDecimal amount, String currency, String note) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        BigDecimal current = calculateTotalCostKrw(jobId);
        BigDecimal budgetAmount = toBudgetKrw(amount, currency);
        BigDecimal newTotal = current.add(budgetAmount);

        // 예산 가드
        if (job.getBudgetCap() != null && newTotal.compareTo(job.getBudgetCap()) > 0) {
            job.setStatus(JobStatus.BUDGET_BLOCKED);
            jobRepository.save(job);
            log.warn("예산 초과로 BUDGET_BLOCKED 전이: jobId={}, new={}, cap={}",
                    jobId, newTotal, job.getBudgetCap());
            throw new BudgetExceededException(jobId, current, budgetAmount, job.getBudgetCap());
        }

        // 원장 기록
        CostLedger ledger = CostLedger.builder()
                .jobId(jobId)
                .category(category)
                .amount(amount)
                .currency(currency != null ? currency : "USD")
                .note(note)
                .build();
        ledgerRepository.save(ledger);

        // 누적 업데이트 (Spring + Worker 통틀어 전체 실비용)
        job.setCostAccumulated(newTotal.setScale(0, RoundingMode.HALF_UP));
        jobRepository.save(job);

        log.info("비용 기록: jobId={}, {}={}, 누적={}", jobId, category, amount, newTotal);
        return newTotal;
    }

    /**
     * 비용 발생 전 사전 체크. 추정 비용이 예산을 넘으면 BUDGET_BLOCKED 전이 + 예외.
     */
    @Transactional
    public void precheck(Long jobId, BigDecimal estimatedCost) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        if (job.getBudgetCap() == null) return;
        if (estimatedCost == null || estimatedCost.compareTo(BigDecimal.ZERO) <= 0) return;

        BigDecimal current = calculateTotalCostKrw(jobId);
        BigDecimal projected = current.add(estimatedCost);

        if (projected.compareTo(job.getBudgetCap()) > 0) {
            job.setStatus(JobStatus.BUDGET_BLOCKED);
            jobRepository.save(job);
            log.warn("예산 사전체크 실패로 BUDGET_BLOCKED 전이: jobId={}, projected={}, cap={}",
                    jobId, projected, job.getBudgetCap());
            throw new BudgetExceededException(jobId, current, estimatedCost, job.getBudgetCap());
        }
    }

    public List<CostLedger> getLedger(Long jobId) {
        return ledgerRepository.findByJobIdOrderByCreatedAtDesc(jobId);
    }

    public BigDecimal getTotal(Long jobId) {
        return calculateTotalCostKrw(jobId);
    }

    /**
     * 2026-10-12 job 14 재현: ImagesService가 이미지 생성 전 "남은 예산"을
     * 계산할 때 getTotal()(Spring+Worker 합산)을 쓰면, FastAPI의
     * budget.py::before_attempt()가 Worker 누적 비용을 자기 장부에서 다시
     * 더해 비교하므로 같은 금액이 양쪽에서 이중으로 차감된다. Worker 누적
     * 비용이 전체 예산의 절반을 넘으면 재시도를 아무리 반복해도 구조적으로
     * 통과할 수 없는 상태가 된다. 이미지/모션 생성 전 "남은 예산"은 Spring이
     * 직접 추적하는 비용(TTS/Claude 등)만 빼야 하고, Worker 쪽 누적 비용은
     * FastAPI 자신의 장부가 단독으로 추적·집행한다.
     */
    public BigDecimal getSpringOnlyTotal(Long jobId) {
        return totalInSpringBudgetKrw(jobId).setScale(0, RoundingMode.HALF_UP);
    }

    /**
     * Spring 원장(Claude/TTS 등)과 Worker 원장(Gemini/Fal 등)의 전체 누적 비용(KRW)을 구한다.
     */
    public BigDecimal calculateTotalCostKrw(Long jobId) {
        BigDecimal springTotal = totalInSpringBudgetKrw(jobId);
        BigDecimal workerTotal = getWorkerTotalKrw(jobId);
        return springTotal.add(workerTotal).setScale(0, RoundingMode.HALF_UP);
    }

    /**
     * 작업의 비용 상세 요약 DTO 생성.
     * Spring 원장 + Worker 원장을 통틀어 개별 호출 내역(items) 및 provider별 합계(groupedItems)를 1원 오차 없이 집계한다.
     */
    @Transactional
    public CostEstimateDto getSummaryDto(Long jobId) {
        VideoJob job = jobRepository.findById(jobId)
                .orElseThrow(() -> new RuntimeException("Job not found: " + jobId));

        List<CostEstimateDto.CostItemDto> unifiedItems = new ArrayList<>();

        // 1. Spring 원장 항목들 추가 (Claude LLM, ElevenLabs TTS 등)
        List<CostLedger> springLedgers = ledgerRepository.findByJobIdOrderByCreatedAtDesc(jobId);
        for (CostLedger l : springLedgers) {
            if (l.getAmount() == null || l.getAmount().compareTo(BigDecimal.ZERO) == 0) {
                continue; // 0원인 항목은 제외
            }
            String provider = mapCategoryToProvider(l.getCategory());
            BigDecimal amountKrw = toBudgetKrw(l.getAmount(), l.getCurrency()).setScale(0, RoundingMode.HALF_UP);
            String note = l.getNote() != null && !l.getNote().isBlank() ? l.getNote() : l.getCategory();
            unifiedItems.add(new CostEstimateDto.CostItemDto(provider, amountKrw, "KRW", note));
        }

        // 2. FastAPI Worker 원장 항목들 추가 (Gemini 이미지 씬, Fal Kling 모션 등)
        try {
            Map<String, Object> workerLedger = fastApiClient.getWorkerCostLedger(jobId);
            if (workerLedger != null && workerLedger.get("items") instanceof List<?> list) {
                for (Object o : list) {
                    if (o instanceof Map<?, ?> item) {
                        Object pVal = item.get("provider");
                        if (pVal == null) pVal = item.get("kind");
                        String rawProvider = pVal != null ? String.valueOf(pVal) : "worker";
                        String provider = mapKindToProvider(rawProvider);
                        BigDecimal amountKrw = asBigDecimal(item.get("amount_krw")).setScale(0, RoundingMode.HALF_UP);

                        if (amountKrw.compareTo(BigDecimal.ZERO) == 0) {
                            continue; // 0원인 항목 제외
                        }

                        String sceneKey = item.get("scene_key") != null ? String.valueOf(item.get("scene_key")) : null;
                        String model = item.get("model") != null ? String.valueOf(item.get("model")) : null;
                        String kind = item.get("kind") != null ? String.valueOf(item.get("kind")) : null;

                        String note = buildWorkerItemNote(provider, sceneKey, model, kind);
                        unifiedItems.add(new CostEstimateDto.CostItemDto(provider, amountKrw, "KRW", note));
                    }
                }
            }
        } catch (Exception e) {
            log.warn("FastAPI Worker 비용 원장 항목 조회 실패 (jobId={}): {}", jobId, e.getMessage());
        }

        // 3. Provider별 그룹핑 & 총합 계산
        Map<String, List<CostEstimateDto.CostItemDto>> grouped = unifiedItems.stream()
                .collect(Collectors.groupingBy(
                        CostEstimateDto.CostItemDto::getProvider,
                        LinkedHashMap::new,
                        Collectors.toList()
                ));

        List<CostEstimateDto.CostGroupSummaryDto> groupedItems = grouped.entrySet().stream()
                .map(entry -> {
                    String provider = entry.getKey();
                    int count = entry.getValue().size();
                    BigDecimal totalAmount = entry.getValue().stream()
                            .map(CostEstimateDto.CostItemDto::getAmount)
                            .filter(Objects::nonNull)
                            .reduce(BigDecimal.ZERO, BigDecimal::add)
                            .setScale(0, RoundingMode.HALF_UP);
                    return new CostEstimateDto.CostGroupSummaryDto(provider, count, totalAmount, "KRW");
                })
                .toList();

        // 4. 총 비용 (currentTotal) = 모든 프로바이더 그룹 totalAmount의 합 = 모든 unifiedItems amount의 합
        BigDecimal currentTotal = groupedItems.stream()
                .map(CostEstimateDto.CostGroupSummaryDto::getTotalAmount)
                .reduce(BigDecimal.ZERO, BigDecimal::add)
                .setScale(0, RoundingMode.HALF_UP);

        BigDecimal cap = job.getBudgetCap();
        BigDecimal remaining = cap != null ? cap.subtract(currentTotal) : null;

        // 5. DB의 cost_accumulated 필드를 실제 총 실비용으로 동기화한다. 기준은 예산 가드와 같은 calculateTotalCostKrw(원장 전체 합계)이고,
        //    비용 컬럼만 갱신한다(작업 행 전체를 save하면 그 사이 바뀐 상태를 옛 값으로 덮어쓴다 — 2026-10-19 20번 Job).
        BigDecimal authoritativeTotal = calculateTotalCostKrw(jobId);
        if (job.getCostAccumulated() == null || job.getCostAccumulated().compareTo(authoritativeTotal) != 0) {
            jobRepository.updateCostAccumulated(jobId, authoritativeTotal);
        }

        return CostEstimateDto.builder()
                .jobId(jobId)
                .currentTotal(currentTotal)
                .budgetCap(cap)
                .remaining(remaining)
                .status(job.getStatus().name())
                .items(unifiedItems)
                .groupedItems(groupedItems)
                .build();
    }

    private BigDecimal totalInSpringBudgetKrw(Long jobId) {
        return ledgerRepository.findByJobIdOrderByCreatedAtDesc(jobId).stream()
                .map(ledger -> toBudgetKrw(ledger.getAmount(), ledger.getCurrency()))
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    private BigDecimal getWorkerTotalKrw(Long jobId) {
        try {
            Map<String, Object> workerLedger = fastApiClient.getWorkerCostLedger(jobId);
            if (workerLedger != null && workerLedger.containsKey("total_krw")) {
                return asBigDecimal(workerLedger.get("total_krw"));
            }
        } catch (Exception e) {
            log.warn("FastAPI Worker 비용 총액 조회 실패 (jobId={}): {}", jobId, e.getMessage());
        }
        return BigDecimal.ZERO;
    }

    private BigDecimal toBudgetKrw(BigDecimal amount, String currency) {
        BigDecimal safeAmount = amount == null ? BigDecimal.ZERO : amount;
        String normalized = currency == null ? "USD" : currency.trim().toUpperCase(Locale.ROOT);
        return switch (normalized) {
            case "KRW" -> safeAmount;
            case "USD" -> safeAmount.multiply(PricingConfig.USD_TO_KRW_BUDGET_RATE)
                    .setScale(0, RoundingMode.HALF_UP);
            default -> safeAmount;
        };
    }

    private static BigDecimal asBigDecimal(Object value) {
        if (value instanceof BigDecimal decimal) return decimal;
        if (value instanceof Number number) return BigDecimal.valueOf(number.doubleValue());
        try {
            return new BigDecimal(String.valueOf(value));
        } catch (Exception ignored) {
            return BigDecimal.ZERO;
        }
    }

    private String mapCategoryToProvider(String category) {
        if (category == null) return "Claude";
        String upper = category.toUpperCase(Locale.ROOT);
        if (upper.contains("CLAUDE") || upper.contains("LLM")) return "Claude";
        if (upper.contains("ELEVENLABS") || upper.contains("TTS")) return "ElevenLabs";
        if (upper.contains("GEMINI") || upper.contains("IMAGE")) return "Gemini";
        if (upper.contains("KLING") || upper.contains("FAL")) return "Fal";
        return category;
    }

    private String mapKindToProvider(String rawProvider) {
        if (rawProvider == null) return "Gemini";
        String lower = rawProvider.toLowerCase(Locale.ROOT);
        if (lower.contains("gemini") || lower.contains("pro") || lower.contains("flash") || lower.contains("vision")) return "Gemini";
        if (lower.contains("fal") || lower.contains("kling")) return "Fal";
        if (lower.contains("claude")) return "Claude";
        if (lower.contains("elevenlabs") || lower.contains("tts")) return "ElevenLabs";
        return "Gemini";
    }

    private String buildWorkerItemNote(String provider, String sceneKey, String model, String kind) {
        if (sceneKey != null && !sceneKey.isBlank()) {
            String cleanScene = sceneKey.replace("image:", "Scene ").replace("kling:", "Scene ");
            if (!cleanScene.startsWith("Scene ")) {
                cleanScene = "Scene " + cleanScene;
            }
            if ("Fal".equalsIgnoreCase(provider)) {
                return cleanScene + " (Fal Kling 모션 비디오)";
            }
            if ("Gemini".equalsIgnoreCase(provider)) {
                return cleanScene + " (Gemini 2K Pro 이미지)";
            }
            return cleanScene + " (" + (model != null ? model : kind) + ")";
        }
        if (model != null && !model.isBlank()) {
            return model;
        }
        return kind != null ? kind : provider;
    }
}
