# 핫키워드 · 벤치마크 채널 발견 → 벤치마크 제작 설계 (덩어리 ①)

작성일: 2026-09-21 / 상태: 설계 확정, 구현 계획 작성 전

## 1. 배경과 목표

현재 메인화면의 "YouTube 채널 벤치마크"(`TrendingSidebar.jsx`)는 검색어가 `'주식'`으로 고정되어 입력창이 없고, 영상을 누르면 유튜브가 새 탭으로 열릴 뿐 제작으로 이어지지 않는다. 롱폼 만들기는 키워드를 처음부터 다시 검색한다. 두 기능이 단절되어 있다.

목표:
1. 카테고리별로 "지금 뜨는 핫키워드"를 발견한다 (주제어를 내가 넣지 않아도 됨).
2. 미리 등록한 벤치마크 채널이 최근 올린 영상을 본다.
3. 그렇게 고른 잘 되는 영상을 벤치마크로 삼아 롱폼 제작을 **키워드 재검색 없이** 시작한다.

## 2. 확정된 결정

| 항목 | 결정 |
| --- | --- |
| 벤치마크 범위 | 1단계: 주제 + 각도 + "뜨는 이유 포인트 분석". 자막 구조 분석은 2단계(1단계 사용 후 판단) |
| 훅 모방 | 훅의 유형·구조·리듬은 참고 허용, 다른 창작자의 문장을 그대로/거의 그대로 복제하는 것은 금지 |
| 사실 근거 | 영상 제목·조회수는 주제·관심도 문맥일 뿐 사실 근거가 아니다. 사실은 기존처럼 검증된 자료만 사용 |
| 발견 방식 | 유튜브 카테고리별 급상승 차트(`chart=mostPopular`, 호출당 쿼터 1) + 제목·태그 명사 집계 |
| 기간 | `48시간 급상승` / `7일 지속` 이중 보기. 두 기간 모두 등장하면 `지속 중`, 48시간에만 등장하면 `순간 급등` |
| 정렬 기준 | 절대 조회수가 아닌 시간당 조회수(조회수 ÷ 업로드 후 경과 시간) |
| 벤치마크 채널 | 내 제작 채널(A/B)별로 따로 관리. 기존 공용 채널은 그대로 유지("공용") |
| 범위 분할 | 덩어리 ①(발견 + 연결)을 먼저 구현, 덩어리 ②(비경제 대본 생성)는 ① 검증 후 별도 설계 |

## 3. 범위 밖 (이번에 하지 않음)

- 비경제 소재의 대본 생성(덩어리 ②). ① 단계에서 근거가 부족하면 "이 소재는 아직 제작 미지원"으로 명확히 안내한다.
- 영상 자막 수집·구조 분석(벤치마크 2단계).
- 구글 트렌드·네이버 데이터랩 연동.
- 7일을 넘는 기간 조회(검색 API 쿼터가 커서 제외).

## 4. 확인된 사실 (2026-09-21, 프로젝트 유튜브 키로 직접 조회, 지역 KR)

- 카테고리를 지정하지 않은 "전체 인기"는 `videoCategoryId` 파라미터를 **아예 빼야** 50개가 나온다 (빈 값이면 `videoChartNotFound`).
- 50개 안팎 정상: 영화·애니(1), 반려동물(15), 스포츠(17), 게임(20), 코미디(23), 엔터테인먼트(24), 뉴스·정치(25), 노하우·스타일(26), 과학기술(28).
- 적게 나옴: 음악(10) 30개, 사람·블로그(22) 10개.
- 차트 미제공: 교육(27) → 화면에서 제외하거나 "제공 안 됨"으로 표시.
- 차트는 기간 파라미터가 없다. 기간은 받은 뒤 `publishedAt`으로 걸러 적용한다.

## 5. 아키텍처

기존 부품 재사용:
- `app/providers/real/trending.py`: 유튜브 호출·쿼터(`_consume_quota`)·채널 기준선(`_fetch_channel_baseline`, uploads 재생목록 조회) 이미 존재.
- 대본 근거 전달 통로: `KEYWORD` 에셋의 후보 정보 → `ScriptService.extractCandidateEvidence` → 대본 워커 `candidate_evidence` → `_candidate_evidence_context`가 `source_videos`를 병합.
- `ReferenceChannelService`: 채널 등록·검증·활성화.

### 5.1 FastAPI (`backend/fastapi-workers`)

1. `GET /workers/trending/hot-keywords?category=&region=KR`
   - 차트를 1회 호출(카테고리 없으면 파라미터 생략), `publishedAt`으로 48시간/7일 창을 각각 계산.
   - 제목·태그에서 kiwipiepy로 명사 추출 → 시간당 조회수 가중 집계. `_keyword_coverage_terms`와 같은 불용어·의문사 필터 재사용.
   - 응답: `keywords[{keyword, score, videoCount, persistence: "sustained"|"spike", sampleVideoIds[]}]`, `videos[{videoId, title, channelTitle, views, subscribers, hoursSincePublish, viewsPerHour, categoryId, tags[]}]`, `windows`.
   - Redis 캐시 1시간. 미지원 카테고리는 `unsupported: true`를 명시적으로 반환(가짜 데이터 금지).
2. `GET /workers/youtube/channels/recent-uploads?channel_ids=&days=7`
   - 채널별 uploads 재생목록(1 단위) + `videos.list` 배치(50개당 1 단위).
   - 항목마다 시간당 조회수와 채널 최근 평균 대비 배수(`outperformanceIndex`)를 포함.
3. `POST /workers/benchmark/analyze` (Spring 내부 호출용)
   - 입력: 영상 메타(제목·태그·설명·통계·상위 댓글). Claude 1회로 "뜨는 이유" 2~3개와 훅 유형(`hook_type`)·제목 구조를 JSON으로 반환.
   - 문장 인용 금지, 유형·구조만 요약.

### 5.2 Spring (`backend/spring-app`)

- `ReferenceChannel.ownerChannelId` (nullable, 추가 컬럼). `null`은 공용. 목록 조회는 `ownerChannelId = 선택 채널 OR null`.
- 프록시: `GET /api/trending/hot-keywords`, `GET /api/reference-channels/recent-uploads?ownerChannelId=`.
- `POST /api/jobs/from-benchmark` — 입력 `{videoId, channelId, ...작업 옵션}`:
  1. 서버가 `videoId`로 통계를 다시 조회(화면 값을 신뢰하지 않음). 삭제·비공개면 409로 중단.
  2. `benchmark/analyze` 호출. 실패해도 제작은 막지 않고 `benchmark_analysis: null`과 사유를 저장.
  3. `VideoJob` 생성(`channelId` 고정, 키워드는 제목에서 클릭베이트 필러를 제거해 정제).
  4. `KEYWORD` 에셋에 `candidates[{keyword, source_videos:[영상], benchmark_analysis}]`와 `selection_path: "BENCHMARK"` 저장 후 기존 `KeywordService.confirm`으로 KEYWORD 게이트 승인.
- 기존 `jobs/{id}/keyword/*` 흐름은 변경하지 않는다.

### 5.3 대본 워커

- `_candidate_evidence_context`가 `benchmark_analysis`를 함께 전달.
- 대본 프롬프트에 `<benchmark_points>` 추가: "이 소재가 먹히는 이유"와 훅 유형 참고용. 문장 복제 금지, 제목·조회수는 사실 근거 아님을 명시.
- `narrative_planner.py`의 "훅을 모사하지 마세요"를 "유형·구조·리듬은 참고하되 문장 복제는 금지"로 변경.

### 5.4 프런트엔드 (`frontend/src`)

- 메인화면 `TrendingSidebar`를 `BenchmarkDiscovery`로 교체(또는 확장). 탭: `핫키워드` · `직접 검색` · `벤치마크 채널 신작`.
- 핫키워드: 카테고리 칩(4장의 지원 카테고리), 기간 토글, 키워드 칩(지속성 뱃지), 영상 카드.
- 직접 검색: 기존 `trendingYoutube` 사용, 결과 화면에 "핫키워드로 돌아가기"를 상시 표시.
- 영상 카드 클릭은 유튜브를 여는 대신 **선택**. 선택 패널에 "뜨는 이유" 요약, 제작 채널 선택, `이 영상으로 롱폼 제작 시작` 버튼.
- 버튼은 `/longform/new`로 이동하며 라우터 state로 벤치마크 정보를 전달. `JobNew.jsx`는 STEP 01을 건너뛰고 STEP 02부터 시작하고 상단에 "벤치마크: 영상 제목" 배지를 표시. 제출 시 `from-benchmark`를 호출. 일반 `롱폼 만들기`(직접 키워드 입력)는 그대로 유지.
- 관리자 "레퍼런스 채널" 탭에 "적용할 제작 채널" 선택(공용 포함)을 추가.

## 6. 에러 처리

| 상황 | 동작 |
| --- | --- |
| 유튜브 키 없음 / 쿼터 소진 | 가짜 데이터를 만들지 않고 "수집 불가" 안내. 캐시가 있으면 캐시 표시 |
| 카테고리 차트 미제공 | 해당 카테고리에 "제공 안 됨" 표시 |
| 선택 영상 삭제·비공개 | 제작 시작 시 안내하고 중단(409) |
| 포인트 분석 실패(크레딧 부족 등) | 제작은 진행, 화면에 "분석 없음" 표시 |
| 비경제 소재 근거 부족 | "이 소재는 아직 제작 미지원" 메시지(덩어리 ②에서 해소) |

## 7. 테스트 계획

- FastAPI 단위 테스트: 명사 집계·불용어/의문사 제외, 48시간/7일 지속성 판정, 미지원 카테고리 응답, 쿼터 소진 시 동작, 카테고리 파라미터 생략 로직.
- Spring 단위 테스트: 채널별 목록 필터(소유 채널 + 공용), `from-benchmark`(FastApiClient mock), 삭제 영상 409.
- 회귀: 기존 fastapi 전체 테스트(현재 1146개 통과)와 spring 컴파일.
- 프런트: `npm run build`.
- 통합: Docker 환경에서 실제 브라우저로 "핫키워드 → 영상 선택 → 제작 시작 → 대본 단계 도달" 확인.

## 8. 위험과 열린 항목

1. ① 완료 후에도 비경제 소재(예: 부산상어)의 대본은 아직 생성되지 않는다. 경제·시사 계열로 전체 흐름을 먼저 검증한다.
2. 포인트 분석은 제목·태그·설명·일부 댓글 기반이라 얕을 수 있다. 부족하면 벤치마크 2단계(자막 구조 분석)를 별도 설계한다.
3. 핫키워드 품질은 제목에서 명사를 뽑는 한계상 "영상", "챌린지" 같은 흔한 단어가 섞일 수 있어 구현 후 불용어를 조정한다.
4. 유튜브 쿼터: 차트·업로드 조회는 모두 낮은 단위(1)라 부담이 작지만, 기존 공유 쿼터 카운터(`_consume_quota`)를 반드시 거친다.
5. 저작권·정책: 훅은 유형·구조까지만 참고하고 문장 복제는 금지한다는 결정을 프롬프트와 테스트로 고정한다.
