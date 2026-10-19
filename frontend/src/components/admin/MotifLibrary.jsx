import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ImagePlus, Loader, Sparkles, Trash2, UploadCloud } from 'lucide-react'
import apiClient from '../../api/client'

// 연상 소재(그림) 라이브러리 관리 — 2026-10-19 운영자 요청: 연상에 쓰는 그림을 화면에서 추가·확인·삭제한다.
// 움직임 부품(이동·도미노 등)은 코드라 여기서 늘릴 수 없고, 소재(그림)만 늘린다.
const GENERATE_COST_KRW = 339 // Gemini 이미지 1장(장부 기준 추정, 콘솔 대조 전)

const errorMessage = (e, fallback) => e?.response?.data?.message || e?.response?.data?.detail || e?.message || fallback

function MotifThumb({ slug, alt }) {
  const [url, setUrl] = useState(null)
  useEffect(() => {
    let revoked = false
    let created = null
    apiClient.get(`/motifs/${slug}/image`, { responseType: 'blob' })
      .then(res => {
        if (revoked) return
        created = URL.createObjectURL(res.data)
        setUrl(created)
      })
      .catch(() => {})
    return () => { revoked = true; if (created) URL.revokeObjectURL(created) }
  }, [slug])
  return (
    <div className="aspect-square rounded-lg bg-slate-700 flex items-center justify-center overflow-hidden">
      {url ? <img src={url} alt={alt} className="max-h-full max-w-full object-contain p-2" /> : <Loader size={14} className="animate-spin text-slate-400" />}
    </div>
  )
}

const originLabel = (m) => (m.generator === 'manual' ? '직접 올림' : m.on_demand ? '즉석 생성' : 'AI 생성')

export default function MotifLibrary() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['motifs'], queryFn: () => apiClient.get('/motifs').then(r => r.data) })
  const motifs = data?.motifs || []
  const pending = motifs.filter(m => m.review === 'pending')

  const [description, setDescription] = useState('')
  const [label, setLabel] = useState('')
  const [aliases, setAliases] = useState('')
  const [file, setFile] = useState(null)
  const [notice, setNotice] = useState(null) // { tone: 'ok' | 'error', text }
  const [jobId, setJobId] = useState('')
  const [orders, setOrders] = useState(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['motifs'] })

  const generate = useMutation({
    mutationFn: () => apiClient.post('/motifs/generate', { description }, { timeout: 600000 }).then(r => r.data),
    onSuccess: (result) => {
      refresh()
      if (result.created?.length) {
        setNotice({ tone: 'ok', text: `'${result.created[0].label}' 소재를 만들었어요. 비용 약 ₩${result.spent_krw.toLocaleString()}` })
        setDescription('')
      } else {
        const reason = result.failed?.[0]?.reason || result.skipped?.[0]?.reason || '만들지 못했어요'
        setNotice({ tone: 'error', text: `${reason} (비용 약 ₩${(result.spent_krw || 0).toLocaleString()})` })
      }
    },
    onError: (e) => setNotice({ tone: 'error', text: errorMessage(e, '소재를 만들지 못했어요.') }),
  })

  const upload = useMutation({
    mutationFn: async () => {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(new Error('파일을 읽지 못했어요.'))
        reader.readAsDataURL(file)
      })
      return apiClient.post('/motifs/upload', { label, aliases, imageBase64: dataUrl }).then(r => r.data)
    },
    onSuccess: (result) => {
      refresh()
      setNotice({ tone: 'ok', text: `'${result.label}' 소재를 등록했어요.` })
      setLabel(''); setAliases(''); setFile(null)
    },
    onError: (e) => setNotice({ tone: 'error', text: errorMessage(e, '업로드에 실패했어요.') }),
  })

  const review = useMutation({
    mutationFn: ({ slug, action }) => apiClient.post(`/motifs/${slug}/review`, { action }).then(r => r.data),
    onSuccess: () => refresh(),
    onError: (e) => setNotice({ tone: 'error', text: errorMessage(e, '처리하지 못했어요.') }),
  })

  const confirmGenerate = () => {
    if (generate.isPending) return
    if (!window.confirm(`'${description.trim()}' 소재를 만듭니다.\n비용 약 ₩${GENERATE_COST_KRW}(Gemini 이미지 1장, 콘솔 대조 전 추정)이에요. 검수에 통과하지 못해도 비용은 들어요. 진행할까요?`)) return
    setNotice(null)
    generate.mutate()
  }

  const remove = (m) => {
    if (!window.confirm(`'${m.label}' 소재를 삭제할까요?\n이미지 파일도 지워지고 되돌릴 수 없어요.`)) return
    review.mutate({ slug: m.slug, action: 'delete' })
  }

  const loadOrders = async () => {
    setNotice(null)
    try {
      const res = await apiClient.get(`/jobs/${jobId}/images/plan`)
      const missing = res.data?.plan?.visual_concepts?.missing || []
      setOrders(missing.filter(o => o.type === 'motif'))
    } catch (e) {
      setOrders(null)
      setNotice({ tone: 'error', text: errorMessage(e, '계획서를 불러오지 못했어요.') })
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="flex items-center gap-2 text-base font-extrabold text-slate-900"><Sparkles size={18} className="text-cyan-600" />연상 소재 라이브러리</h2>
        <p className="mt-1 text-xs text-slate-500 leading-relaxed">
          대본의 행간을 그림으로 보여 줄 때 쓰는 소재예요. 한 번 만든 소재는 저장되어 다음 영상부터 그대로 재사용해요.
          움직임(이동·도미노·저울 등) 자체는 코드로 만든 부품이라 여기서 늘릴 수 없고, 소재(그림)만 늘려요.
        </p>
        {notice && (
          <p className={`mt-3 rounded-lg px-3 py-2 text-xs font-semibold ${notice.tone === 'ok' ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-700'}`}>{notice.text}</p>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
          <h3 className="flex items-center gap-2 text-sm font-extrabold text-slate-900"><ImagePlus size={16} />설명으로 만들기 <span className="text-[11px] font-semibold text-amber-700">1장 약 ₩{GENERATE_COST_KRW}</span></h3>
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3} maxLength={120}
            placeholder="예) 충격을 흡수하는 쿠션 같은 완충재"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <button type="button" onClick={confirmGenerate} disabled={generate.isPending || description.trim().length < 4}
            className="inline-flex items-center gap-2 rounded-lg bg-cyan-600 px-4 py-2 text-xs font-bold text-white disabled:opacity-50">
            {generate.isPending ? <Loader size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {generate.isPending ? '만드는 중 (약 30초)…' : '소재 만들기'}
          </button>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
          <h3 className="flex items-center gap-2 text-sm font-extrabold text-slate-900"><UploadCloud size={16} />직접 올리기 <span className="text-[11px] font-semibold text-emerald-700">무료</span></h3>
          <input value={label} onChange={e => setLabel(e.target.value)} maxLength={12} placeholder="이름 (2~12자, 예: 완충재)"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input value={aliases} onChange={e => setAliases(e.target.value)} placeholder="대본에서 쓰는 다른 말 (쉼표로 구분, 예: 쿠션, 충격 흡수)"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input type="file" accept="image/png" onChange={e => setFile(e.target.files?.[0] || null)} className="block w-full text-xs" />
          <p className="text-[11px] text-slate-500">배경이 투명한 PNG만 올릴 수 있어요(한 변 64~4096px, 6MB 이하). 이름·별칭이 기존 소재와 겹치면 등록되지 않아요.</p>
          <button type="button" onClick={() => upload.mutate()} disabled={upload.isPending || !file || label.trim().length < 2}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-800 px-4 py-2 text-xs font-bold text-white disabled:opacity-50">
            {upload.isPending ? <Loader size={14} className="animate-spin" /> : <UploadCloud size={14} />}등록
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h3 className="text-sm font-extrabold text-slate-900">필요한데 없는 소재 주문 <span className="text-[11px] font-semibold text-slate-500">(편집 계획서 기준)</span></h3>
        <div className="flex items-center gap-2 text-xs">
          <input value={jobId} onChange={e => setJobId(e.target.value.replace(/\D/g, ''))} placeholder="작업 번호" className="w-28 rounded-lg border border-slate-300 px-2 py-1.5" />
          <button type="button" onClick={loadOrders} disabled={!jobId}
            className="rounded-lg border border-slate-300 px-3 py-1.5 font-bold text-slate-700 disabled:opacity-50">주문 불러오기</button>
        </div>
        {orders && orders.length === 0 && <p className="text-xs text-slate-500">이 작업에는 새로 필요한 소재 주문이 없어요.</p>}
        {orders && orders.map(o => (
          <div key={o.name} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-xs">
            <span><b>{o.name}</b> — {o.description} <span className="text-slate-400">(장면 {(o.scenes || []).join(', ')})</span></span>
            <button type="button" onClick={() => setDescription(o.description)} className="shrink-0 rounded border border-cyan-500 px-2 py-1 font-bold text-cyan-700">설명 채우기</button>
          </div>
        ))}
      </div>

      {pending.length > 0 && (
        <div className="rounded-2xl border border-amber-300 bg-amber-50 p-5 space-y-3">
          <h3 className="text-sm font-extrabold text-amber-900">육안 확인 대기 {pending.length}개 <span className="text-[11px] font-semibold">— 즉석으로 만든 소재예요. 마음에 들면 승인, 아니면 삭제하세요</span></h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            {pending.map(m => (
              <div key={m.slug} className="rounded-lg bg-white p-2 text-xs space-y-1">
                <MotifThumb slug={m.slug} alt={m.label} />
                <p className="font-bold text-slate-800">{m.label}</p>
                <div className="flex gap-1">
                  <button type="button" onClick={() => review.mutate({ slug: m.slug, action: 'approve' })} className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-1 font-bold text-white"><Check size={11} />승인</button>
                  <button type="button" onClick={() => remove(m)} className="inline-flex items-center gap-1 rounded border border-rose-400 px-2 py-1 font-bold text-rose-600"><Trash2 size={11} />삭제</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h3 className="text-sm font-extrabold text-slate-900">전체 소재 {motifs.length}개</h3>
        {isLoading && <Loader size={16} className="animate-spin text-slate-400" />}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {motifs.map(m => (
            <div key={m.slug} className="rounded-lg border border-slate-200 p-2 text-xs space-y-1">
              {m.has_image ? <MotifThumb slug={m.slug} alt={m.label} /> : <div className="aspect-square rounded-lg bg-rose-50 text-rose-600 flex items-center justify-center">파일 없음</div>}
              <p className="font-bold text-slate-800">{m.label}</p>
              <p className="text-[10px] text-slate-500 leading-snug line-clamp-2">{(m.aliases || []).join(', ')}</p>
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold text-slate-400">{originLabel(m)}{m.review === 'pending' ? ' · 대기' : ''}</span>
                <button type="button" onClick={() => remove(m)} title="삭제" className="text-slate-400 hover:text-rose-600"><Trash2 size={12} /></button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
