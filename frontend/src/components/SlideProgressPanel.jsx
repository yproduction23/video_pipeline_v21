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
      if (!scene.has_thumb || requested.current.has(scene.index)) return
      requested.current.add(scene.index)
      apiClient
        .get(`/jobs/${jobId}/images/progress/thumb/${scene.index}`, { responseType: 'blob' })
        .then(res => setThumbs(prev => ({ ...prev, [scene.index]: URL.createObjectURL(res.data) })))
        .catch(() => requested.current.delete(scene.index))
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
