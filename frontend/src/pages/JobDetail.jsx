import { useState, useMemo, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft, Download, CheckCircle, Loader,
  ThumbsUp, ThumbsDown, Zap, Star, AlertCircle, RotateCcw,
  FileText, Image as ImageIcon, Music, ChevronDown, ChevronUp,
  Clock, Edit, Save, Printer, Scissors, Copy, ExternalLink, Youtube, Info
} from 'lucide-react'
import Layout from '../components/Layout'
import { jobsApi } from '../api/jobs'
import { authStore } from '../store/auth'
import apiClient from '../api/client'
import { formatAutonomy, formatCategory } from '../constants/jobStatus'

const PIPELINE_STEPS = [
  { key: 'keyword', label: '키워드 탐색', pendingStatus: 'KEYWORD_PENDING', gate: 'KEYWORD',
    runFn: (id) => jobsApi.searchKeyword(id, '', 5) },
  { key: 'script', label: '스크립트 생성', pendingStatus: 'SCRIPT_PENDING', gate: 'SCRIPT',
    runFn: (id) => jobsApi.generateScript(id) },
  { key: 'tts', label: '음성(TTS) 합성', pendingStatus: 'TTS_PENDING', gate: 'TTS',
    runFn: (id) => jobsApi.generateTts(id) },
  { key: 'images', label: '이미지 생성', pendingStatus: 'IMAGES_PENDING', gate: 'IMAGES',
    runFn: (id) => jobsApi.generateImages(id) },
  { key: 'longform', label: '영상 조립', pendingStatus: 'ASSEMBLING', gate: 'PREVIEW',
    runFn: (id) => jobsApi.generateLongform(id) },
]

const STEP_PROGRESS_INFO = {
  keyword: {
    desc: '실시간 국내/해외 주요 지수 및 환율 정보를 조회하고 당일 뉴스 RSS 피드를 수집하여 팩트를 분석하고 트렌디한 영상 키워드 후보를 도출합니다.',
  },
  script: {
    desc: '수집한 주식 및 경제 수치들에 대해 3단계 교차 검증(3-Round Fact Checker)을 거쳐 정확한 사실만 확정합니다. 확정된 수치만을 사용하여 영상 길이에 맞춘 스토리보드 대본을 생성합니다. (분량에 비례하여 글자 수가 타겟팅됩니다.)',
  },
  tts: {
    desc: '작성된 영상 대본의 톤앤매너에 맞게 고음질 인공지능 성우의 음성 오디오 데이터로 합성하는 과정을 진행합니다.',
  },
  images: {
    desc: '각 시나리오 씬별 주식/경제 분석 흐름에 맞춰, 일관성 있는 금색 코인 마스코트 캐릭터와 직관적인 설명적 배경이 어우러진 고품질 AI 일러스트 이미지를 Google Gemini API를 통해 생성합니다.',
  },
  longform: {
    desc: '최종 생성된 스크립트 대본, TTS 오디오 타임라인, 생성된 AI 일러스트와 씬 전환 영상을 시간 동기화하여 자막과 함께 고화질 MP4 동영상 파일로 합치고 인코딩합니다.',
  },
}

const STATUS_ORDER = [
  'DRAFT', 'KEYWORD_PENDING', 'SCRIPT_PENDING', 'TTS_PENDING',
  'IMAGES_PENDING', 'IMAGES_RETRY_REQUIRED', 'ASSEMBLING', 'PREVIEW_PENDING', 'READY', 'PUBLISHED'
]
// FAILED는 STATUS_ORDER에 포함하지 않아 indexOf = -1 → 아래 로직이 'idle'을 반환하도록 유도

function getStepStatus(step, job, approvals) {
  // FAILED 상태: approval 기록이 있는 단계만 done, 나머지는 idle
  if (['FAILED', 'IMAGES_RETRY_REQUIRED'].includes(job.status) && job.status === 'FAILED') {
    return approvals.find(a => a.gate === step.gate) ? 'done' : 'idle'
  }
  if (approvals.find(a => a.gate === step.gate)) return 'done'
  if (['READY','PUBLISHED'].includes(job.status)) return 'done'
  if (job.status === step.pendingStatus) return 'active'
  if (job.status === 'IMAGES_RETRY_REQUIRED' && step.key === 'images') return 'active'
  if (job.status === 'ASSEMBLING' && step.key === 'longform') return 'active'
  const ji = STATUS_ORDER.indexOf(job.status)
  const si = STATUS_ORDER.indexOf(step.pendingStatus)
  return ji > si ? 'done' : 'idle'
}

const AUTONOMY_STYLE = {
  AUTO: 'bg-accent-green/20 text-accent-green border-accent-green/30',
  GUIDED: 'bg-accent-cyan/20 text-accent-cyan border-accent-cyan/30',
}
const AUTONOMY_DESC = {
  AUTO: '주제·길이·키워드 입력 후 전체 자동 진행', GUIDED: '키워드·스크립트·목소리·이미지 단계별 승인',
}

const hasYoutubeMetrics = (candidate) => Array.isArray(candidate?.source_videos) && candidate.source_videos.length > 0
const metricNumber = (value, digits = 0) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('ko-KR', { maximumFractionDigits: digits }) : '—'

function KeywordMetricGuide() {
  return <details className="mt-3 rounded-xl border border-slate-200 bg-slate-100/40 px-3 py-2 text-xs text-navy-400">
    <summary className="flex cursor-pointer items-center gap-1.5 font-semibold text-gray-200"><Info size={14} className="text-accent-cyan" />후보 지표 안내</summary>
    <div className="mt-2 grid gap-1.5 leading-relaxed md:grid-cols-2">
      <p><b className="text-gray-200">구독자 대비 조회</b> = 조회수 ÷ 구독자 수입니다. 채널 규모 대비 반응을 봅니다.</p>
      <p><b className="text-gray-200">채널 평균 대비</b> = 조회수 ÷ 해당 채널의 표본 평균 조회수입니다. 평소보다 얼마나 높은지 봅니다.</p>
      <p><b className="text-gray-200">시간당 조회</b> = 게시 후 현재까지의 평균 조회 속도입니다.</p>
      <p><b className="text-gray-200">—</b> 표시는 뉴스 후보이거나 공개 YouTube 지표를 수집하지 못한 경우입니다. 0으로 추정하지 않습니다.</p>
    </div>
  </details>
}

/**
 * [UI 개선]
 * - 기존 text-gray-*(Tailwind 기본 회색) → text-navy-400(라벨류) / text-gray-200(본문류)로 통일.
 *   navy 배경 위에서 gray는 색감이 미묘하게 어긋나 탁해 보이는 원인이었습니다.
 * - text-[9px]/text-[10px]/text-[11px]처럼 너무 작은 임의 크기들을 text-xs(12px)
 *   이상으로 올렸고, 대사/설명 본문(text-xs)은 text-sm(14px)으로 상향했습니다.
 * - 카드들에 shadow-card를 추가해 배경과의 구분감을 살렸습니다.
 * - 기능 로직(뮤테이션, 쿼리, 씬 편집/분할/재생성 등)은 전혀 건드리지 않았습니다.
 */
const HOOK_TYPE_LABEL = {
  number_context: '수치로 시작',
  belief_reversal: '통념 뒤집기',
  time_contrast: '시간 대비',
  hidden_context: '숨은 맥락',
  direct_question: '직접 질문',
  human_stake: '사람 이야기',
}

function BenchmarkKeywordCard({ candidate, confirmed }) {
  const video = candidate.source_videos?.[0] || {}
  const analysis = candidate.benchmark_analysis
  const hours = Number(video.hoursSincePublish)
  const views = Number(video.views || 0)
  const perHour = Number.isFinite(hours) ? Math.round(views / Math.max(hours, 1)) : null
  return (
    <div className="rounded-xl border border-cyan-200 bg-cyan-50/60 px-4 py-3.5 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-cyan-600 px-2 py-0.5 text-xs font-bold text-white">벤치마크 기반</span>
        <span className="text-sm font-bold text-slate-900">{candidate.keyword}</span>
        {confirmed && <span className="rounded-full border border-emerald-200 bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">✓ 확정됨</span>}
      </div>
      <div className="space-y-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700">
        <div className="font-semibold text-slate-900">{video.title || '벤치마크 영상'}</div>
        {video.channelTitle && <div className="text-slate-500">{video.channelTitle}</div>}
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span>조회수 <b>{metricNumber(views)}</b></span>
          {perHour != null && <span>시간당 <b>{metricNumber(perHour)}</b> (제작 시작 시점)</span>}
          <span>좋아요 <b>{metricNumber(video.likes)}</b></span>
        </div>
        {video.videoId && (
          <a href={`https://www.youtube.com/watch?v=${video.videoId}`} target="_blank" rel="noreferrer" className="inline-block text-cyan-700 underline">원본 영상 보기</a>
        )}
      </div>
      {analysis ? (
        <div className="space-y-1.5 text-xs text-slate-700">
          <div className="font-semibold text-slate-900">이 영상이 뜨는 이유 (자동 분석)</div>
          <ul className="list-disc space-y-0.5 pl-5">
            {(analysis.reasons || []).map((reason, index) => <li key={index}>{reason}</li>)}
          </ul>
          {analysis.hook_type && <div>훅 유형: <b>{HOOK_TYPE_LABEL[analysis.hook_type] || analysis.hook_type}</b></div>}
          {analysis.title_pattern && <div>제목 구조: {analysis.title_pattern}</div>}
        </div>
      ) : (
        <p className="text-xs text-slate-500">뜨는 이유 분석 없이 영상 정보만 반영해 진행했습니다.</p>
      )}
    </div>
  )
}

export default function JobDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [gateModal, setGateModal] = useState(null)
  const [runningStep, setRunningStep] = useState(null)
  const [expandedScript, setExpandedScript] = useState(false)

  const [isEditingScript, setIsEditingScript] = useState(false)
  const [editedScriptText, setEditedScriptText] = useState('')
  const [scriptViewMode, setScriptViewMode] = useState('paragraphs')
  const [editingSceneIndex, setEditingSceneIndex] = useState(null)
  const [editingSceneText, setEditingSceneText] = useState('')
  const [activeSceneActionIndex, setActiveSceneActionIndex] = useState(null)
  const [scenePage, setScenePage] = useState(1)
  const [longformScenePage, setLongformScenePage] = useState(1)
  const [imageSalt, setImageSalt] = useState(0)
  const [selectedThumbnailVariant, setSelectedThumbnailVariant] = useState(1)
  const [thumbnailPreset, setThumbnailPreset] = useState('')
  const [isGuidedConfirmOpen, setIsGuidedConfirmOpen] = useState(false)
  const [showEngPrompt, setShowEngPrompt] = useState({})
  const [showCostDetails, setShowCostDetails] = useState(false)

  const [selectedVoiceId, setSelectedVoiceId] = useState('default_ko')
  const [previewText, setPreviewText] = useState('오늘 코스피가 올랐다고요? 숫자만 보고 뛰어들면, 시장은 늘 한 발 먼저 웃습니다.')
  const [previewUrl, setPreviewUrl] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)

  const { data: voices = [] } = useQuery({
    queryKey: ['voices'],
    queryFn: () => apiClient.get('/channels/voices').then(r => r.data),
    staleTime: Infinity,
  })

  const { data: channels = [] } = useQuery({
    queryKey: ['channels'],
    queryFn: () => apiClient.get('/channels').then(r => r.data),
    staleTime: Infinity,
  })

  const { data: job, isLoading } = useQuery({
    queryKey: ['job', id], queryFn: () => jobsApi.get(id), refetchInterval: 3000,
  })
  // 자동 모드에서는 상태뿐 아니라 방금 생성된 산출물도 함께 다시 읽어야
  // 다음 단계와 이전 단계 결과가 새로고침 없이 동시에 보인다.
  const autoRefreshInterval = job?.autonomy === 'AUTO' && !['READY', 'PUBLISHED', 'FAILED', 'IMAGES_RETRY_REQUIRED'].includes(job?.status)
    ? 3000
    : false

  useEffect(() => {
    if (job && channels.length > 0) {
      const channel = channels.find(c => c.channelId === job.channelId)
      if (channel && channel.voiceId) {
        setSelectedVoiceId(channel.voiceId)
      }
      if (job.ttsVoiceId) setSelectedVoiceId(job.ttsVoiceId)
    }
  }, [job, channels])

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  const previewVoice = async () => {
    if (selectedVoiceId === 'default_ko' || !previewText.trim() || previewText.trim().length > 100) return
    setPreviewLoading(true)
    try {
      const response = await apiClient.post('/channels/voices/preview', {
        voiceId: selectedVoiceId,
        text: previewText.trim(),
      }, { responseType: 'blob' })
      if (previewUrl) URL.revokeObjectURL(previewUrl)
      setPreviewUrl(URL.createObjectURL(response.data))
    } catch (error) {
      console.error('목소리 미리듣기 실패', error)
    } finally {
      setPreviewLoading(false)
    }
  }

  const { data: approvals = [] } = useQuery({
    queryKey: ['approvals', id], queryFn: () => jobsApi.approvals(id), refetchInterval: 3000,
  })
  const { data: costs } = useQuery({
    queryKey: ['costs', id], queryFn: () => jobsApi.costs(id), refetchInterval: 10000,
  })

  // Temporal 백엔드 오케스트레이션이 활성화되었으므로, 프론트엔드에서의 AUTO 모드 이중 자동 트리거(REST API 중복 호출)를 방지하기 위해 비활성화합니다.
  // useEffect(() => {
  //   if (!job || job.autonomy !== 'AUTO' || runningStep || isLoading) return;
  //   const activeStep = PIPELINE_STEPS.find(step => {
  //     const ss = getStepStatus(step, job, approvals);
  //     return ss === 'active';
  //   });
  //   if (activeStep) {
  //     console.log('AUTO 모드: 자동 실행 트리거 ->', activeStep.key);
  //     handleRun(activeStep);
  //   }
  // }, [job, approvals, runningStep, isLoading]);

  const { data: kwAssets = [] } = useQuery({
    queryKey: ['assets', id, 'KEYWORD'], queryFn: () => jobsApi.assets(id, 'KEYWORD'), enabled: !!job, refetchInterval: autoRefreshInterval,
  })
  const { data: scriptAssets = [] } = useQuery({
    queryKey: ['assets', id, 'SCRIPT'], queryFn: () => jobsApi.assets(id, 'SCRIPT'), enabled: !!job, refetchInterval: autoRefreshInterval,
  })
  const { data: imageAssets = [] } = useQuery({
    queryKey: ['assets', id, 'SCENE_IMAGE'], queryFn: () => jobsApi.assets(id, 'SCENE_IMAGE'), enabled: !!job, refetchInterval: autoRefreshInterval,
  })
  const { data: ttsAssets = [] } = useQuery({
    queryKey: ['assets', id, 'TTS_AUDIO'], queryFn: () => jobsApi.assets(id, 'TTS_AUDIO'), enabled: !!job, refetchInterval: autoRefreshInterval,
  })
  const { data: youtubeMetadataAssets = [] } = useQuery({
    queryKey: ['assets', id, 'YOUTUBE_METADATA'], queryFn: () => jobsApi.assets(id, 'YOUTUBE_METADATA'), enabled: !!job, refetchInterval: autoRefreshInterval,
  })
  const { data: thumbnailAssets = [] } = useQuery({
    queryKey: ['assets', id, 'THUMBNAIL_IMAGE'], queryFn: () => jobsApi.assets(id, 'THUMBNAIL_IMAGE'), enabled: !!job,
  })

  const thumbnailMeta = useMemo(() => {
    const latest = thumbnailAssets[thumbnailAssets.length - 1]
    try { return JSON.parse(latest?.metaJson || '{}') } catch { return {} }
  }, [thumbnailAssets])
  const thumbnailVariants = thumbnailMeta?.longform_result?.variants || []
  const thumbnailVariantCount = Math.max(1, Math.min(3, thumbnailVariants.length || 1))
  const recommendedThumbnailVariant = Number(thumbnailMeta?.longform_result?.selected_variant ?? 0) + 1
  const thumbnailPersonMatches = thumbnailMeta?.person_matches || []
  const thumbnailPresetLabels = {
    person_led: '실사 인물 단독',
    chart_led: '차트 중심',
    mascot_led: '캐릭터 단독',
  }

  useEffect(() => {
    const saved = Number(thumbnailMeta?.longform_selected_variant || thumbnailMeta?.longform_result?.selected_variant + 1 || 1)
    setSelectedThumbnailVariant(saved)
  }, [thumbnailMeta])

  const youtubePackage = useMemo(() => {
    if (!youtubeMetadataAssets.length) return null
    try {
      return JSON.parse(youtubeMetadataAssets[youtubeMetadataAssets.length - 1].metaJson || '{}')
    } catch { return null }
  }, [youtubeMetadataAssets])

  const kwCandidates = useMemo(() => {
    for (let i = kwAssets.length - 1; i >= 0; i--) {
      try { const m = JSON.parse(kwAssets[i].metaJson || '{}'); if (m.candidates) return m.candidates } catch {}
    }
    return []
  }, [kwAssets])
  const isBenchmarkKeyword = kwCandidates.some(c => c && Object.prototype.hasOwnProperty.call(c, 'benchmark_analysis'))
  const keywordSelection = useMemo(() => {
    for (let i = kwAssets.length - 1; i >= 0; i--) {
      try {
        const meta = JSON.parse(kwAssets[i].metaJson || '{}')
        if (meta.selection_path || meta.error_code) return meta
      } catch {}
    }
    return {}
  }, [kwAssets])

  const scriptData = useMemo(() => {
    if (!scriptAssets.length) return null
    try {
      const latest = scriptAssets[scriptAssets.length - 1]
      return JSON.parse(latest.metaJson || '{}')
    } catch { return null }
  }, [scriptAssets])

  const fmt = (s) => {
    if (s == null || isNaN(s)) return '0:00'
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec < 10 ? '0' : ''}${sec}`
  }

  const imageList = useMemo(() => {
    return imageAssets.map(a => {
      try { return JSON.parse(a.metaJson || '{}') } catch { return null }
    }).filter(Boolean)
  }, [imageAssets])

  const sortedImageList = useMemo(() => {
    return [...imageList].sort((a, b) => (a.index || 0) - (b.index || 0))
  }, [imageList])

  // Large 20-minute jobs can have 200+ scenes. Keep the editor responsive by
  // rendering ten review cards at a time.
  const scenePageCount = Math.max(1, Math.ceil(sortedImageList.length / 10))
  const pagedImageList = useMemo(() => {
    const start = (scenePage - 1) * 10
    return sortedImageList.slice(start, start + 10)
  }, [scenePage, sortedImageList])

  const longformScenePageCount = Math.max(1, Math.ceil(sortedImageList.length / 10))
  const pagedLongformImageList = useMemo(() => {
    const start = (longformScenePage - 1) * 10
    return sortedImageList.slice(start, start + 10)
  }, [longformScenePage, sortedImageList])

  useEffect(() => {
    if (scenePage > scenePageCount) setScenePage(scenePageCount)
  }, [scenePage, scenePageCount])

  useEffect(() => {
    if (longformScenePage > longformScenePageCount) setLongformScenePage(longformScenePageCount)
  }, [longformScenePage, longformScenePageCount])

  const ttsInfo = useMemo(() => {
    if (!ttsAssets.length) return null
    try { return JSON.parse(ttsAssets[ttsAssets.length - 1].metaJson || '{}') } catch { return null }
  }, [ttsAssets])

  const approveMut = useMutation({
    mutationFn: ({ gate, comment }) => jobsApi.approve(id, gate, comment),
    onSuccess: () => { qc.invalidateQueries(['job',id]); qc.invalidateQueries(['approvals',id]); setGateModal(null) },
  })
  const rejectMut = useMutation({
    mutationFn: ({ gate, comment }) => jobsApi.reject(id, gate, comment),
    onSuccess: () => { qc.invalidateQueries(['job',id]); qc.invalidateQueries(['approvals',id]); setGateModal(null) },
  })

  const saveScriptMut = useMutation({
    mutationFn: (text) => jobsApi.confirmScript(id, text, scriptData?.sections || []),
    onSuccess: () => {
      qc.invalidateQueries(['job', id])
      qc.invalidateQueries(['assets', id, 'SCRIPT'])
      setIsEditingScript(false)
    },
    onError: (err) => {
      alert('스크립트 저장 실패: ' + (err.response?.data?.message || err.message))
    }
  })

  const revalidateScriptMut = useMutation({
    mutationFn: () => jobsApi.revalidateScript(id),
    onSuccess: () => {
      qc.invalidateQueries(['job', id])
      qc.invalidateQueries(['assets', id, 'SCRIPT'])
    },
    onError: (err) => {
      alert('스크립트 재검증 실패: ' + (err.response?.data?.message || err.message))
    },
  })

  const confirmKeywordMut = useMutation({
    mutationFn: (keyword) => jobsApi.confirmKeyword(id, keyword),
    onSuccess: () => {
      qc.invalidateQueries(['job', id])
      qc.invalidateQueries(['approvals', id])
      qc.invalidateQueries(['assets', id, 'KEYWORD'])
    },
    onError: (err) => alert('키워드 선택 실패: ' + (err.response?.data?.message || err.message)),
  })

  const regenImageMut = useMutation({
    mutationFn: ({ index, text, subtitleText, section, mode }) => jobsApi.updateSceneImage(id, index, {
      text,
      subtitleText,
      section,
      mode,
    }),
    onSuccess: (data, variables) => {
      qc.invalidateQueries(['assets', id, 'SCENE_IMAGE'])
      setEditingSceneIndex(null)
      setActiveSceneActionIndex(null)
      setImageSalt(prev => prev + 1)
      const modeStr = variables.mode === 'image_only' ? '이미지만' : '원문과 이미지';
      alert(`${modeStr} 변경이 저장되었습니다. 최종 영상에는 '동영상 재조립'을 실행한 뒤 반영됩니다.`);
    },
    onError: (err) => {
      alert('수정 실패: ' + (err.response?.data?.message || err.message))
    },
    onSettled: () => setActiveSceneActionIndex(null),
  })

  const splitSceneMut = useMutation({
    mutationFn: ({ index, part1, part2 }) => jobsApi.splitScene(id, index, part1, part2),
    onSuccess: () => {
      qc.invalidateQueries(['assets', id, 'SCENE_IMAGE'])
      setEditingSceneIndex(null)
      alert("씬 분할이 성공적으로 반영되었습니다. 수정 사항을 최종 동영상 파일에 완전히 적용하려면 우측 상단의 '동영상 재조립' 버튼을 꼭 클릭해 주세요.");
    },
    onError: (err) => {
      alert('씬 분할 실패: ' + (err.response?.data?.message || err.message))
    }
  })

  const sceneKlingMut = useMutation({
    mutationFn: ({ index, enabled }) => jobsApi.setSceneKling(id, index, enabled),
    onSuccess: () => {
      qc.invalidateQueries(['assets', id, 'SCENE_IMAGE'])
    },
    onError: (err) => {
      alert('Kling 씬 설정 실패: ' + (err.response?.data?.message || err.message))
    }
  })

  const rebuildLongformMut = useMutation({
    mutationFn: () => jobsApi.rebuildLongform(id),
    onSuccess: () => {
      qc.invalidateQueries(['job', id])
      qc.invalidateQueries(['assets', id])
      setImageSalt(prev => prev + 1)
      alert('동영상이 성공적으로 재조립되었습니다.')
    },
    onError: (err) => {
      alert('동영상 재조립 실패: ' + (err.response?.data?.message || err.message))
    }
  })

  const selectThumbnailMut = useMutation({
    mutationFn: (variant) => jobsApi.selectThumbnailVariant(id, 'longform', variant),
    onSuccess: ({ selected_variant }) => {
      setSelectedThumbnailVariant(selected_variant)
      setImageSalt(prev => prev + 1)
      qc.invalidateQueries(['assets', id, 'THUMBNAIL_IMAGE'])
    },
    onError: (err) => alert('썸네일 선택 실패: ' + (err.response?.data?.message || err.message)),
  })

  const regenerateThumbnailMut = useMutation({
    mutationFn: (preset) => jobsApi.regenerateThumbnail(id, 'longform', preset || undefined),
    onSuccess: () => {
      setImageSalt(prev => prev + 1)
      qc.invalidateQueries(['assets', id, 'THUMBNAIL_IMAGE'])
    },
    onError: (err) => alert('썸네일 재생성 실패: ' + (err.response?.data?.message || err.message)),
  })

  const stopMutation = useMutation({
    mutationFn: () => jobsApi.stop(id),
    onSuccess: () => {
      qc.invalidateQueries(['job', id])
      alert('작업이 중지되었습니다.')
    },
    onError: (err) => {
      alert('작업 중지 실패: ' + (err.response?.data?.message || err.message))
    }
  })

  const deleteMutation = useMutation({
    mutationFn: () => jobsApi.delete(id),
    onSuccess: () => {
      alert('작업이 성공적으로 삭제되었습니다.')
      navigate('/longform')
    },
    onError: (err) => {
      alert('작업 삭제 실패: ' + (err.response?.data?.message || err.message))
    }
  })

  const handleStop = () => {
    if (window.confirm('정말로 진행 중인 영상 제작 작업을 즉시 중지하시겠습니까?')) {
      stopMutation.mutate()
    }
  }

  const handleDelete = () => {
    if (window.confirm('정말로 이 작업을 삭제하시겠습니까? 관련 데이터베이스 기록 및 미디어 파일이 완전히 제거됩니다.')) {
      deleteMutation.mutate()
    }
  }

  const publishMut = useMutation({
    mutationFn: () => jobsApi.publish(id),
    onSuccess: () => {
      qc.invalidateQueries(['job', id])
      qc.invalidateQueries(['assets', id])
      alert("유튜브 업로드가 완료되었습니다!");
    },
    onError: (err) => {
      alert('유튜브 업로드 실패: ' + (err.response?.data?.message || err.message))
    }
  })
  const { data: qcAssets = [] } = useQuery({
    queryKey: ['assets', id, 'QC_REPORT'], queryFn: () => jobsApi.assets(id, 'QC_REPORT'), enabled: !!job, refetchInterval: autoRefreshInterval,
  })
  const { data: klingMotionPolicy = {} } = useQuery({
    queryKey: ['pipeline', 'motion-policy'],
    queryFn: async () => (await apiClient.get('/pipeline/motion-policy')).data,
    enabled: !!job,
    staleTime: 60_000,
  })

  const klingMotionWindowEnd = useMemo(() => {
    const targetSeconds = Number(job?.longformTargetMinutes || 0) * 60
    const threshold = Number(klingMotionPolicy.long_threshold_seconds ?? 600)
    const shortWindow = Number(klingMotionPolicy.short_window_seconds ?? 45)
    const longWindow = Number(klingMotionPolicy.long_window_seconds ?? 60)
    return targetSeconds > threshold ? longWindow : shortWindow
  }, [job?.longformTargetMinutes, klingMotionPolicy])

  const outputQc = useMemo(() => {
    if (!qcAssets.length) return null
    try { return JSON.parse(qcAssets[qcAssets.length - 1].metaJson || '{}') } catch { return null }
  }, [qcAssets])

  const retryScriptMut = useMutation({
    mutationFn: () => jobsApi.retryFromScript(id),
    onSuccess: () => {
      qc.invalidateQueries(['job', id])
      qc.invalidateQueries(['approvals', id])
      qc.invalidateQueries(['assets', id])
    },
    onError: (err) => alert('스크립트 재시작 실패: ' + (err.response?.data?.message || err.message)),
  })

  const handleRun = async (step) => {
    setRunningStep(step.key)
    try {
      if (step.key === 'tts') {
        await jobsApi.generateTts(id, selectedVoiceId)
      } else {
        await step.runFn(id)
      }
      qc.invalidateQueries(['job',id])
      qc.invalidateQueries(['approvals',id])
      qc.invalidateQueries(['assets',id])
    } catch(e){ console.error(e); alert('실행 실패: ' + (e.response?.data?.message || e.message)) }
    finally { setRunningStep(null) }
  }

  const handleGuidedGateApprove = async (step) => {
    setRunningStep(`${step.key}-approve`)
    try {
      if (step.gate === 'TTS') {
        if (!selectedVoiceId || selectedVoiceId === 'default_ko') {
          throw new Error('TTS 생성 전에 ElevenLabs 목소리를 선택하세요.')
        }
        await jobsApi.selectTtsVoice(id, selectedVoiceId)
      }
      await jobsApi.approve(id, step.gate, step.gate === 'TTS' ? `목소리 선택: ${selectedVoiceId}` : '')
      qc.invalidateQueries(['job', id])
      qc.invalidateQueries(['approvals', id])
    } catch (e) {
      alert('게이트 승인 실패: ' + (e.response?.data?.message || e.message))
    } finally {
      setRunningStep(null)
    }
  }

  if (isLoading) return <Layout><div className="flex items-center justify-center h-64"><Loader className="animate-spin text-accent-cyan" size={32}/></div></Layout>
  if (!job) return <Layout><div className="text-navy-400 p-8">작업을 찾을 수 없습니다.</div></Layout>

  const isAuto = job.autonomy === 'AUTO'
  const isGuided = job.autonomy === 'GUIDED'
  const isManual = false
  const isDone = ['READY','PUBLISHED'].includes(job.status)
  const isRunning = !['DRAFT', 'READY', 'PUBLISHED', 'FAILED'].includes(job.status)
  const isDeletable = ['DRAFT', 'READY', 'FAILED'].includes(job.status)
  const token = authStore.getToken()

  return (
    <Layout>
      {/* 헤더 */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/longform')} className="text-navy-400 hover:text-slate-900 transition"><ChevronLeft size={24}/></button>
          <div>
            <h1 className="text-2xl font-bold">{job.title}</h1>
            <div className="text-sm text-navy-400 mt-1 flex items-center gap-2 flex-wrap">
              <span>{formatCategory(job.category)}</span><span>·</span><span>{job.longformTargetMinutes}분</span><span>·</span>
              <span className={`text-sm px-2.5 py-1 rounded-full border font-medium ${AUTONOMY_STYLE[job.autonomy]}`}>{formatAutonomy(job.autonomy)}</span>
              <span className="text-navy-400 text-sm">{AUTONOMY_DESC[job.autonomy]}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={job.status}/>
          {job.status === 'DRAFT' && (
            <button
              onClick={async () => {
                setRunningStep('keyword')
                try {
                  await jobsApi.searchKeyword(job.id, job.title || '', 5)
                  qc.invalidateQueries(['job', id])
                  qc.invalidateQueries(['assets', id])
                } catch (e) {
                  console.error(e)
                  alert('작업 시작 실패: ' + (e.response?.data?.message || e.message))
                } finally {
                  setRunningStep(null)
                }
              }}
              disabled={runningStep !== null}
              className="text-sm bg-accent-green text-navy-950 hover:bg-opacity-90 disabled:opacity-50 px-4 py-2 rounded-xl transition font-semibold flex items-center gap-1.5"
            >
              {runningStep === 'keyword' ? <Loader className="animate-spin" size={14}/> : <Zap size={14}/>}
              작업 시작
            </button>
          )}
          {isRunning && (
            <button
              onClick={handleStop}
              disabled={stopMutation.isPending}
              className="text-sm bg-red-950/40 text-red-400 border border-red-900/60 hover:bg-red-900/50 disabled:opacity-50 px-4 py-2 rounded-xl transition font-semibold"
            >
              작업 중지
            </button>
          )}
          {(job?.status === 'FAILED' || job?.status === 'IMAGES_RETRY_REQUIRED') && (
            <button
              onClick={() => retryScriptMut.mutate()}
              disabled={retryScriptMut.isPending}
              className="text-sm bg-accent-cyan text-navy-950 hover:opacity-90 disabled:opacity-50 px-4 py-2 rounded-xl transition font-semibold flex items-center gap-1.5"
            >
              {retryScriptMut.isPending ? <Loader className="animate-spin" size={14}/> : <RotateCcw size={14}/>}
              재시도
            </button>
          )}
          {isDeletable && (
            <button
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
              className="text-sm bg-navy-700/60 text-navy-400 border border-slate-300 hover:bg-navy-600/50 disabled:opacity-50 px-4 py-2 rounded-xl transition"
            >
              작업 삭제
            </button>
          )}
        </div>
      </div>

      {isGuided && !isDone && (
        <div className="bg-accent-cyan/10 border border-accent-cyan/30 rounded-xl px-5 py-4 mb-5 flex items-center gap-3">
          <AlertCircle className="text-accent-cyan flex-shrink-0" size={20}/>
          <p className="text-sm text-accent-cyan"><span className="font-semibold">반자동 모드</span> — 키워드·스크립트·목소리·이미지를 검토하고 승인할 때만 다음 단계로 진행됩니다.</p>
        </div>
      )}
      {isAuto && !isDone && (
        <div className="bg-accent-green/10 border border-accent-green/30 rounded-xl px-5 py-4 mb-5 flex items-center gap-3">
          <Zap className="text-accent-green flex-shrink-0" size={20}/>
          <div>
            <p className="text-sm text-accent-green"><span className="font-semibold">완전 자동 모드</span> — 백엔드 서버가 모든 단계를 자율적으로 실행합니다.</p>
            <p className="text-sm text-accent-green/70 mt-1">상태와 단계별 산출물은 3초마다 자동 갱신됩니다. 이전 단계의 후보·스크립트·음성·이미지도 이 화면에 유지됩니다.</p>
          </div>
        </div>
      )}
      {job.status === 'TOPIC_EVIDENCE_REQUIRED' && (
        <div className="mb-5 rounded-xl border border-accent-gold/40 bg-accent-gold/10 px-5 py-4 text-sm text-accent-gold">
          <div className="font-semibold">선택 후보의 최신 직접 근거가 부족합니다.</div>
          <div className="mt-1 text-navy-300">아래 근거 점수를 비교해 후보를 직접 선택하거나, 키워드를 수정해 다시 검색하세요.</div>
          {Array.isArray(keywordSelection.missing_terms) && keywordSelection.missing_terms.length > 0 && (
            <div className="mt-1 text-xs text-navy-400">부족한 검증 항목: {keywordSelection.missing_terms.join(', ')}</div>
          )}
        </div>
      )}

      {['FAILED', 'IMAGES_RETRY_REQUIRED'].includes(job.status) && (
        <div className="bg-accent-gold/10 border border-accent-gold/30 rounded-xl px-5 py-4 mb-5 flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-accent-gold">
              {imageList.length > 0
                ? '기존 대본·TTS·이미지가 정상 보존되어 있습니다. 동영상 조립 단계부터 즉시 재개할 수 있습니다.'
                : ttsAssets.length > 0
                ? '기존 승인 대본과 TTS 음성이 보존되어 있습니다. 이미지 생성 단계부터 즉시 재개할 수 있습니다.'
                : scriptData
                ? '기존 승인 대본이 보존되어 있습니다. 음성(TTS) 합성 단계부터 즉시 재개할 수 있습니다.'
                : '선택한 키워드가 유지되어 있습니다. 대본 생성 단계부터 즉시 재개할 수 있습니다.'}
            </p>
            <p className="text-xs text-accent-gold/70 mt-1">
              이전 단계에서 검증 및 확정된 산출물은 삭제되지 않고 그대로 재사용됩니다.
            </p>
          </div>
          <button
            onClick={() => retryScriptMut.mutate()}
            disabled={retryScriptMut.isPending}
            className="shrink-0 flex items-center gap-1.5 bg-accent-gold text-navy-950 text-sm font-semibold px-4 py-2.5 rounded-xl hover:opacity-90 disabled:opacity-50 transition shadow-sm"
          >
            {retryScriptMut.isPending ? <Loader size={15} className="animate-spin"/> : <RotateCcw size={15}/>}
            {imageList.length > 0
              ? '동영상 조립 재시도'
              : ttsAssets.length > 0
              ? '이미지 생성 재시도'
              : scriptData
              ? 'TTS 생성 재시도'
              : '대본 생성 재시도'}
          </button>
        </div>
      )}

      {job.outputPath && (
        <div className="bg-white rounded-xl border border-accent-green p-5 space-y-4 mb-6 shadow-card">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <CheckCircle className="text-accent-green" size={22}/>
              <div>
                <div className="font-semibold text-base">
                  {isDone ? '영상 생성 완료' : '영상 조립 완료 (미리보기)'}
                </div>
                <div className="text-sm text-navy-400 mt-0.5">{job.longformTargetMinutes}분 · 1920×1080</div>
              </div>
            </div>
            <div className="flex gap-2">
              {['PREVIEW_PENDING', 'READY'].includes(job.status) && (
                <button
                  onClick={() => rebuildLongformMut.mutate()}
                  disabled={rebuildLongformMut.isPending}
                  className="flex items-center gap-1.5 bg-accent-gold text-navy-950 font-semibold text-sm px-4 py-2 rounded-xl hover:opacity-90 disabled:opacity-50 transition"
                >
                  {rebuildLongformMut.isPending ? <Loader size={14} className="animate-spin"/> : <Zap size={14}/>}
                  동영상 재조립
                </button>
              )}
              {['PREVIEW_PENDING', 'READY'].includes(job.status) && (
                <a href={`/longform/${id}/shorts`}
                  className="flex items-center gap-1.5 bg-accent-cyan text-navy-950 font-semibold text-sm px-4 py-2 rounded-xl hover:opacity-90 transition"
                >
                  쇼츠 제작하기
                </a>
              )}
              <a href={`/api/files/download?path=${encodeURIComponent(job.outputPath)}&token=${token}`}
                className="flex items-center gap-2 bg-accent-green text-navy-950 font-semibold text-sm px-4 py-2 rounded-xl hover:opacity-90 transition" download>
                <Download size={14}/>MP4 다운로드
              </a>
            </div>
          </div>

          <div className="aspect-video bg-slate-50 rounded-xl overflow-hidden border border-slate-200">
            <video
              key={imageSalt}
              controls
              className="w-full h-full"
              src={`/api/files/download?path=${encodeURIComponent(job.outputPath)}&token=${token}`}
            />
          </div>
        </div>
      )}

      {['PREVIEW_PENDING', 'READY', 'PUBLISH_PENDING', 'PUBLISHED'].includes(job.status) && (
        <div className="bg-white rounded-xl border border-accent-cyan p-5 space-y-4 mb-6 shadow-card">
          <div className="flex items-center justify-between border-b border-slate-200 pb-3">
            <h3 className="text-base font-bold text-accent-cyan flex items-center gap-1.5">
              <Youtube size={18}/> YouTube 업로드 및 수동 발행 지원 킷
            </h3>
            {job.status === 'PUBLISHED' ? (
              <span className="text-sm bg-accent-green/10 text-accent-green font-bold px-2.5 py-1 rounded border border-accent-green/20">
                업로드 완료
              </span>
            ) : (
              <span className="text-sm bg-accent-gold/10 text-accent-gold font-bold px-2.5 py-1 rounded border border-accent-gold/20">
                {job.status === 'PUBLISH_PENDING' ? '게시 연동 대기' : `업로드 대기 중 (${job.autonomy} 모드)`}
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-slate-100/60 p-3 rounded-xl border border-slate-200 flex flex-col justify-between">
              <div>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h4 className="text-sm font-semibold text-gray-200">자동 썸네일 추천</h4>
                  {thumbnailPersonMatches.length > 0 && (
                    <span className="rounded-full border border-accent-gold/40 bg-accent-gold/10 px-2 py-0.5 text-[10px] font-semibold text-accent-gold">
                      {thumbnailPersonMatches.map((person) => person.person_name).filter(Boolean).join(' · ') || '실제 인물'} 반영
                    </span>
                  )}
                  {thumbnailPersonMatches.length === 0 && (
                    <span className="rounded-full border border-slate-300 bg-slate-100/70 px-2 py-0.5 text-[10px] text-slate-500">
                      승인 인물 사진 없음
                    </span>
                  )}
                </div>
                <div className="aspect-video bg-slate-50 rounded border border-slate-200 overflow-hidden relative">
                  <img
                    src={`/api/jobs/${id}/thumbnail/longform?t=${imageSalt}`}
                    alt="YouTube Thumbnail"
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      e.target.onerror = null;
                      e.target.src = "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?auto=format&fit=crop&w=400&q=80";
                    }}
                  />
                </div>
                <div className="grid grid-cols-3 gap-1.5 mt-2" aria-label="썸네일 후보 선택">
                  {Array.from({ length: thumbnailVariantCount }, (_, index) => index + 1).map((variant, index) => (
                    <button
                      key={variant}
                      type="button"
                      disabled={selectThumbnailMut.isPending}
                      onClick={() => selectThumbnailMut.mutate(variant)}
                      className={`relative aspect-video overflow-hidden rounded border transition ${selectedThumbnailVariant === variant ? 'border-accent-cyan ring-1 ring-accent-cyan' : 'border-slate-200 hover:border-navy-500'}`}
                      title={`후보 ${variant} 선택`}
                    >
                      <img
                        src={`/api/jobs/${id}/thumbnail/longform/variant/${variant}?t=${imageSalt}`}
                        alt={`썸네일 후보 ${variant}`}
                        className="w-full h-full object-cover"
                        onError={(e) => { e.currentTarget.parentElement.style.display = 'none' }}
                      />
                      {recommendedThumbnailVariant === variant && (
                        <span className="absolute left-0.5 top-0.5 rounded bg-accent-cyan px-1 text-[8px] font-bold text-navy-950">
                          추천
                        </span>
                      )}
                      <span className="absolute bottom-0.5 right-0.5 rounded bg-black/80 px-1 text-[9px] text-white">
                        {thumbnailPresetLabels[thumbnailVariants[index]?.preset] || `${variant}안`}
                      </span>
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-[11px] text-slate-500">
                  영상 장면과 사용 가능한 승인 에셋으로 만든 후보입니다. 실사 인물과 캐릭터는 서로 섞지 않고 별도 안으로 제안합니다.
                </p>
                <section className="mt-2 rounded-xl border border-accent-gold/30 bg-accent-gold/5 p-2" aria-label="노란 마스코트 스타일 추천안">
                  <div className="flex gap-2">
                    <img
                      src="/thumbnail-examples/yellow-mascot-style-reference-v1.png"
                      alt="노란 마스코트 스타일 추천 썸네일 예시"
                      className="h-16 w-28 rounded border border-accent-gold/30 object-cover"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-semibold text-accent-gold">노란 마스코트 스타일 추천안</p>
                      <p className="mt-0.5 text-[10px] leading-relaxed text-slate-500">
                        예시는 스타일 기준입니다. 선택하면 현재 영상 장면과 승인 에셋을 사용해 실제 후보로 재생성합니다.
                      </p>
                      <button
                        type="button"
                        disabled={regenerateThumbnailMut.isPending}
                        onClick={() => {
                          setThumbnailPreset('mascot_led')
                          regenerateThumbnailMut.mutate('mascot_led')
                        }}
                        className="mt-1.5 rounded border border-accent-gold/60 bg-accent-gold/10 px-2 py-1 text-[10px] font-semibold text-accent-gold transition hover:bg-accent-gold/20 disabled:opacity-50"
                      >
                        {regenerateThumbnailMut.isPending ? '생성 중…' : '이 스타일로 후보 생성'}
                      </button>
                    </div>
                  </div>
                </section>
                <div className="mt-2 flex gap-1.5">
                  <select
                    value={thumbnailPreset}
                    onChange={(event) => setThumbnailPreset(event.target.value)}
                    className="min-w-0 flex-1 rounded border border-slate-300 bg-slate-50 px-2 py-1.5 text-xs text-gray-200"
                    aria-label="썸네일 재생성 프리셋"
                  >
                    <option value="">자동 추천</option>
                    <option value="mascot_led">캐릭터 단독</option>
                    <option value="person_led">실사 인물 단독</option>
                    <option value="chart_led">차트 중심</option>
                  </select>
                  <button
                    type="button"
                    disabled={regenerateThumbnailMut.isPending}
                    onClick={() => regenerateThumbnailMut.mutate(thumbnailPreset)}
                    className="rounded border border-accent-cyan/60 bg-accent-cyan/10 px-2 py-1.5 text-xs font-semibold text-accent-cyan disabled:opacity-50"
                  >
                    {regenerateThumbnailMut.isPending ? '생성 중…' : '후보 재생성'}
                  </button>
                </div>
              </div>
              <a
                href={`/api/jobs/${id}/thumbnail/longform`}
                target="_blank"
                rel="noreferrer"
                download
                className="mt-3 w-full bg-navy-700 border border-slate-300 text-center text-sm text-accent-cyan py-2 rounded hover:bg-navy-600 transition flex items-center justify-center gap-1"
              >
                <Download size={14}/> 썸네일 다운로드
              </a>
            </div>

            <div className="md:col-span-2 space-y-3">
              {youtubePackage?.longform ? (
                <>
                  <div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-navy-400">추천 제목 (3안)</span>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(youtubePackage.longform.titles?.join('\n') || '');
                          alert('추천 제목 3안이 복사되었습니다.');
                        }}
                        className="text-sm text-accent-cyan hover:underline flex items-center gap-0.5"
                      >
                        <Copy size={12}/> 전체 복사
                      </button>
                    </div>
                    <div className="bg-slate-50 p-3 rounded border border-slate-200 space-y-2 mt-1.5">
                      {youtubePackage.longform.titles?.map((t, idx) => (
                        <div key={idx} className="flex items-start gap-1.5 text-sm text-gray-200">
                          <span className="text-accent-cyan font-bold">안{idx+1}.</span>
                          <span className="flex-1 select-all">{t}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-navy-400">더보기 상세 설명글 (Description)</span>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(youtubePackage.longform.description || '');
                          alert('더보기 글이 복사되었습니다.');
                        }}
                        className="text-sm text-accent-cyan hover:underline flex items-center gap-0.5"
                      >
                        <Copy size={12}/> 복사
                      </button>
                    </div>
                    <textarea
                      readOnly
                      value={youtubePackage.longform.description || ''}
                      className="w-full bg-slate-50 border border-slate-200 rounded p-3 text-sm text-gray-200 mt-1.5 h-20 focus:outline-none resize-none font-mono"
                    />
                  </div>

                  <div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-navy-400 font-mono">태그 / 해시태그</span>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(youtubePackage.longform.tags?.join(', ') || '');
                          alert('해시태그가 복사되었습니다.');
                        }}
                        className="text-sm text-accent-cyan hover:underline flex items-center gap-0.5"
                      >
                        <Copy size={12}/> 복사
                      </button>
                    </div>
                    <div className="bg-slate-50 p-3 rounded border border-slate-200 mt-1.5 text-sm text-accent-cyan flex flex-wrap gap-1.5">
                      {youtubePackage.longform.tags?.map((tag, idx) => (
                        <span key={idx} className="bg-white px-2 py-1 rounded border border-slate-200">#{tag}</span>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <div className="text-sm text-navy-400 h-full flex items-center justify-center">
                  <Loader size={14} className="animate-spin mr-1.5"/> 유튜브 메타데이터 생성 중...
                </div>
              )}
            </div>
          </div>

          <div className="border-t border-slate-200 pt-3 flex items-center justify-between">
            <div>
              {job.youtubeUrl && (
                <a
                  href={job.youtubeUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm text-accent-cyan hover:underline flex items-center gap-1"
                >
                  <ExternalLink size={14}/> YouTube 업로드 동영상 링크 열기
                </a>
              )}
            </div>

            <div className="flex gap-2">
              {job.status === 'READY' && (
                <button
                  onClick={() => {
                    if (job.autonomy === 'GUIDED') {
                      setIsGuidedConfirmOpen(true);
                    } else {
                      if (confirm("유튜브 채널로 즉시 업로드(시뮬레이션)하시겠습니까?")) {
                        publishMut.mutate();
                      }
                    }
                  }}
                  disabled={publishMut.isPending}
                  className="flex items-center gap-1.5 bg-red-600 text-white font-semibold text-sm px-5 py-2.5 rounded-xl hover:bg-red-500 disabled:opacity-50 transition"
                >
                  {publishMut.isPending ? <Loader size={14} className="animate-spin"/> : <Youtube size={14}/>}
                  {job.autonomy === 'GUIDED' ? '업로드 검토 및 발행' : '즉시 YouTube 업로드'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {isGuidedConfirmOpen && (
        <div className="fixed inset-0 bg-black/75 z-50 flex items-center justify-center p-4">
          <div className="bg-slate-100 border border-slate-200 rounded-xl p-6 max-w-xl w-full space-y-4">
            <h3 className="text-base font-bold text-accent-cyan flex items-center gap-1.5 border-b border-navy-800 pb-3">
              <Youtube size={18}/> YouTube 업로드 검토 (GUIDED 게이트)
            </h3>

            <div className="space-y-3">
              <div>
                <label className="text-sm text-navy-400">제목 선택</label>
                <div className="space-y-1.5 mt-1.5">
                  {youtubePackage?.longform?.titles?.map((t, idx) => (
                    <label key={idx} className="flex items-start gap-2 bg-slate-50 p-2.5 rounded border border-navy-800 hover:border-slate-200 cursor-pointer text-sm text-gray-200">
                      <input
                        type="radio"
                        name="selected_title"
                        defaultChecked={idx === 0}
                        className="mt-0.5 accent-accent-cyan"
                      />
                      <span>{t}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-sm text-navy-400">더보기 상세 설명글</label>
                <textarea
                  readOnly
                  value={youtubePackage?.longform?.description || ''}
                  className="w-full bg-slate-50 border border-navy-800 rounded p-2.5 text-sm text-gray-200 mt-1.5 h-24 focus:outline-none resize-none font-mono"
                />
              </div>

              <div>
                <label className="text-sm text-navy-400">추천 해시태그</label>
                <div className="bg-slate-50 p-2.5 rounded border border-navy-800 mt-1.5 text-sm text-accent-cyan flex flex-wrap gap-1.5">
                  {youtubePackage?.longform?.tags?.map((tag, idx) => (
                    <span key={idx} className="bg-slate-100 px-2 py-1 rounded border border-navy-800">#{tag}</span>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-2 border-t border-navy-800 pt-3">
              <button
                onClick={() => setIsGuidedConfirmOpen(false)}
                className="bg-navy-700 hover:bg-navy-600 text-sm px-4 py-2 rounded text-navy-400 transition"
              >
                닫기
              </button>
              <button
                onClick={() => {
                  setIsGuidedConfirmOpen(false);
                  publishMut.mutate();
                }}
                className="bg-red-600 hover:bg-red-500 text-sm px-5 py-2 rounded text-white font-semibold transition"
              >
                검토 승인 및 업로드
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2 space-y-3">
          {PIPELINE_STEPS.map((step, idx) => {
            const ss = getStepStatus(step, job, approvals)
            const approval = approvals.find(a => a.gate === step.gate)
            const canRetryBlockedImageStep = job.status === 'IMAGES_RETRY_REQUIRED' && step.key === 'images'
            // GUIDED 워크플로는 각 생성 Activity를 자동 실행하지 않는다.
            // 따라서 키워드·TTS뿐 아니라 대본, 이미지, 조립 단계에도 실제
            // 실행 버튼이 있어야 승인 게이트까지 진행할 수 있다.
            const showRun = ss === 'active' && runningStep === null && (
              isManual || isGuided || canRetryBlockedImageStep
            )
            const guidedGates = ['KEYWORD','SCRIPT','TTS','IMAGES','PREVIEW']
            // SCRIPT는 실제 LLM 에셋이 저장되고 hard-fail·수동 검토 계약을
            // 통과한 뒤에만 승인할 수 있다. 다른 게이트의 기존 동작은 유지한다.
            const scriptReady = step.gate !== 'SCRIPT' || (
              scriptData !== null &&
              scriptData?.used_real_llm === true &&
              scriptData?.requires_manual_review !== true
            )
            const showGuidedApprove = (isGuided || isAuto) && ss === 'active' && guidedGates.includes(step.gate) && scriptReady
            const showManualApprove = isManual && ss === 'active' && runningStep !== step.key

            return (
              <div key={step.key} className={`bg-white rounded-xl border transition shadow-card ${ss === 'active' ? 'border-accent-cyan' : 'border-slate-200'}`}>
                <div className="flex items-center justify-between px-5 py-4">
                  <div className="flex items-center gap-3">
                    <StepIcon status={ss} idx={idx+1}/>
                    <div>
                      <div className="font-semibold text-base">{step.label}</div>
                      {approval && <div className="text-sm text-navy-400 mt-0.5">{approval.result === 'AUTO_APPROVED' ? '⚡ 자동 승인' : `✓ ${approval.approvedBy}`}</div>}
                      {ss === 'active' && isAuto && job.status !== 'IMAGES_RETRY_REQUIRED' && (
                        <div className="text-sm text-accent-cyan mt-0.5 flex items-center gap-1"><Loader size={12} className="animate-spin"/>자동 진행 중</div>
                      )}
                      {ss === 'active' && (job.status === 'IMAGES_RETRY_REQUIRED' || !isAuto) && (
                        <div className="text-sm text-amber-500 font-semibold mt-0.5">⚠️ 이미지 검수 및 승인 대기 중</div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {step.gate === 'SCRIPT' && ss === 'active' && scriptData?.requires_manual_review === true && (
                      <button
                        type="button"
                        onClick={() => revalidateScriptMut.mutate()}
                        disabled={revalidateScriptMut.isPending || !!runningStep}
                        className="text-xs bg-amber-50 text-amber-700 border border-amber-300 px-3 py-2 rounded-xl hover:bg-amber-100 transition font-semibold disabled:opacity-50"
                      >
                        {revalidateScriptMut.isPending ? '재검증 중…' : '최신 기준으로 재검증'}
                      </button>
                    )}
                    {showRun && (
                      <div className="flex items-center gap-2">
                        {step.key === 'tts' && (showRun || (isGuided && ss === 'active')) && (
                          <div className="flex items-center gap-2">
                            <select
                              value={selectedVoiceId}
                              onChange={(e) => setSelectedVoiceId(e.target.value)}
                              className="bg-navy-700 border border-slate-300 rounded-xl px-2.5 py-1.5 text-xs text-slate-900 focus:outline-none focus:ring-1 focus:ring-accent-cyan"
                            >
                              <option value="default_ko">기본 한국어 목소리 (gTTS)</option>
                              {voices.map((v) => (
                                <option key={v.voiceId} value={v.voiceId}>
                                  {v.name} ({v.category})
                                </option>
                              ))}
                            </select>
                            {selectedVoiceId !== 'default_ko' && (
                              <div className="flex flex-col gap-1">
                                <div className="flex items-center gap-1">
                                  <input
                                    value={previewText}
                                    maxLength={100}
                                    onChange={(e) => setPreviewText(e.target.value)}
                                    className="w-64 bg-white border border-slate-300 rounded px-2 py-1 text-xs text-slate-900"
                                    aria-label="미리듣기 문장"
                                  />
                                  <button
                                    type="button"
                                    onClick={previewVoice}
                                    disabled={previewLoading || !previewText.trim() || previewText.trim().length > 100}
                                    className="px-2 py-1 rounded bg-accent-violet/20 text-accent-violet text-xs disabled:opacity-50"
                                  >{previewLoading ? '생성 중…' : '미리듣기'}</button>
                                </div>
                                <span className="text-[10px] text-navy-400">{previewText.trim().length}/100자 · 같은 문장은 7일간 캐시</span>
                                {previewUrl && <audio src={previewUrl} controls autoPlay className="h-6 w-56" />}
                              </div>
                            )}
                            {selectedVoiceId !== 'default_ko' && voices.find(v => v.voiceId === selectedVoiceId) && (
                              <div className="flex flex-col gap-1">
                                {voices.find(v => v.voiceId === selectedVoiceId).previewUrl && (
                                  <div className="flex items-center gap-1">
                                    <span className="text-[9px] text-navy-400 font-semibold w-8">톤:</span>
                                    <audio
                                      src={voices.find(v => v.voiceId === selectedVoiceId).previewUrl}
                                      controls
                                      className="h-5 w-24"
                                      style={{ filter: 'invert(0.9) hue-rotate(180deg)' }}
                                    />
                                  </div>
                                )}
                                {voices.find(v => v.voiceId === selectedVoiceId).auditionUrl && (
                                  <div className="flex items-center gap-1">
                                    <span className="text-[9px] text-accent-cyan font-semibold w-8">낭독:</span>
                                    <audio
                                      src={voices.find(v => v.voiceId === selectedVoiceId).auditionUrl}
                                      controls
                                      className="h-5 w-24"
                                      style={{ filter: 'invert(0.9) hue-rotate(180deg)' }}
                                    />
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                        <button onClick={() => handleRun(step)} disabled={!!runningStep}
                          className="flex items-center gap-1.5 bg-accent-cyan text-navy-950 text-sm font-semibold px-4 py-2 rounded-xl hover:opacity-90 disabled:opacity-50 transition">
                          {runningStep === step.key ? <Loader size={14} className="animate-spin"/> : <Zap size={14}/>}실행
                        </button>
                      </div>
                    )}
                    {(showManualApprove || showGuidedApprove) && (
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setGateModal({ gate: step.gate, step })}
                          className="text-xs bg-navy-700 text-gray-200 border border-slate-300 px-3 py-2 rounded-xl hover:bg-navy-600 transition font-medium flex items-center gap-1"
                        >
                          🔍 상세보기/검토
                        </button>
                        <button
                          type="button"
                          onClick={() => handleGuidedGateApprove(step)}
                          disabled={runningStep === `${step.key}-approve` || (step.gate === 'TTS' && selectedVoiceId === 'default_ko')}
                          className="text-xs bg-accent-gold text-navy-950 font-bold px-3.5 py-2 rounded-xl hover:opacity-90 transition disabled:opacity-50 flex items-center gap-1 shadow-sm"
                        >
                          {runningStep === `${step.key}-approve` ? <Loader size={12} className="animate-spin"/> : '✓'} 승인
                        </button>
                      </div>
                    )}
                    {ss === 'active' && isAuto && <Loader size={16} className="animate-spin text-accent-cyan"/>}
                  </div>
                </div>

                {ss === 'active' && (
                  <div className="mx-5 mb-4 p-4 bg-slate-100/50 rounded-xl border border-slate-200/60 text-sm space-y-2.5">
                    {step.key === 'script' && (
                      <div className="flex items-center justify-end text-gray-200 font-semibold">
                        <div className="text-navy-400 text-xs">
                          목표 분량: <span className="text-accent-cyan">{job.longformTargetMinutes || 1}분</span>
                          (동일 음성 실측 기준 약 <span className="text-accent-cyan">{Math.round((job.longformTargetMinutes || 1) * 475)}자</span> 생성)
                        </div>
                      </div>
                    )}
                    <div className="text-navy-400 leading-relaxed text-sm">
                      {STEP_PROGRESS_INFO[step.key]?.desc}
                    </div>
                    {isAuto && (
                      <div className="pt-2.5 border-t border-slate-200/40 flex items-center gap-2 text-accent-cyan text-sm">
                        <Loader size={12} className="animate-spin"/>
                        <span>자동 모드가 실행 중입니다. 완료되면 다음 단계와 생성 결과가 자동으로 갱신되며, 이전 단계 결과도 계속 확인할 수 있습니다.</span>
                      </div>
                    )}
                  </div>
                )}

                {step.key === 'keyword' && kwCandidates.length > 0 && (
                  <div className="px-5 pb-4 border-t border-slate-200">
                    <div className="flex items-center justify-between mt-3 mb-3">
                      <p className="text-sm font-semibold text-navy-300">{isBenchmarkKeyword ? '벤치마크 영상으로 시작한 작업' : `후보 키워드 ${kwCandidates.length}개`}</p>
                      {!isBenchmarkKeyword && <span className="text-xs text-navy-500">점수 100점 만점 (뉴스+수치+카테고리 합산 후 YouTube 데이터 없을 시 100점 기준 환산)</span>}
                    </div>
                    {!isBenchmarkKeyword && <KeywordMetricGuide />}
                    <div className="space-y-3">
                      {kwCandidates.map((c, i) => {
                        if (isBenchmarkKeyword) {
                          return <BenchmarkKeywordCard key={i} candidate={c} confirmed={job.keyword === c.keyword} />
                        }
                        const hasPublicMetrics = hasYoutubeMetrics(c)
                        const wasAutoSelected = isAuto && job.keyword === c.keyword
                        const isTop = Number.isFinite(Number(c.score)) && i === 0
                        const score = Number(c.score) || 0
                        const newsScore = c.news_score ?? 0
                        const numericScore = c.market_data_score ?? 0
                        const categoryScore = c.category_score ?? 0
                        const youtubeScore = c.youtube_score
                        const newsCount = c.evidence?.news_count ?? 0
                        const numericVerified = c.evidence?.numeric_claims_verified
                        return (
                        <div key={i} className={`rounded-xl border transition ${isTop ? 'bg-accent-gold/8 border-accent-gold/30' : 'bg-white/60 border-slate-200/60'}`}>
                          {/* 헤더 영역 */}
                          <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-3.5 pb-2">
                            <div className="flex flex-wrap items-center gap-2 min-w-0">
                              {isTop && <Star size={13} className="text-accent-gold fill-accent-gold flex-shrink-0"/>}
                              <span className={`font-semibold text-sm truncate ${isTop ? 'text-slate-900 font-bold' : 'text-slate-800'}`}>{c.keyword}</span>
                              {isTop && <span className="flex-shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-800 border border-amber-200">1위</span>}
                              {wasAutoSelected && <span className="flex-shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800 border border-emerald-200">✓ 자동 선택됨</span>}
                              {newsCount === 0 && (
                                <span className="flex-shrink-0 rounded-lg bg-amber-50 px-2 py-0.5 text-[11px] font-bold text-amber-800 border border-amber-300">
                                  ⚠️ 뉴스 근거 없음 — 시장 데이터 기반 추정
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 flex-shrink-0">
                              {Number.isFinite(score) && (
                                <div className={`text-lg font-bold tabular-nums ${isTop ? 'text-indigo-600' : 'text-slate-700'}`}>
                                  {score}<span className="text-xs font-normal ml-0.5 text-slate-500">점</span>
                                </div>
                              )}
                              {(isGuided || job.status === 'TOPIC_EVIDENCE_REQUIRED') && ['KEYWORD_PENDING', 'TOPIC_EVIDENCE_REQUIRED'].includes(job.status) && (
                                <button
                                  type="button"
                                  onClick={() => confirmKeywordMut.mutate(c.keyword)}
                                  disabled={confirmKeywordMut.isPending}
                                  className="text-xs bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 px-3 py-1 rounded-xl font-semibold transition shadow-xs"
                                >선택</button>
                              )}
                            </div>
                          </div>

                          {/* 점수 바 영역 */}
                          {Number.isFinite(score) && (
                            <div className="px-4 pb-3 space-y-1.5">
                              <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden border border-slate-200">
                                <div className="h-full rounded-full bg-indigo-600 transition-all" style={{width: `${Math.min(score, 100)}%`}}/>
                              </div>
                              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2.5 text-xs mt-2.5">
                                {/* 뉴스 점수 블록 (독립 카드) */}
                                <div className={`rounded-xl p-2.5 border transition ${newsCount === 0 ? 'bg-amber-50/50 border-amber-200' : 'bg-slate-50 border-slate-200'}`}>
                                  <div className="flex items-center justify-between mb-1">
                                    <span className="font-semibold text-slate-700">뉴스 검증</span>
                                    <span className={`font-bold tabular-nums ${newsScore >= 20 ? 'text-emerald-700' : newsScore >= 10 ? 'text-amber-700' : 'text-slate-600'}`}>
                                      {newsScore}<span className="text-slate-400 font-normal">/40점</span>
                                    </span>
                                  </div>
                                  <div className="w-full bg-slate-200 rounded-full h-1 overflow-hidden">
                                    <div className="h-full rounded-full bg-blue-500" style={{width: `${(newsScore/40)*100}%`}}/>
                                  </div>
                                  <p className={`mt-1.5 text-[11px] font-medium ${newsCount === 0 ? 'text-amber-800 font-semibold' : 'text-slate-600'}`}>
                                    {newsCount === 0 ? '0건 수집 (시장 추정)' : `${newsCount}건 수집 완료`}
                                  </p>
                                </div>
                                {/* 수치 점수 블록 (독립 카드) */}
                                <div className="bg-purple-50/40 rounded-xl p-2.5 border border-purple-200">
                                  <div className="flex items-center justify-between mb-1">
                                    <span className="font-semibold text-purple-900">수치 검증</span>
                                    <span className={`font-bold tabular-nums ${numericScore >= 25 ? 'text-emerald-700' : numericScore >= 10 ? 'text-amber-700' : 'text-slate-600'}`}>
                                      {numericScore}<span className="text-slate-400 font-normal">/35점</span>
                                    </span>
                                  </div>
                                  <div className="w-full bg-purple-100 rounded-full h-1 overflow-hidden">
                                    <div className="h-full rounded-full bg-purple-600" style={{width: `${(numericScore/35)*100}%`}}/>
                                  </div>
                                  <p className="text-purple-800 mt-1.5 text-[11px] font-medium">
                                    {numericVerified === null ? '수치 주장 없음' : numericVerified ? '시장수치 검증 완료' : '수치 검증 미달'}
                                  </p>
                                </div>
                                {/* 카테고리 점수 블록 (독립 카드) */}
                                <div className="bg-blue-50/40 rounded-xl p-2.5 border border-blue-200">
                                  <div className="flex items-center justify-between mb-1">
                                    <span className="font-semibold text-blue-900">카테고리</span>
                                    <span className={`font-bold tabular-nums ${categoryScore >= 15 ? 'text-emerald-700' : categoryScore >= 8 ? 'text-amber-700' : 'text-slate-600'}`}>
                                      {categoryScore}<span className="text-slate-400 font-normal">/20점</span>
                                    </span>
                                  </div>
                                  <div className="w-full bg-blue-100 rounded-full h-1 overflow-hidden">
                                    <div className="h-full rounded-full bg-blue-600" style={{width: `${(categoryScore/20)*100}%`}}/>
                                  </div>
                                  <p className="text-blue-800 mt-1.5 text-[11px] font-medium">KOSPI 마켓 연관</p>
                                </div>
                                {/* YouTube 점수 블록 (독립 카드) */}
                                <div className="bg-slate-50 rounded-xl p-2.5 border border-slate-200">
                                  <div className="flex items-center justify-between mb-1">
                                    <span className="font-semibold text-slate-700">YouTube</span>
                                    <span className={`font-bold tabular-nums ${youtubeScore != null && youtubeScore >= 10 ? 'text-emerald-700' : youtubeScore != null ? 'text-amber-700' : 'text-slate-500'}`}>
                                      {youtubeScore != null ? <>{youtubeScore}<span className="text-slate-400 font-normal">/15점</span></> : <span className="text-[11px] font-normal text-slate-400">미수집</span>}
                                    </span>
                                  </div>
                                  <div className="w-full bg-slate-200 rounded-full h-1 overflow-hidden">
                                    <div className="h-full rounded-full bg-rose-500" style={{width: youtubeScore != null ? `${(youtubeScore/15)*100}%` : '0%'}}/>
                                  </div>
                                  <p className="text-slate-500 mt-1.5 text-[11px] font-medium">{youtubeScore == null ? '100점 가중치 환산' : '구독자 대비 반응'}</p>
                                </div>
                              </div>
                            </div>
                          )}

                          {/* YouTube 세부 지표 */}
                          {hasPublicMetrics && (
                            <div className="mx-4 mb-3 rounded-xl bg-slate-100/40 border border-slate-200/40 px-3 py-2 grid grid-cols-3 gap-x-4 gap-y-1 text-xs text-navy-400">
                              <span>조회수 <span className="text-navy-200">{metricNumber(c.views)}</span></span>
                              <span>구독자 <span className="text-navy-200">{metricNumber(c.subscribers)}</span></span>
                              <span>좋아요 <span className="text-navy-200">{c.likes_available === false ? '비공개' : metricNumber(c.likes)}</span></span>
                              <span>구독자 대비 <span className="text-accent-cyan font-semibold">{metricNumber(c.engagement_ratio, 2)}×</span></span>
                              <span>채널 평균 대비 <span className="text-accent-cyan font-semibold">{metricNumber(c.outperformance_index, 2)}×</span></span>
                              <span>시간당 조회 <span className="text-navy-200">{metricNumber(c.velocity_vph)}</span></span>
                            </div>
                          )}

                          {/* 근거 텍스트 */}
                          {(wasAutoSelected || c.reason) && (
                            <div className={`mx-4 mb-3 rounded-xl border px-3 py-2 text-xs leading-relaxed ${wasAutoSelected ? 'border-accent-green/30 bg-accent-green/8 text-accent-green' : 'border-slate-200/50 bg-slate-100/20 text-navy-400'}`}>
                              <span className="font-semibold">{wasAutoSelected ? '자동 선택 이유' : '선정 근거'}: </span>
                              {wasAutoSelected
                                ? <>자동 모드는 작업 요청에 전달된 키워드가 있으면 그 키워드를 우선 확정하고, 없으면 후보 우선순위 1위를 선택합니다. {hasPublicMetrics ? '후보 우선순위는 채널 평균 대비 40% · 구독자 대비 조회 30% · 시간당 조회 30%를 반영합니다.' : '공개 YouTube 지표가 없을 때에는 수집된 뉴스·후보 근거 순위를 반영합니다.'} {c.reason || ''}</>
                                : c.reason}
                            </div>
                          )}

                          {/* 소스 영상 */}
                          {c.source_videos?.length > 0 && (
                            <div className="mx-4 mb-3 pl-3 border-l-2 border-slate-200 text-[11px] text-navy-400 space-y-0.5">
                              {c.source_videos.slice(0, 2).map((video, vi) => (
                                <a
                                  key={video.video_id || vi}
                                  href={video.video_id ? `https://www.youtube.com/watch?v=${video.video_id}` : undefined}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="block hover:text-accent-cyan truncate max-w-[680px]"
                                >
                                  ↳ {video.title} · 조회 {(video.views || 0).toLocaleString()} · 구독 {(video.subscribers || 0).toLocaleString()}
                                </a>
                              ))}
                            </div>
                          )}
                        </div>
                        )
                      })}
                    </div>
                    {job.keyword && <div className="mt-3 text-sm text-navy-400 flex items-center gap-1.5"><span className="text-accent-green font-bold">✓</span> 확정: <span className="text-accent-cyan font-semibold">{job.keyword}</span></div>}
                  </div>
                )}


                {step.key === 'script' && scriptData && (
                  <div className="px-5 pb-4 border-t border-slate-200">
                    <div className="flex flex-wrap items-center justify-between gap-3 mt-3 mb-3 border-b border-slate-200/50 pb-3">
                      <div className="flex items-center gap-2">
                        <FileText size={15} className="text-accent-cyan"/>
                        <span className="text-sm text-navy-400">
                          {scriptData.char_count?.toLocaleString()}자
                          {scriptData.used_real_llm === false && (
                            <span className="ml-2 text-accent-gold">⚠ Mock 스크립트</span>
                          )}
                          {scriptData.used_real_llm === true && (
                            <span className="ml-2 text-accent-green">✓ LLM 생성</span>
                          )}
                        </span>
                        {scriptData.length_contract && (
                          <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-700">
                            목표 {Math.round((scriptData.length_contract.target_seconds || 0) / 60)}분 ·
                            {scriptData.length_contract.tts_speed}배속 ·
                            내레이션 {scriptData.length_contract.target_chars?.toLocaleString()}자
                          </span>
                        )}
                        {scriptData.keyword_validation?.passed && (
                          <span className="text-xs font-medium text-emerald-700">✓ 선택 키워드 반영 검증</span>
                        )}
                      </div>

                      {scriptData.quality_report?.storytelling && (
                        <div className="rounded-xl border border-accent-cyan/25 bg-accent-cyan/5 px-3 py-1.5 text-xs text-navy-300">
                          <span className="font-semibold text-accent-cyan">오리지널 금융 스토리텔링</span>
                          <span className="ml-2">편집 리듬 {scriptData.quality_report.storytelling.score}점</span>
                          {scriptData.quality_report.storytelling.suggestions?.length > 0 && (
                            <span className="ml-2 text-navy-400">· {scriptData.quality_report.storytelling.suggestions[0]}</span>
                          )}
                        </div>
                      )}

                      <div className="flex items-center gap-2 flex-wrap">
                        <div className="flex bg-navy-700 rounded p-1 text-xs">
                          <button onClick={() => setScriptViewMode('paragraphs')}
                            className={`px-2.5 py-1 rounded transition ${scriptViewMode === 'paragraphs' ? 'bg-accent-cyan text-navy-950 font-bold' : 'text-navy-400 hover:text-slate-900'}`}>단락 가독성</button>
                          {sortedImageList.length > 0 && (
                            <button onClick={() => setScriptViewMode('mixed')}
                              className={`px-2.5 py-1 rounded transition ${scriptViewMode === 'mixed' ? 'bg-accent-cyan text-navy-950 font-bold' : 'text-navy-400 hover:text-slate-900'}`}>대본 + 이미지</button>
                          )}
                          <button onClick={() => setScriptViewMode('raw')}
                            className={`px-2.5 py-1 rounded transition ${scriptViewMode === 'raw' ? 'bg-accent-cyan text-navy-950 font-bold' : 'text-navy-400 hover:text-slate-900'}`}>기본 텍스트</button>
                        </div>

                        <div className="flex gap-1">
                          <button onClick={() => {
                            const txt = scriptData.script || '';
                            const blob = new Blob([txt], { type: 'text/plain;charset=utf-8' });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = `script_${id}.txt`;
                            a.click();
                            URL.revokeObjectURL(url);
                          }} className="bg-navy-700 text-gray-200 hover:text-slate-900 text-xs px-2.5 py-1.5 rounded border border-slate-300 transition">TXT</button>

                          <button onClick={() => {
                            const txt = scriptData.script || '';
                            const cleanParas = txt.split(/\r?\n+/).map(p => p.trim()).filter(Boolean);

                            const bodyContent = sortedImageList.length > 0 ? `
                              <h2>주식 자동화 영상 스토리보드 대본 (Job #${id})</h2>
                              <div class="meta">
                                <p style="margin-bottom: 4pt;"><strong>영상 주제:</strong> ${job.title}</p>
                                <p style="margin-bottom: 0pt;"><strong>선택 키워드:</strong> ${job.keyword || ''}</p>
                              </div>
                              <hr style="margin-bottom: 20pt; border: none; border-top: 1px solid #dddddd;"/>
                              ${sortedImageList.map(img => `
                                <div style="margin-bottom: 20pt; page-break-inside: avoid;">
                                  <p style="font-family: Arial, sans-serif; font-size: 11pt; font-weight: bold; color: #0088cc; margin-bottom: 6pt;">[씬 #${img.index}] (${fmt(img.start)} ~ ${fmt(img.start + img.duration)})</p>
                                  <table cellpadding="0" cellspacing="0" style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                      <td style="width: 240px; padding-right: 15px; vertical-align: top;">
                                        <img src="${window.location.origin}/api/files/download?path=${encodeURIComponent(img.image_path)}&token=${token}" width="240" height="135" style="border: 1px solid #dddddd; display: block;" />
                                      </td>
                                      <td style="vertical-align: top; font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; text-align: justify; color: #333333;">
                                        ${img.prompt_ko || img.text || img.prompt || ''}
                                      </td>
                                    </tr>
                                  </table>
                                </div>
                              `).join('')}
                            ` : `
                              <h2>주식 자동화 영상 스크립트 (Job #${id})</h2>
                              <div class="meta">
                                <p style="margin-bottom: 4pt;"><strong>영상 주제:</strong> ${job.title}</p>
                                <p style="margin-bottom: 0pt;"><strong>선택 키워드:</strong> ${job.keyword || ''}</p>
                              </div>
                              <hr style="margin-bottom: 20pt; border: none; border-top: 1px solid #dddddd;"/>
                              ${cleanParas.map(p => `<p>${p}</p>`).join('')}
                            `;

                            const html = `
                              <html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
                              <head>
                                <meta charset="utf-8">
                                <title>Script</title>
                                <style>
                                  body { padding: 40px; }
                                  h2 { font-family: 'Malgun Gothic', Arial, sans-serif; color: #0d1b2a; margin-bottom: 16pt; border-bottom: 2px solid #0d1b2a; padding-bottom: 6pt; }
                                  .meta { font-family: 'Malgun Gothic', Arial, sans-serif; font-size: 10pt; color: #555555; margin-bottom: 20pt; background: #f4f6f9; padding: 10pt; }
                                  p { font-family: 'Malgun Gothic', Arial, sans-serif; line-height: 1.6; font-size: 11pt; margin-top: 0pt; margin-bottom: 12pt; text-align: justify; }
                                </style>
                              </head>
                              <body>
                                ${bodyContent}
                              </body>
                              </html>
                            `;
                            const blob = new Blob([html], { type: 'application/msword;charset=utf-8' });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = `script_${id}.doc`;
                            a.click();
                            URL.revokeObjectURL(url);
                          }} className="bg-navy-700 text-gray-200 hover:text-slate-900 text-xs px-2.5 py-1.5 rounded border border-slate-300 transition">Word</button>

                          <button onClick={() => {
                            const txt = scriptData.script || '';
                            const cleanParas = txt.split(/\r?\n+/).map(p => p.trim()).filter(Boolean);
                            const printWindow = window.open('', '_blank');

                            const bodyContent = sortedImageList.length > 0 ? `
                              <h2>주식 자동화 영상 스토리보드 대본</h2>
                              <div class="meta">
                                <div><strong>작업 ID:</strong> Job #${id}</div>
                                <div><strong>영상 주제:</strong> ${job.title}</div>
                                <div><strong>선택 키워드:</strong> ${job.keyword || ''}</div>
                              </div>
                              ${sortedImageList.map(img => `
                                <div class="scene-block" style="margin-bottom: 25px; page-break-inside: avoid; border-bottom: 1px solid #eeeeee; padding-bottom: 15px; display: flex; gap: 20px;">
                                  <div style="width: 240px; flex-shrink: 0;">
                                    <img src="${window.location.origin}/api/files/download?path=${encodeURIComponent(img.image_path)}&token=${token}" style="width: 240px; height: 135px; object-fit: cover; border: 1px solid #cccccc; border-radius: 4px;" />
                                  </div>
                                  <div style="flex: 1;">
                                    <div style="font-weight: bold; color: #0d1b2a; font-size: 13px; margin-bottom: 6px;">씬 #${img.index} (${fmt(img.start)} ~ ${fmt(img.start + img.duration)})</div>
                                    <p style="margin-top: 0; margin-bottom: 0; text-align: justify; font-size: 13px; line-height: 1.8;">
                                      ${img.prompt_ko || img.text || img.prompt || ''}
                                    </p>
                                  </div>
                                </div>
                              `).join('')}
                            ` : `
                              <h2>주식 자동화 영상 스크립트</h2>
                              <div class="meta">
                                <div><strong>작업 ID:</strong> Job #${id}</div>
                                <div><strong>영상 주제:</strong> ${job.title}</div>
                                <div><strong>선택 키워드:</strong> ${job.keyword || ''}</div>
                              </div>
                              ${cleanParas.map(p => `<p>${p}</p>`).join('')}
                            `;

                            const htmlContent = `
                              <html>
                              <head>
                                <title>스크립트 인쇄 - Job #${id}</title>
                                <style>
                                  body { font-family: 'Malgun Gothic', 'Nanum Gothic', Arial, sans-serif; padding: 40px; line-height: 1.8; color: #333; }
                                  h2 { border-bottom: 2px solid #0d1b2a; padding-bottom: 10px; margin-bottom: 20px; color: #0d1b2a; }
                                  .meta { margin-bottom: 30px; font-size: 13px; color: #555; background: #f8f9fa; padding: 15px; border-left: 4px solid #00d4ff; }
                                  .meta div { margin-bottom: 6px; }
                                  p { margin-top: 0; margin-bottom: 16px; text-align: justify; font-size: 14px; text-justify: inter-word; }
                                </style>
                              </head>
                              <body>
                                ${bodyContent}
                                <script>window.onload = function() { window.print(); window.close(); }</script>
                              </body>
                              </html>
                            `;
                            printWindow.document.write(htmlContent);
                            printWindow.document.close();
                          }} className="bg-navy-700 text-gray-200 hover:text-slate-900 text-xs px-2.5 py-1.5 rounded border border-slate-300 transition flex items-center gap-1">
                            <Printer size={12}/>PDF 인쇄
                          </button>
                        </div>

                        {job.status === 'SCRIPT_PENDING' && (
                          <button onClick={() => {
                            if (isEditingScript) {
                              setIsEditingScript(false);
                            } else {
                              setEditedScriptText(scriptData.script || '');
                              setIsEditingScript(true);
                            }
                          }} className="flex items-center gap-1 text-xs bg-accent-gold/20 text-accent-gold border border-accent-gold/30 px-2.5 py-1.5 rounded hover:bg-accent-gold/30 transition">
                            <Edit size={12}/>{isEditingScript ? '편집 취소' : '스크립트 수정'}
                          </button>
                        )}

                        <button onClick={() => setExpandedScript(!expandedScript)}
                          className="text-sm text-accent-cyan flex items-center gap-1 hover:underline">
                          {expandedScript ? '접기' : '전체 보기'}
                          {expandedScript ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}
                        </button>
                      </div>
                    </div>

                    {isEditingScript ? (
                      <div className="space-y-3">
                        <textarea
                          value={editedScriptText}
                          onChange={e => setEditedScriptText(e.target.value)}
                          className="w-full bg-navy-700 border border-slate-300 rounded-xl p-3.5 text-sm text-slate-900 focus:outline-none focus:ring-1 focus:ring-accent-cyan font-mono resize-y"
                          rows={12}
                        />
                        <div className="flex justify-end gap-2">
                          <button
                            onClick={() => setIsEditingScript(false)}
                            className="bg-navy-700 text-navy-400 hover:text-slate-900 text-sm px-4 py-2 rounded transition"
                          >
                            취소
                          </button>
                          <button
                            onClick={() => saveScriptMut.mutate(editedScriptText)}
                            disabled={saveScriptMut.isPending}
                            className="flex items-center gap-1.5 bg-accent-green text-navy-950 text-sm font-semibold px-4 py-2 rounded hover:opacity-90 disabled:opacity-50 transition"
                          >
                            <Save size={14}/>
                            {saveScriptMut.isPending ? '저장 중...' : '수정 저장 및 확정'}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div>
                        {scriptViewMode === 'paragraphs' ? (
                          <div className="space-y-4 max-h-[400px] overflow-y-auto pr-2 bg-slate-100/30 rounded-xl p-4 border border-slate-200/50">
                            {(() => {
                              const sections = scriptData.sections || [];
                              if (sections.length > 0) {
                                return sections.map((sec, idx) => (
                                  <p key={idx} className="text-sm text-gray-200 leading-relaxed text-justify mb-2">
                                    {sec.content}
                                  </p>
                                ));
                              } else {
                                return (expandedScript ? (scriptData.script || '') : ((scriptData.script || '').slice(0, 400) + ((scriptData.script || '').length > 400 ? '...' : '')))
                                  .split(/\n+/)
                                  .filter(Boolean)
                                  .map((para, idx) => (
                                    <p key={idx} className="text-sm text-gray-200 leading-relaxed text-justify">
                                      {para}
                                    </p>
                                  ));
                              }
                            })()}
                          </div>
                        ) : scriptViewMode === 'mixed' && sortedImageList.length > 0 ? (
                          <div className="space-y-4 max-h-[400px] overflow-y-auto pr-2 bg-slate-100/30 rounded-xl p-4 border border-slate-200/50">
                            {sortedImageList.map((img, idx) => (
                              <div key={img.index || idx} className="flex gap-4 border-b border-navy-800 pb-3 last:border-0 last:pb-0">
                                <div className="w-28 aspect-video bg-navy-700 rounded overflow-hidden border border-slate-300 flex-shrink-0">
                                  <img
                                    src={`/api/files/download?path=${encodeURIComponent(img.image_path)}&token=${token}&salt=${imageSalt}`}
                                    alt={`씬 ${img.index}`}
                                    className="w-full h-full object-cover"
                                    onError={e => { e.target.style.display = 'none' }}
                                  />
                                </div>
                                <div className="flex-1">
                                  <div className="text-xs font-bold text-accent-cyan mb-1 flex items-center gap-1.5">
                                    <span>씬 #{img.index}</span>
                                    <span className="text-navy-400 font-normal">
                                      {fmt(img.start)} ~ {fmt(img.start + img.duration)}
                                    </span>
                                  </div>
                                  <p className="text-sm text-gray-200 leading-relaxed text-justify">
                                    {img.prompt_ko || img.text || img.prompt || '(내용 없음)'}
                                  </p>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div>
                            {scriptData.sections && scriptData.sections.length > 0 ? (
                              <div className="space-y-2">
                                {scriptData.sections.map((sec, i) => (
                                  <div key={i} className="bg-slate-50 rounded-xl p-3.5">
                                    <div className="text-sm font-semibold text-accent-gold mb-1">{sec.title}</div>
                                    <p className="text-sm text-gray-200 leading-relaxed">
                                      {expandedScript ? sec.content : (sec.content?.slice(0, 80) + (sec.content?.length > 80 ? '...' : ''))}
                                    </p>
                                  </div>
                                ))}
                              </div>
                            ) : scriptData.script ? (
                              <div className="bg-slate-50 rounded-xl p-3.5">
                                <p className="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">
                                  {expandedScript ? scriptData.script : (scriptData.script.slice(0, 300) + (scriptData.script.length > 300 ? '...' : ''))}
                                </p>
                              </div>
                            ) : null}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {step.key === 'tts' && ttsInfo && (
                  <div className="px-5 pb-4 border-t border-slate-200">
                    <div className="flex items-center gap-2 mt-3 mb-2.5">
                      <Music size={15} className="text-accent-cyan"/>
                      <span className="text-sm text-navy-400">
                        {ttsInfo.total_duration ? `${(ttsInfo.total_duration/60).toFixed(1)}분` : ''}
                        {ttsInfo.chunks && ` · ${ttsInfo.chunks.length}개 자막 청크`}
                        {ttsInfo.used_gtts === true && <span className="ml-2 text-accent-green">✓ gTTS 실제 음성</span>}
                      </span>
                    </div>
                    {ttsInfo.audio_path && (
                      <audio controls className="w-full h-9" style={{ filter: 'invert(0.9)' }}>
                        <source src={`/api/files/download?path=${encodeURIComponent(ttsInfo.audio_path)}&token=${token}`} type="audio/mpeg"/>
                      </audio>
                    )}
                  </div>
                )}

                {step.key === 'longform' && outputQc && (
                  <div className="px-5 pb-4 border-t border-slate-200">
                    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-100/40 p-3.5">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-white">최종 출력 QC</span>
                        <span className={`text-xs px-2.5 py-1 rounded-full ${outputQc.passed ? 'bg-accent-green/10 text-accent-green' : 'bg-red-500/10 text-red-300'}`}>
                          {outputQc.passed ? '통과' : '검토 필요'} · {outputQc.score ?? 0}점
                        </span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-navy-400">
                        {Object.entries(outputQc.checks || {}).map(([name, check]) => (
                          <div key={name} className="rounded bg-white/70 px-2.5 py-2">
                            <span className={check.passed ? 'text-accent-green' : 'text-red-300'}>{check.passed ? '✓' : '✕'}</span>
                            <span className="ml-1.5">{name.replaceAll('_', ' ')}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

                {(step.key === 'images' && sortedImageList.length > 0) && (() => {
                  const isLongform = false
                  const curPage = isLongform ? longformScenePage : scenePage
                  const setCurPage = isLongform ? setLongformScenePage : setScenePage
                  const pageCount = isLongform ? longformScenePageCount : scenePageCount
                  const displayList = isLongform ? pagedLongformImageList : pagedImageList
                  return (
                  <div className="px-5 pb-4 border-t border-slate-200">
                    <div className="flex items-center justify-between mt-3 mb-3">
                      <div className="flex items-center gap-2">
                        <ImageIcon size={15} className="text-accent-cyan"/>
                        <span className="text-sm text-navy-400">
                          {sortedImageList.length}개 씬 이미지 (AI 일러스트 기반)
                        </span>
                      </div>
                      {['IMAGES_PENDING', 'PREVIEW_PENDING', 'READY'].includes(job.status) && (
                        <span className="text-xs bg-accent-cyan/10 text-accent-cyan px-2.5 py-1 rounded-full font-semibold">
                          수정/재생성 활성화됨
                        </span>
                      )}
                    </div>

                    <div className="space-y-3 max-h-[500px] overflow-y-auto pr-2 bg-slate-100/30 rounded-xl p-3 border border-slate-200/50">
                      {displayList.map((img, i) => {
                        const isEditingThis = editingSceneIndex === img.index;
                        const isRegeneratingThis = regenImageMut.isPending;
                        const qualityScore = img.qualityScore ?? img.quality_score;
                        const qualityFlags = img.qualityFlags ?? img.quality_flags ?? [];
                        const retryRecommended = img.retryRecommended ?? img.retry_recommended;
                        const imageProfile = img.imageProfile ?? img.image_profile;
                        const useKling = img.useKling ?? img.use_kling;
                        const sceneStart = Number(img.start ?? img.start_seconds ?? 0)
                        const sceneDuration = Number(img.duration ?? img.estimated_seconds ?? 0)
                        const hasVerifiedFactOverlay = Boolean((img.v5_verified_overlays || img.verified_facts || []).length)
                        const isKlingEligible = klingMotionPolicy.enabled !== false
                          && sceneStart + sceneDuration <= klingMotionWindowEnd
                          && !hasVerifiedFactOverlay

                        return (
                          <div key={img.index || i} className="flex gap-4 bg-white/40 border border-slate-200/60 rounded-xl p-3.5 hover:border-slate-300 transition">
                            <div className="w-40 aspect-video bg-navy-700 rounded overflow-hidden border border-slate-300 flex-shrink-0 relative">
                              <img
                                src={`/api/files/download?path=${encodeURIComponent(img.image_path)}&token=${token}&salt=${imageSalt}`}
                                alt={`씬 ${img.index}`}
                                className="w-full h-full object-cover"
                                onError={e => { e.target.style.display = 'none' }}
                              />
                            </div>

                            <div className="flex-1 flex flex-col justify-between">
                              <div>
                                <div className="flex items-center justify-between">
                                  <div className="text-xs font-semibold text-accent-cyan flex items-center gap-1.5">
                                    <span className="bg-accent-cyan/10 px-2 py-0.5 rounded font-bold">씬 #{img.index}</span>
                                    <span className="text-navy-400 font-normal flex items-center gap-0.5">
                                      <Clock size={12}/>
                                      {fmt(img.start)} ~ {fmt(img.start + img.duration)} ({img.duration?.toFixed(1)}초)
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-1.5">
                                    {imageProfile?.tier && (
                                      <span className={`text-xs px-2 py-0.5 rounded border ${imageProfile.tier === 'pro' ? 'bg-accent-gold/10 text-accent-gold border-accent-gold/30' : 'bg-navy-700 text-navy-400 border-slate-300'}`}>
                                        {imageProfile.tier === 'pro' ? 'Gemini Pro 2K' : '프로필 오류'}
                                      </span>
                                    )}
                                    {qualityScore !== undefined && (
                                      <span
                                        className={`text-xs px-2 py-0.5 rounded border ${retryRecommended ? 'bg-accent-gold/10 text-accent-gold border-accent-gold/30' : 'bg-accent-green/10 text-accent-green border-accent-green/30'}`}
                                        title={img.semanticReason || img.semantic_reason || (qualityFlags.length ? `품질 확인: ${qualityFlags.join(', ')}` : '기술 품질 확인 통과')}
                                      >
                                        {retryRecommended ? '검토 권장' : '품질 확인'} {qualityScore}점
                                      </span>
                                    )}
                                    <span className="text-xs bg-navy-700 text-navy-400 px-2 py-0.5 rounded border border-slate-300">
                                      구분: {img.section}
                                    </span>
                                  </div>
                                </div>

                                <div className="mt-2.5">
                                  {isEditingThis ? (
                                    <div className="space-y-2">
                                      <label className="block text-[11px] text-slate-500">원본 한국어 문장 · 텍스트 반영 이미지 재생성에 사용</label>
                                      <textarea
                                        id={`scene-edit-${img.index}`}
                                        value={editingSceneText}
                                        onChange={e => setEditingSceneText(e.target.value)}
                                        className="w-full bg-navy-700 border border-slate-300 rounded p-2.5 text-sm text-slate-900 focus:outline-none focus:ring-1 focus:ring-accent-cyan resize-none"
                                        rows={2}
                                      />
                                      <div>
                                        <div className="text-[11px] font-semibold text-navy-400 mb-1">현재 생성된 영어 이미지 프롬프트</div>
                                        <p className="max-h-24 overflow-y-auto rounded border border-slate-200 bg-slate-50/40 p-2 text-xs leading-relaxed text-navy-300 font-mono">
                                          {img.prompt_en || img.prompt || '아직 생성된 영어 프롬프트가 없습니다.'}
                                        </p>
                                      </div>
                                    </div>
                                  ) : (
                                    <div className="space-y-1.5">
                                      <div className="text-[11px] font-semibold text-navy-400">원본 한국어 문장</div>
                                      <p className="text-sm text-gray-200 leading-relaxed text-justify line-clamp-3">
                                        {img.text || img.prompt_ko || img.prompt || '(내용 없음)'}
                                      </p>
                                      {img.prompt_en && (
                                        <div className="mt-1">
                                          <div className="text-[11px] font-semibold text-navy-400 mb-1">생성된 영어 이미지 프롬프트</div>
                                          <button
                                            onClick={() => setShowEngPrompt(prev => ({ ...prev, [img.index]: !prev[img.index] }))}
                                            className="text-xs text-accent-cyan hover:underline flex items-center gap-1"
                                          >
                                            {showEngPrompt[img.index] ? '영문 AI 프롬프트 접기' : '영문 AI 프롬프트 보기'}
                                          </button>
                                          {showEngPrompt[img.index] && (
                                            <p className="text-xs text-navy-400 bg-slate-100/40 p-2 rounded border border-slate-200 font-mono mt-1 leading-relaxed">
                                              {img.prompt_en}
                                            </p>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  )}
                                </div>
                              </div>

                              <div className="flex justify-end gap-2 mt-2.5">
                                {!isEditingThis && isKlingEligible && ['IMAGES_PENDING', 'PREVIEW_PENDING', 'READY'].includes(job.status) && (
                                  <button
                                    onClick={() => sceneKlingMut.mutate({ index: img.index, enabled: !useKling })}
                                    disabled={sceneKlingMut.isPending}
                                    className={`flex items-center gap-1 text-xs border px-2.5 py-1.5 rounded transition disabled:opacity-50 ${useKling ? 'bg-purple-500/20 text-purple-200 border-purple-400/60' : 'bg-navy-700 text-gray-200 hover:text-slate-900 border-slate-300'}`}
                                    title={`직접 선택하면 초반 ${klingMotionWindowEnd}초 구간 안에서 선택한 씬만 Kling 영상화합니다.`}
                                  >
                                    <Zap size={12}/>
                                    {useKling ? 'Kling 선택됨' : '이 씬 Kling'}
                                  </button>
                                )}
                                {isEditingThis ? (
                                  <>
                                    <button
                                      onClick={() => {
                                        const ta = document.getElementById(`scene-edit-${img.index}`);
                                        if (!ta) return;
                                        const pos = ta.selectionStart;
                                        const val = ta.value;
                                        if (pos <= 0 || pos >= val.length) {
                                          alert("텍스트 입력창에서 분할할 커서 위치를 클릭한 뒤 눌러주세요.");
                                          return;
                                        }
                                        const p1 = val.substring(0, pos).trim();
                                        const p2 = val.substring(pos).trim();
                                        if (!p1 || !p2) {
                                          alert("커서 앞뒤로 텍스트가 존재해야 분할이 가능합니다.");
                                          return;
                                        }
                                        if (confirm("이 위치에서 씬을 두 개로 분할하시겠습니까?")) {
                                          splitSceneMut.mutate({ index: img.index, part1: p1, part2: p2 });
                                        }
                                      }}
                                      disabled={isRegeneratingThis || splitSceneMut.isPending}
                                      className="flex items-center gap-1 bg-red-500 text-white text-xs font-semibold px-2.5 py-1.5 rounded hover:opacity-90 disabled:opacity-50 transition"
                                      title="커서가 있는 위치를 기준으로 씬을 2개로 분할합니다."
                                    >
                                      {splitSceneMut.isPending ? <Loader size={12} className="animate-spin"/> : <Scissors size={12}/>}
                                      씬 분할
                                    </button>
                                    <button
                                      onClick={() => setEditingSceneIndex(null)}
                                      disabled={isRegeneratingThis || splitSceneMut.isPending}
                                      className="bg-navy-700 text-navy-400 hover:text-slate-900 text-xs px-3 py-1.5 rounded transition"
                                    >
                                      취소
                                    </button>
                                    <button
                                      onClick={() => regenImageMut.mutate({
                                        index: img.index,
                                        text: editingSceneText,
                                        section: img.section,
                                        mode: 'image_only'
                                      })}
                                      disabled={isRegeneratingThis || splitSceneMut.isPending}
                                      className="flex items-center gap-1 bg-accent-cyan text-navy-950 text-xs font-semibold px-2.5 py-1.5 rounded hover:opacity-90 disabled:opacity-50 transition"
                                      title="원본 문장과 검토한 영문 프롬프트를 그대로 유지하고 이미지만 다시 생성합니다."
                                    >
                                      {isRegeneratingThis ? <Loader size={12} className="animate-spin"/> : <Save size={12}/>}
                                      이미지만 재생성
                                    </button>
                                    <button
                                      onClick={() => regenImageMut.mutate({
                                        index: img.index,
                                        text: editingSceneText,
                                        section: img.section,
                                        mode: 'text_and_image'
                                      })}
                                      disabled={isRegeneratingThis}
                                      className="flex items-center gap-1 bg-accent-green text-navy-950 text-xs font-semibold px-2.5 py-1.5 rounded hover:opacity-90 disabled:opacity-50 transition"
                                      title="수정한 원본 한국어 문장으로 새 영문 이미지 프롬프트를 만들고 이미지를 재생성합니다."
                                    >
                                      {isRegeneratingThis ? <Loader size={12} className="animate-spin"/> : <Save size={12}/>}
                                      텍스트 반영 이미지 재생성
                                    </button>
                                  </>
                                ) : (
                                  ['IMAGES_PENDING', 'PREVIEW_PENDING', 'READY'].includes(job.status) && (
                                    <div className="flex gap-2">
                                      <button
                                        onClick={() => regenImageMut.mutate({
                                          index: img.index,
                                          text: img.text || img.prompt_ko || '',
                                          subtitleText: img.subtitle_text || img.subtitleText || '',
                                          section: img.section,
                                          mode: 'image_only',
                                        })}
                                        disabled={isRegeneratingThis}
                                        className="flex items-center gap-1 text-xs bg-accent-cyan text-navy-950 font-semibold px-2.5 py-1.5 rounded transition disabled:opacity-50"
                                        title="검토한 영문 프롬프트와 원본 문장은 유지하고 이미지만 다시 생성합니다."
                                      >
                                        {isRegeneratingThis ? <Loader size={12} className="animate-spin"/> : <ImageIcon size={12}/>} 이미지만 재생성
                                      </button>
                                      <button
                                        onClick={() => {
                                          setEditingSceneIndex(img.index);
                                          setEditingSceneText(img.text || img.prompt_ko || img.prompt || '');
                                        }}
                                        className="flex items-center gap-1 text-xs bg-navy-700 text-gray-200 hover:text-slate-900 border border-slate-300 px-2.5 py-1.5 rounded transition"
                                      >
                                        <Edit size={12}/>
                                        원문 편집
                                      </button>
                                    </div>
                                  )
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3 text-xs text-navy-400">
                      <span>씬 {((curPage - 1) * 10) + 1}–{Math.min(curPage * 10, sortedImageList.length)} / {sortedImageList.length}</span>
                      <div className="flex items-center gap-1">
                        <button onClick={() => setCurPage(1)} disabled={curPage === 1} className="px-2 py-1 border border-slate-300 rounded disabled:opacity-40">«</button>
                        <button onClick={() => setCurPage(p => Math.max(1, p - 1))} disabled={curPage === 1} className="px-2 py-1 border border-slate-300 rounded disabled:opacity-40">‹</button>
                        {Array.from({ length: pageCount }, (_, i) => i + 1)
                          .filter(page => page === 1 || page === pageCount || Math.abs(page - curPage) <= 1)
                          .map((page, index, pages) => (
                            <span key={page} className="flex items-center gap-1">
                              {index > 0 && page - pages[index - 1] > 1 && <span className="px-1">…</span>}
                              <button onClick={() => setCurPage(page)} className={`min-w-7 px-2 py-1 rounded border ${page === curPage ? 'bg-accent-cyan text-navy-950 border-accent-cyan font-bold' : 'border-slate-300 hover:text-slate-900'}`}>{page}</button>
                            </span>
                          ))}
                        <button onClick={() => setCurPage(p => Math.min(pageCount, p + 1))} disabled={curPage === pageCount} className="px-2 py-1 border border-slate-300 rounded disabled:opacity-40">›</button>
                        <button onClick={() => setCurPage(pageCount)} disabled={curPage === pageCount} className="px-2 py-1 border border-slate-300 rounded disabled:opacity-40">»</button>
                      </div>
                    </div>
                  </div>
                  )
                })()}
              </div>
            )
          })}
        </div>

        <div className="space-y-4">
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-card">
            <h3 className="text-base font-semibold mb-3">작업 정보</h3>
            <div className="space-y-3 text-sm">
              <InfoRow label="상태" value={<StatusBadge status={job.status} small/>}/>
              <InfoRow label="카테고리" value={formatCategory(job.category)}/>
              <InfoRow label="목표 길이" value={`${job.longformTargetMinutes}분`}/>
              <InfoRow label="진행 방식" value={<span className={`text-sm px-2.5 py-1 rounded-full border ${AUTONOMY_STYLE[job.autonomy]}`}>{formatAutonomy(job.autonomy)}</span>}/>
              {job.keyword && <InfoRow label="확정 키워드" value={<span className="text-accent-cyan text-sm">{job.keyword}</span>}/>}
            </div>
          </div>

          {costs && (
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-card">
              <h3 className="text-base font-bold text-slate-900 mb-3">비용 상세</h3>
              
              {/* provider별 그룹 합산 표시 */}
              {costs.groupedItems && costs.groupedItems.length > 0 && (
                <div className="space-y-2 mb-4">
                  {costs.groupedItems.map((group, i) => (
                    <div key={i} className="flex items-center justify-between text-sm py-1.5 border-b border-slate-100 last:border-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-slate-800">{group.provider}</span>
                        <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-600 font-medium">
                          {group.count}건
                        </span>
                      </div>
                      <span className="font-bold text-slate-900">
                        {group.currency === 'KRW'
                          ? `₩${Math.round(Number(group.totalAmount || 0)).toLocaleString()}`
                          : `$${parseFloat(group.totalAmount || 0).toFixed(3)}`}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* 누적 / 예산 요약 */}
              <div className="space-y-2 text-sm pt-3 border-t border-slate-200 mb-3">
                <InfoRow label="누적" value={`₩${Math.round(Number(costs.currentTotal || 0)).toLocaleString()}`}/>
                <InfoRow label="예산" value={costs.budgetCap ? `₩${Math.round(Number(costs.budgetCap)).toLocaleString()}` : '무제한'}/>
              </div>

              {costs.budgetCap && (
                <div className="mb-4">
                  <div className="h-2 bg-slate-100 rounded-full overflow-hidden border border-slate-200">
                    <div className="h-full bg-indigo-600 rounded-full transition-all" style={{width:`${Math.min(100,(costs.currentTotal/costs.budgetCap)*100)}%`}}/>
                  </div>
                </div>
              )}

              {/* 개별 호출 내역 상세 보기 아코디언 토글 */}
              {costs.items && costs.items.length > 0 && (
                <div className="pt-2 border-t border-slate-200">
                  <button
                    type="button"
                    onClick={() => setShowCostDetails(!showCostDetails)}
                    className="w-full flex items-center justify-between text-xs font-semibold text-slate-600 hover:text-slate-900 py-1.5 transition"
                  >
                    <span>개별 호출 내역 ({costs.items.length}건)</span>
                    <span className="text-slate-400">{showCostDetails ? '▲ 접기' : '▼ 펼치기'}</span>
                  </button>
                  {showCostDetails && (
                    <div className="mt-2 space-y-2 pt-2 border-t border-slate-100 max-h-60 overflow-y-auto pr-1">
                      {costs.items.map((item, i) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span className="text-slate-500 truncate max-w-[160px]" title={item.note || item.provider}>
                            {item.provider} <span className="text-[10px] text-slate-400">({item.note || '완료'})</span>
                          </span>
                          <span className="font-medium text-slate-700">
                            {item.currency === 'KRW'
                              ? `₩${Math.round(Number(item.amount || 0)).toLocaleString()}`
                              : `$${parseFloat(item.amount || 0).toFixed(3)}`}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {approvals.length > 0 && (
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-card">
              <h3 className="text-base font-semibold mb-3">게이트 이력</h3>
              <div className="space-y-2.5">
                {approvals.map((a,i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <span className="text-navy-400">{a.gate}</span>
                    <span className={a.result==='REJECTED'?'text-accent-red':a.result==='AUTO_APPROVED'?'text-accent-cyan':'text-accent-green'}>
                      {a.result==='AUTO_APPROVED'?'⚡ 자동':a.result==='REJECTED'?'✗ 거부':'✓ 승인'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {gateModal && (
        <GateModal gate={gateModal.gate} step={gateModal.step}
          onApprove={c => approveMut.mutate({gate:gateModal.gate,comment:c})}
          onReject={c => rejectMut.mutate({gate:gateModal.gate,comment:c})}
          onClose={() => setGateModal(null)}
          loading={approveMut.isPending||rejectMut.isPending}/>
      )}
    </Layout>
  )
}

function StepIcon({status,idx}) {
  if (status==='done') return <CheckCircle className="text-accent-green flex-shrink-0" size={22}/>
  if (status==='active') return <Loader className="text-accent-cyan animate-spin flex-shrink-0" size={22}/>
  return <div className="w-6 h-6 rounded-full border border-slate-300 flex items-center justify-center text-sm text-navy-400 flex-shrink-0">{idx}</div>
}

function StatusBadge({status,small}) {
  const M={
    READY:{l:'완료',c:'bg-accent-green/20 text-accent-green'},
    PUBLISHED:{l:'업로드됨',c:'bg-accent-green/20 text-accent-green'},
    ASSEMBLING:{l:'조립중',c:'bg-accent-cyan/20 text-accent-cyan'},
    FAILED:{l:'오류',c:'bg-accent-red/20 text-accent-red'},
    BUDGET_BLOCKED:{l:'예산초과',c:'bg-accent-red/20 text-accent-red'},
    DRAFT:{l:'초안',c:'bg-navy-700 text-navy-400'},
    KEYWORD_PENDING:{l:'키워드',c:'bg-accent-cyan/10 text-accent-cyan'},
    SCRIPT_PENDING:{l:'스크립트',c:'bg-accent-cyan/10 text-accent-cyan'},
    TTS_PENDING:{l:'TTS',c:'bg-accent-cyan/10 text-accent-cyan'},
    IMAGES_PENDING:{l:'이미지',c:'bg-accent-cyan/10 text-accent-cyan'},
    PREVIEW_PENDING:{l:'미리보기 대기',c:'bg-accent-gold/20 text-accent-gold'},
  }
  const c=M[status]||{l:status,c:'bg-navy-700 text-navy-400'}
  return <span className={`${small?'text-sm px-3 py-1':'text-base px-4 py-1.5'} rounded-full font-medium ${c.c}`}>{c.l}</span>
}

function InfoRow({label,value}) {
  return <div className="flex items-center justify-between gap-2"><span className="text-navy-400 flex-shrink-0">{label}</span><span className="font-medium text-right">{value}</span></div>
}

function GateModal({gate,step,onApprove,onReject,onClose,loading}) {
  const [comment,setComment]=useState('')
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl p-6 w-full max-w-sm border border-slate-200 shadow-2xl">
        <h3 className="font-bold text-base mb-2">{step.label} 검토</h3>
        <p className="text-sm text-navy-400 mb-4">결과를 확인하고 승인 또는 거부하세요.</p>
        <textarea value={comment} onChange={e=>setComment(e.target.value)} placeholder="코멘트 (선택사항)" rows={3}
          className="w-full bg-navy-700 border border-slate-200 rounded-xl px-3.5 py-2.5 text-sm text-slate-900 mb-4 focus:outline-none focus:ring-1 focus:ring-accent-cyan resize-none"/>
        <div className="flex gap-3">
          <button onClick={()=>onReject(comment)} disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 bg-accent-red/20 text-accent-red border border-accent-red/30 rounded-xl py-2.5 text-sm hover:bg-accent-red/30 disabled:opacity-50 transition">
            <ThumbsDown size={15}/>거부
          </button>
          <button onClick={()=>onApprove(comment)} disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 bg-accent-green/20 text-accent-green border border-accent-green/30 rounded-xl py-2.5 text-sm hover:bg-accent-green/30 disabled:opacity-50 transition">
            <ThumbsUp size={15}/>승인
          </button>
        </div>
        <button onClick={onClose} className="w-full mt-2 text-navy-400 text-sm hover:text-gray-200 transition">닫기</button>
      </div>
    </div>
  )
}
