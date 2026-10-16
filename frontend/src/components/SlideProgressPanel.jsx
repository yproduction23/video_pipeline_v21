import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader, CheckCircle, AlertTriangle, RotateCcw, ZoomIn, X } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import apiClient from '../api/client'
import { jobsApi } from '../api/jobs'

const TYPE_LABEL = {
  slide_stock_video: '스톡 영상',
  slide_ai_background: 'AI 배경',
  slide_kling_video: 'Kling',
  slide_article_card: '기사 카드',
  slide_stat_chart: '수치 그래프',
  slide_key_text: '강조 텍스트',
  slide_bullet_list: '목록 인포',
  slide_equation: '계산식',
  slide_board: '설명 장면',
  slide_big_number: '큰 숫자',
  slide_compare_numbers: '수치 비교',
  slide_flow: '흐름도',
}

// 2026-10-15 사용자 요청: 씬별 생성을 누르면 장면이 몇 개로 나뉘었는지 보이고,
// 만들어진 장면이 하나씩 쌓여 보여야 한다. 썸네일은 JWT가 필요한 API라 fetch→Blob URL로 받는다.
export default function SlideProgressPanel({ jobId, running }) {
  const [thumbs, setThumbs] = useState({})
  const [regenerating, setRegenerating] = useState(false)
  const [actionError, setActionError] = useState('')
  const [viewer, setViewer] = useState(null) // { index, kind: 'image'|'video', url } | { index, loading: true }
  const requested = useRef(new Set())
  const queryClient = useQueryClient()

  const { data } = useQuery({
    queryKey: ['slide-progress', jobId],
    queryFn: () => apiClient.get(`/jobs/${jobId}/images/progress`).then(r => r.data),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return running || status === 'running' || status === 'preparing' ? 3000 : false
    },
  })

  const scenes = data?.scenes || []
  useEffect(() => {
    scenes.forEach((scene) => {
      // 같은 번호 장면을 다시 생성하면 파일 경로·상태가 바뀐다 — 번호만으로 캐시하면 이전 썸네일이 남는다.
      const key = `${scene.index}:${scene.status}:${scene.image_path || ''}`
      if (!scene.has_thumb || requested.current.has(key)) return
      requested.current.add(key)
      apiClient
        .get(`/jobs/${jobId}/images/progress/thumb/${scene.index}`, { responseType: 'blob' })
        .then(res => setThumbs(prev => {
          if (prev[scene.index]) URL.revokeObjectURL(prev[scene.index])
          return { ...prev, [scene.index]: URL.createObjectURL(res.data) }
        }))
        .catch(() => requested.current.delete(key))
    })
  }, [scenes, jobId])

  useEffect(() => () => {
    Object.values(thumbs).forEach(url => URL.revokeObjectURL(url))
    // 언마운트 시에만 정리한다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 정리 UI: 실패·경고 장면만 골라 다시 만든다. 보류(needs_review) 장면은 재시도 상한을
  // 사람이 승인해야 열리므로 승인 호출을 먼저 시도한다(해당 없으면 무시).
  const problemIndexes = scenes
    .filter(s => s.status === 'failed' || (s.status === 'done' && ((s.flags || []).length > 0 || s.error)))
    .map(s => s.index)

  // 돋보기: 완성된 장면(영상이 있으면 영상, 아니면 이미지)을 원본 크기로 크게 본다.
  const openViewer = async (scene) => {
    setViewer({ index: scene.index, loading: true })
    try {
      const res = await apiClient.get(`/jobs/${jobId}/images/progress/scene/${scene.index}`, { responseType: 'blob' })
      const kind = String(res.data.type || '').startsWith('video') ? 'video' : 'image'
      setViewer({ index: scene.index, kind, url: URL.createObjectURL(res.data) })
    } catch {
      setViewer(null)
      setActionError('확대할 파일을 불러오지 못했습니다.')
    }
  }
  const closeViewer = () => {
    if (viewer?.url) URL.revokeObjectURL(viewer.url)
    setViewer(null)
  }

  const regenerate = async (indexes) => {
    if (!indexes.length || regenerating) return
    setRegenerating(true)
    setActionError('')
    try {
      for (const index of indexes) {
        try { await jobsApi.approveSceneRetry(jobId, index, false) } catch { /* 승인 대상 아님 */ }
      }
      await jobsApi.generateImages(jobId, indexes)
      queryClient.invalidateQueries(['job', String(jobId)])
      queryClient.invalidateQueries(['assets', String(jobId)])
      queryClient.invalidateQueries(['slide-progress', jobId])
    } catch (e) {
      setActionError(e.response?.data?.message || e.message || '다시 생성에 실패했습니다.')
    } finally {
      setRegenerating(false)
    }
  }

  if (!data || data.status === 'none') return null
  const total = data.total || 0
  const done = data.done || 0
  const percent = total > 0 ? Math.round((done / total) * 100) : 0
  const preparing = data.status === 'preparing'

  // 확정(자동 검수 통과) 위, 검토 필요 아래, 아직 만드는 중인 장면은 맨 아래.
  const isReview = (s) => s.status === 'failed' || (s.status === 'done' && ((s.flags || []).length > 0 || s.error))
  const sections = [
    { key: 'confirmed', title: '확정 씬', tone: 'text-emerald-700', hint: '자동 검수를 통과한 장면',
      items: scenes.filter(s => s.status === 'done' && !isReview(s)) },
    { key: 'review', title: '검토 필요 씬', tone: 'text-amber-700', hint: '실패했거나 경고가 있는 장면 — 확인 후 다시 생성',
      items: scenes.filter(isReview) },
    { key: 'pending', title: '생성 대기·진행 중', tone: 'text-slate-600', hint: '',
      items: scenes.filter(s => s.status === 'pending') },
  ]

  const renderScene = (scene) => {
    const failed = scene.status === 'failed'
    const flagged = (scene.flags || []).length > 0 || scene.error
    return (
      <div key={scene.index}
        className={`rounded-lg border p-1.5 text-[11px] bg-white ${failed ? 'border-rose-400' : flagged ? 'border-amber-400' : 'border-slate-200'}`}>
        <div className="relative aspect-video rounded bg-slate-100 overflow-hidden flex items-center justify-center group">
          {scene.status !== 'pending' && scene.has_thumb && (
            <button type="button" onClick={() => openViewer(scene)} title="크게 보기"
              className="absolute right-1 top-1 z-10 rounded-full bg-black/60 p-1 text-white opacity-90 hover:bg-black/80">
              <ZoomIn size={14} />
            </button>
          )}
          {thumbs[scene.index]
            ? <img src={thumbs[scene.index]} alt={`장면 ${scene.index + 1}`} className="w-full h-full object-cover" />
            : scene.status === 'pending'
              ? <Loader size={14} className="animate-spin text-slate-400" />
              : failed ? <AlertTriangle size={16} className="text-rose-500" /> : null}
        </div>
        <div className="mt-1 flex items-center justify-between font-bold text-slate-800">
          <span>#{scene.index + 1}</span>
          <span className="text-slate-500 font-semibold">
            {scene.status === 'pending' ? '대기' : failed ? '실패'
              : [TYPE_LABEL[scene.visual_type] || scene.visual_type, scene.overlay_type && scene.visual_type !== 'slide_article_card' && scene.visual_type !== 'slide_stat_chart' ? '+오버레이' : null].filter(Boolean).join(' ')}
          </span>
        </div>
        <p className="mt-0.5 line-clamp-2 text-slate-500 leading-snug">{scene.text}</p>
        {(failed || flagged) && scene.status !== 'pending' && (
          <div className="mt-1 space-y-1">
            {scene.error && <p className="text-rose-600 leading-snug line-clamp-2">{scene.error}</p>}
            <button type="button" disabled={regenerating || data.status === 'running' || preparing}
              onClick={() => regenerate([scene.index])}
              className="inline-flex items-center gap-1 rounded border border-amber-400 px-1.5 py-0.5 font-bold text-amber-700 disabled:opacity-50">
              <RotateCcw size={10} />다시 생성
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="mt-3 rounded-xl border border-cyan-200 bg-cyan-50/50 p-4 space-y-3">
      <div className="flex items-center justify-between text-sm font-bold text-slate-900">
        <span className="flex items-center gap-2">
          {data.status === 'running' || preparing
            ? <Loader size={14} className="animate-spin text-cyan-700" />
            : <CheckCircle size={14} className="text-emerald-600" />}
          {preparing
            ? '장면 준비 중 (대본·음성 계보 검증, 기사 증거 찾기)'
            : `총 ${total}개 장면으로 나눴어요 · ${done}개 완료`}
        </span>
        {!preparing && <span className="text-cyan-800">{percent}%</span>}
      </div>
      {!preparing && (
        <div className="h-2 rounded-full bg-slate-200 overflow-hidden">
          <div className="h-full bg-cyan-600 transition-all" style={{ width: `${percent}%` }} />
        </div>
      )}
      {(problemIndexes.length > 0 || actionError) && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {problemIndexes.length > 0 && (
            <button type="button" disabled={regenerating || data.status === 'running' || preparing}
              onClick={() => regenerate(problemIndexes)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1.5 font-bold text-white disabled:opacity-50">
              {regenerating ? <Loader size={12} className="animate-spin" /> : <RotateCcw size={12} />}
              문제 장면 {problemIndexes.length}개 다시 생성
            </button>
          )}
          {actionError && <span className="font-semibold text-rose-600">{actionError}</span>}
        </div>
      )}
      {viewer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={closeViewer}>
          <div className="relative max-h-full max-w-6xl w-full" onClick={e => e.stopPropagation()}>
            <button type="button" onClick={closeViewer}
              className="absolute -top-10 right-0 flex items-center gap-1 rounded-lg bg-white/15 px-3 py-1.5 text-sm font-bold text-white hover:bg-white/25">
              <X size={14} />닫기
            </button>
            {viewer.loading && (
              <div className="flex h-64 items-center justify-center text-white"><Loader className="animate-spin" /></div>
            )}
            {viewer.kind === 'video' && (
              <video src={viewer.url} controls autoPlay loop className="max-h-[85vh] w-full rounded-lg bg-black" />
            )}
            {viewer.kind === 'image' && (
              <img src={viewer.url} alt={`장면 ${viewer.index + 1}`} className="max-h-[85vh] w-full rounded-lg object-contain" />
            )}
            {!viewer.loading && (
              <p className="mt-2 text-center text-sm font-semibold text-white">
                장면 #{viewer.index + 1}{viewer.kind === 'video' ? ' · 영상' : ' · 이미지'}
              </p>
            )}
          </div>
        </div>
      )}
      {scenes.length > 0 && (
        <div className="space-y-3 max-h-[560px] overflow-y-auto">
          {sections.map(section => section.items.length > 0 && (
            <div key={section.key}>
              <div className={`mb-1.5 flex items-center gap-2 text-xs font-bold ${section.tone}`}>
                <span>{section.title}</span>
                <span className="rounded-full bg-white px-2 py-0.5 border border-current">{section.items.length}개</span>
                <span className="font-semibold text-slate-500">{section.hint}</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2">
                {section.items.map(renderScene)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const BACKGROUND_LABEL = { board: '회색 배경', stock: '스톡 영상', photo: 'AI 사진' }
const BACKGROUND_TONE = {
  board: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  stock: 'bg-sky-100 text-sky-800 border-sky-300',
  photo: 'bg-amber-100 text-amber-800 border-amber-300',
}
const OVERLAY_LABEL = {
  key_text: '강조 텍스트', big_number: '큰 숫자', compare_numbers: '수치 비교', flow: '흐름도',
  bullet_list: '목록', stat_chart: '수치 그래프', equation: '계산식', article_card: '기사 카드',
}

const BACKGROUND_HINT = {
  board: '어두운 회색 배경 위에 후편집만 나와요',
  stock: '스톡 영상을 깔아요',
  photo: 'AI 사진(Gemini, 유료)을 만들어요',
}

const fmtTime = (seconds) => {
  const total = Math.max(0, Math.round(seconds))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

// 후편집이 화면에 정확히 무엇을 띄우는지(글자·숫자·항목)를 한 줄씩 풀어 쓴다.
function overlayLines(overlay) {
  if (!overlay) return []
  const lines = []
  const icon = overlay.icon ? ` (그림: ${overlay.icon})` : ''
  switch (overlay.type) {
    case 'key_text':
      lines.push(`문구 “${overlay.text}”${icon}`)
      break
    case 'big_number':
      lines.push(`큰 숫자 “${overlay.value}”${overlay.label ? ` · 소제목 ${overlay.label}` : ''}${icon}`)
      break
    case 'compare_numbers':
      lines.push(`비교 “${overlay.headline}”${icon}`)
      ;(overlay.rows || []).forEach(r => lines.push(`  · ${r.label}: ${r.value}`))
      break
    case 'flow':
      lines.push(`흐름도 ${(overlay.nodes || []).map(n => `${n.label}${n.icon ? `(${n.icon})` : ''}`).join(' → ')}`)
      break
    case 'bullet_list':
      lines.push(`목록 “${overlay.title}”`)
      ;(overlay.items || []).forEach((item, k) => lines.push(`  ${k + 1}. ${item}`))
      break
    case 'stat_chart':
      lines.push(`막대 그래프 “${overlay.title || ''}”`)
      ;(overlay.items || []).forEach(item => lines.push(`  · ${item.label}: ${item.value_text}`))
      if (!(overlay.items || []).length && overlay.use_article_series) lines.push('  · 기사 속 수치 계열')
      break
    case 'equation':
      lines.push(`계산식 ${(overlay.left || []).join(` ${(overlay.ops || [])[0] || ''} `)} = ${overlay.result}${icon}`)
      break
    case 'article_card':
      lines.push('기사 카드(보도 인용)')
      break
    default:
      lines.push(overlay.type)
  }
  return lines
}

function backgroundLines(scene) {
  if (scene.background === 'stock') {
    const queries = (scene.stock_queries || []).join(' / ')
    return [`스톡 검색어: ${queries || '(없음)'}`,
      scene.fallback_key_text ? `못 찾으면 → 회색 배경 + “${scene.fallback_key_text}”` : null].filter(Boolean)
  }
  if (scene.background === 'photo') return [`AI 사진: ${scene.photo_brief || ''}`]
  return ['어두운 회색 배경']
}

// 편집 계획서: 구간 카드(시간대·배경·후편집 요약) → 장면별 상세(내레이션·배경 검색어·화면에 뜰 내용·이유).
// 2026-10-16 운영자 요청: "스톡 영상 씬 2~11"처럼 범위만 있으면 내용을 알 수 없다 — 무엇을 보여 주는지 풀어서 보여 준다.
// 문제 장면을 체크하고 말로 수정 지시를 적으면 그 장면만 다시 계획한다(필드 직접 편집 없음).
export function EditPlanSection({ plan, onReplan, busy }) {
  const summary = plan.summary || {}
  const scenes = plan.scenes || []
  const sceneByIndex = Object.fromEntries(scenes.map(s => [s.index, s]))
  const notes = plan.review_notes || {}
  const [marked, setMarked] = useState({}) // { [index]: 수정 지시 }
  const [error, setError] = useState('')
  const markedIndexes = Object.keys(marked).map(Number).sort((a, b) => a - b)

  // 장면별 시작 시각(누적)
  const startAt = {}
  let acc = 0
  scenes.forEach((scene) => { startAt[scene.index] = acc; acc += scene.duration || 0 })

  const toggle = (index) => setMarked(prev => {
    const next = { ...prev }
    if (index in next) delete next[index]
    else next[index] = ''
    return next
  })

  const submit = async () => {
    if (!markedIndexes.length || busy || !onReplan) return
    setError('')
    try {
      await onReplan(markedIndexes.map(index => ({ index, note: marked[index] || '' })))
      setMarked({})
    } catch (e) {
      setError(e.response?.data?.message || e.message || '다시 계획하지 못했어요. 기존 계획서는 그대로예요.')
    }
  }

  const renderScene = (scene) => {
    const lines = overlayLines(scene.overlay)
    const onFootage = scene.background !== 'board'
    return (
      <div key={scene.index} className={`py-2 ${scene.index in marked ? 'bg-amber-50' : ''}`}>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex cursor-pointer items-center gap-1 font-bold text-slate-800">
            <input type="checkbox" checked={scene.index in marked} onChange={() => toggle(scene.index)} />
            #{scene.index + 1}
          </label>
          <span className="text-slate-500">
            {fmtTime(startAt[scene.index])}~{fmtTime(startAt[scene.index] + (scene.duration || 0))} · {Math.round(scene.duration || 0)}초
          </span>
          {scene.overlay
            ? <span className="rounded-full bg-violet-100 px-2 py-0.5 font-bold text-violet-700">
                후편집 · {OVERLAY_LABEL[scene.overlay.type] || scene.overlay.type}
              </span>
            : <span className="rounded-full bg-slate-100 px-2 py-0.5 font-semibold text-slate-500">후편집 없음(영상 + 자막만)</span>}
          {scene.revised && <span className="rounded-full bg-amber-100 px-1.5 py-0.5 font-bold text-amber-700">수정됨</span>}
          {scene.auto_repaired && <span className="rounded-full bg-sky-50 px-1.5 py-0.5 font-semibold text-sky-600">자동 보정</span>}
        </div>
        <p className="mt-1 text-slate-600">“{scene.narration}”</p>
        <div className="mt-1 space-y-0.5 pl-3 text-slate-700">
          {backgroundLines(scene).map((line, k) => (
            <p key={`b${k}`}><b className="text-slate-500">배경</b> {line}</p>
          ))}
          {lines.map((line, k) => (
            <p key={`o${k}`} className={line.startsWith('  ') ? 'pl-4' : ''}>
              {k === 0 && <b className="text-violet-600">화면에 뜨는 내용 </b>}{line.trimStart()}
            </p>
          ))}
          {scene.overlay && onFootage && (
            <p className="text-slate-400">영상을 어둡게 눌러 그 위에 얹어요</p>
          )}
          {scene.reason && <p className="text-slate-400">이유: {scene.reason}</p>}
          {notes[String(scene.index)] && !(scene.index in marked) && (
            <p className="font-semibold text-amber-700">운영자 지시: {notes[String(scene.index)]}</p>
          )}
        </div>
        {scene.index in marked && (
          <input type="text" value={marked[scene.index]} maxLength={200}
            onChange={(e) => setMarked(prev => ({ ...prev, [scene.index]: e.target.value }))}
            placeholder="수정 지시 (예: 회색 배경으로 바꾸고 금리 상승 화살표를 보여 줘)"
            className="mt-1 w-full rounded border border-amber-300 px-2 py-1 text-xs" />
        )}
      </div>
    )
  }

  return (
    <details open className="rounded-lg border border-slate-200 bg-white p-3">
      <summary className="cursor-pointer text-sm font-bold text-slate-900">
        편집 계획서 · 회색 배경 {summary.board || 0} · 스톡 {summary.stock || 0} · AI 사진 {summary.photo || 0}
        <span className="ml-2 font-semibold text-slate-500">
          후편집 {summary.overlay_scenes || 0}장면 · 총 {fmtTime(plan.total_seconds || 0)} · 예상 Gemini ₩{(summary.gemini_krw_expected || 0).toLocaleString()}
        </span>
      </summary>
      {plan.quality && (
        <p className="mt-1 text-xs text-slate-500">
          레퍼런스 기준 점검 · 스톡 비중 {Math.round((plan.quality.footage_ratio || 0) * 100)}%(목표 약 2/3) ·
          후편집 {Math.round((plan.quality.overlay_ratio || 0) * 100)}%(목표 55~75%) ·
          배경 평균 {plan.quality.mean_bg_run_seconds || 0}초 유지
          {(plan.quality.violations || []).length > 0
            ? <span className="ml-1 font-bold text-rose-600">· 기준 미달: {(plan.quality.violations || []).join(', ')}</span>
            : <span className="ml-1 font-bold text-emerald-600">· 기준 통과</span>}
          {plan.auto_repair_rounds ? <span className="ml-1 text-slate-400">(자동 보정 {plan.auto_repair_rounds}회)</span> : null}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs">
        <span className="font-semibold text-amber-800">
          문제 되는 장면은 체크하고 수정 지시를 적은 뒤 다시 계획하세요. 이미지는 만들지 않아요.
        </span>
        <button type="button" onClick={submit} disabled={!markedIndexes.length || busy}
          className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-1.5 font-bold text-white disabled:opacity-50">
          {busy ? <Loader size={12} className="animate-spin" /> : <RotateCcw size={12} />}
          표시한 {markedIndexes.length}장면 다시 계획
        </button>
      </div>
      {error && <p className="mt-1 text-xs font-semibold text-rose-600">{error}</p>}
      {markedIndexes.length > 0 && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs">
          <p className="mb-1 font-bold text-slate-700">문제 정리 ({markedIndexes.length}장면)</p>
          <ul className="space-y-0.5 text-slate-600">
            {markedIndexes.map(index => (
              <li key={index}>#{index + 1} — {marked[index] ? marked[index] : '지시 없음(더 나은 구성으로 다시)'}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 space-y-2">
        {(plan.segments || []).map(segment => {
          const first = segment.range[0]
          const last = segment.range[1]
          const segScenes = segment.scenes.map(i => sceneByIndex[i]).filter(Boolean)
          const withOverlay = segScenes.filter(s => s.overlay)
          const counts = {}
          withOverlay.forEach(s => { counts[s.overlay.type] = (counts[s.overlay.type] || 0) + 1 })
          return (
            <details key={segment.id} open className="rounded-lg border border-slate-200">
              <summary className="cursor-pointer bg-slate-50 px-3 py-2 text-xs">
                <span className={`mr-2 rounded-full border px-2 py-0.5 font-bold ${BACKGROUND_TONE[segment.background] || ''}`}>
                  {BACKGROUND_LABEL[segment.background] || segment.background}
                </span>
                <b className="text-slate-800">
                  씬 {first + 1}{last !== first ? `~${last + 1}` : ''} · {fmtTime(startAt[first])}~{fmtTime(startAt[last] + (sceneByIndex[last]?.duration || 0))} ({segScenes.length}장면, {Math.round(segment.seconds)}초)
                </b>
                {segment.highlight && <span className="ml-2 rounded-full bg-rose-100 px-2 py-0.5 font-bold text-rose-700">하이라이트</span>}
                <span className="ml-2 text-slate-500">
                  {BACKGROUND_HINT[segment.background]} · 후편집 {withOverlay.length}장면
                  {withOverlay.length > 0 && ` (${Object.entries(counts).map(([t, c]) => `${OVERLAY_LABEL[t] || t} ${c}`).join(', ')})`}
                </span>
              </summary>
              <div className="divide-y divide-slate-100 px-3 text-xs">
                {segScenes.map(renderScene)}
              </div>
            </details>
          )
        })}
      </div>
    </details>
  )
}
