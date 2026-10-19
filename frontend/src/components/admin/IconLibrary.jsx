import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader, Shapes, Trash2, UploadCloud } from 'lucide-react'
import apiClient from '../../api/client'

// 아이콘 라이브러리 관리 — 2026-10-19 운영자 요청: 연상 소재뿐 아니라 아이콘도 화면에서 추가(직접 올리기)·확인·삭제한다.
// 기본 아이콘은 기존 계획서가 이름으로 쓰고 있어 삭제할 수 없고, 직접 올린 아이콘만 삭제할 수 있다.
const errorMessage = (e, fallback) => e?.response?.data?.message || e?.response?.data?.detail || e?.message || fallback

function IconThumb({ name, alt }) {
  const [url, setUrl] = useState(null)
  useEffect(() => {
    let revoked = false
    let created = null
    apiClient.get(`/motifs/icons/${name}/image`, { responseType: 'blob' })
      .then(res => {
        if (revoked) return
        created = URL.createObjectURL(res.data)
        setUrl(created)
      })
      .catch(() => {})
    return () => { revoked = true; if (created) URL.revokeObjectURL(created) }
  }, [name])
  return (
    <div className="aspect-square rounded-lg bg-slate-700 flex items-center justify-center overflow-hidden">
      {url ? <img src={url} alt={alt} className="max-h-full max-w-full object-contain p-2" /> : <Loader size={14} className="animate-spin text-slate-400" />}
    </div>
  )
}

export default function IconLibrary() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['icons'], queryFn: () => apiClient.get('/motifs/icons').then(r => r.data) })
  const icons = data?.icons || []
  const uploaded = icons.filter(i => i.uploaded)

  const [label, setLabel] = useState('')
  const [hint, setHint] = useState('')
  const [file, setFile] = useState(null)
  const [notice, setNotice] = useState(null) // { tone: 'ok' | 'error', text }

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['icons'] })

  const upload = useMutation({
    mutationFn: async () => {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(new Error('파일을 읽지 못했어요.'))
        reader.readAsDataURL(file)
      })
      return apiClient.post('/motifs/icons/upload', { label, hint, fileBase64: dataUrl }).then(r => r.data)
    },
    onSuccess: (result) => {
      refresh()
      setNotice({ tone: 'ok', text: `'${result.hint}' 아이콘을 등록했어요. 다음 계획서부터 고를 수 있어요.` })
      setLabel(''); setHint(''); setFile(null)
    },
    onError: (e) => setNotice({ tone: 'error', text: errorMessage(e, '업로드에 실패했어요.') }),
  })

  const remove = useMutation({
    mutationFn: (name) => apiClient.post(`/motifs/icons/${name}/delete`).then(r => r.data),
    onSuccess: () => refresh(),
    onError: (e) => setNotice({ tone: 'error', text: errorMessage(e, '삭제하지 못했어요.') }),
  })

  const confirmRemove = (icon) => {
    if (!window.confirm(`'${icon.hint}' 아이콘을 삭제할까요?\n이미 이 아이콘을 쓰는 계획서는 아이콘 없이 보여요. 되돌릴 수 없어요.`)) return
    setNotice(null)
    remove.mutate(icon.name)
  }

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="flex items-center gap-2 text-base font-extrabold text-slate-900"><Shapes size={18} className="text-cyan-600" />아이콘 라이브러리</h2>
        <p className="mt-1 text-xs text-slate-500 leading-relaxed">
          숫자 카드·흐름도·비교·그림 장면에 붙는 작은 아이콘이에요. 영상을 만들 때 AI가 아래 <b>설명</b>을 보고 알맞은 아이콘을 골라요.
          그래서 어떤 뜻에 쓰는 그림인지 설명을 구체적으로 적어야 해요. 기본 아이콘은 삭제할 수 없고, 직접 올린 아이콘만 삭제할 수 있어요.
        </p>
        {notice && (
          <p className={`mt-3 rounded-lg px-3 py-2 text-xs font-semibold ${notice.tone === 'ok' ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-700'}`}>{notice.text}</p>
        )}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-extrabold text-slate-900"><UploadCloud size={16} />직접 올리기 <span className="text-[11px] font-semibold text-emerald-700">무료</span></h3>
        <input value={label} onChange={e => setLabel(e.target.value)} maxLength={20} placeholder="이름 (2~20자, 예: 방패)"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <input value={hint} onChange={e => setHint(e.target.value)} maxLength={80} placeholder="설명 (4~80자, 예: 방패: 안전·방어·보호)"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <input type="file" accept="image/svg+xml,image/png,.svg,.png" onChange={e => setFile(e.target.files?.[0] || null)} className="block w-full text-xs" />
        <p className="text-[11px] text-slate-500 leading-relaxed">
          SVG(권장, 기본 아이콘처럼 흰색 본체·앰버 포인트·진한 선의 굵은 평면 스타일) 또는 배경이 투명한 PNG만 올릴 수 있어요(2MB 이하, PNG는 한 변 48~2048px).
          스크립트·외부 링크가 들어간 SVG는 등록되지 않아요. 스타일이 다르면 영상에서 튀어 보일 수 있으니 올린 뒤 목록에서 확인해 주세요.
        </p>
        <button type="button" onClick={() => { setNotice(null); upload.mutate() }} disabled={upload.isPending || !file || label.trim().length < 2 || hint.trim().length < 4}
          className="inline-flex items-center gap-2 rounded-lg bg-slate-800 px-4 py-2 text-xs font-bold text-white disabled:opacity-50">
          {upload.isPending ? <Loader size={14} className="animate-spin" /> : <UploadCloud size={14} />}등록
        </button>
      </div>

      {uploaded.length > 0 && (
        <div className="rounded-2xl border border-cyan-200 bg-cyan-50 p-5 space-y-3">
          <h3 className="text-sm font-extrabold text-cyan-900">직접 올린 아이콘 {uploaded.length}개</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            {uploaded.map(i => (
              <div key={i.name} className="rounded-lg bg-white p-2 text-xs space-y-1">
                <IconThumb name={i.name} alt={i.hint} />
                <p className="font-bold text-slate-800 leading-snug">{i.hint}</p>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold text-slate-400">{i.format.toUpperCase()} · {i.created}</span>
                  <button type="button" onClick={() => confirmRemove(i)} title="삭제" className="text-slate-400 hover:text-rose-600"><Trash2 size={12} /></button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h3 className="text-sm font-extrabold text-slate-900">전체 아이콘 {icons.length}개</h3>
        {isLoading && <Loader size={16} className="animate-spin text-slate-400" />}
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-6 lg:grid-cols-8">
          {icons.map(i => (
            <div key={i.name} className="rounded-lg border border-slate-200 p-2 text-xs space-y-1">
              {i.has_image ? <IconThumb name={i.name} alt={i.hint} /> : <div className="aspect-square rounded-lg bg-rose-50 text-rose-600 flex items-center justify-center">파일 없음</div>}
              <p className="text-[10px] text-slate-600 leading-snug line-clamp-3">{i.hint}</p>
              {i.uploaded && <span className="text-[10px] font-semibold text-cyan-700">직접 올림</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
