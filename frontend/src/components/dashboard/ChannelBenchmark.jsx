import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Users, TrendingUp, Tv, X, Plus, Search, Loader2 } from 'lucide-react'
import apiClient from '../../api/client'

function formatSubscribers(count) {
  if (count == null) return '정보 없음'
  if (count >= 10000) {
    const man = (count / 10000).toFixed(1)
    return `~${man.endsWith('.0') ? man.slice(0, -2) : man}만명`
  }
  return `~${count.toLocaleString('ko-KR')}명`
}

function formatViews(count) {
  if (count == null) return '—'
  if (count >= 10000) {
    const man = (count / 10000).toFixed(1)
    return `${man.endsWith('.0') ? man.slice(0, -2) : man}만회`
  }
  return `${count.toLocaleString('ko-KR')}회`
}

function apiError(error, fallback) {
  return error?.response?.data?.message || error?.response?.data?.detail || error?.message || fallback
}

export default function ChannelBenchmark() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [addRef, setAddRef] = useState('')
  const [addPreview, setAddPreview] = useState(null)
  const [addName, setAddName] = useState('')
  const [addError, setAddError] = useState('')

  const adminListQuery = useQuery({
    queryKey: ['admin-reference-channels-active'],
    queryFn: () => apiClient.get('/admin/reference-channels', { params: { activeOnly: true } }).then(res => res.data),
  })

  const channelIds = (adminListQuery.data || []).map(ch => ch.channelId)

  // 최근 영상 성과(최근 10개 평균 조회수, 업로드 주기)로 "성장세"를 대체한다.
  // 구독자 수 변화 이력(스냅샷)을 아직 저장하지 않아 실제 증가율은 계산할 수 없다.
  const statsQuery = useQuery({
    queryKey: ['youtube-channel-benchmark', channelIds.join(',')],
    queryFn: () => apiClient.get('/youtube/channels/benchmark').then(res => res.data),
    staleTime: 1000 * 60 * 60 * 6,
    retry: 1,
    enabled: channelIds.length > 0,
  })

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['admin-reference-channels-active'] })
    queryClient.invalidateQueries({ queryKey: ['youtube-channel-benchmark'] })
    queryClient.invalidateQueries({ queryKey: ['admin-reference-channels'] })
  }

  const removeMutation = useMutation({
    mutationFn: id => apiClient.delete(`/admin/reference-channels/${id}`).then(res => res.data),
    onSuccess: invalidateAll,
  })

  const previewMutation = useMutation({
    mutationFn: ref => apiClient.post('/admin/reference-channels/bulk-preview', [ref]).then(res => res.data),
    onSuccess: data => {
      const row = Array.isArray(data) ? data[0] : null
      const candidate = row?.candidates?.[0]
      setAddPreview(candidate || null)
      setAddName(candidate?.title || '')
      setAddError(row?.errorMessage || (!candidate ? '존재하는 YouTube 채널을 확인할 수 없습니다.' : ''))
    },
    onError: error => {
      setAddPreview(null)
      setAddError(apiError(error, '채널 정보를 불러오지 못했습니다.'))
    },
  })

  const createMutation = useMutation({
    mutationFn: payload => apiClient.post('/admin/reference-channels', payload).then(res => res.data),
    onSuccess: () => {
      setAddOpen(false)
      setAddRef('')
      setAddPreview(null)
      setAddName('')
      setAddError('')
      invalidateAll()
    },
    onError: error => setAddError(apiError(error, '채널 추가에 실패했습니다.')),
  })

  const statsByChannelId = new Map((statsQuery.data?.channels || []).map(ch => [ch.channel_id, ch]))
  const channels = (adminListQuery.data || []).map(ch => {
    const stat = statsByChannelId.get(ch.channelId)
    return {
      id: ch.id,
      channelId: ch.channelId,
      title: ch.displayName || ch.youtubeTitle || ch.channelId,
      subscriberCount: stat?.subscriber_count ?? (ch.subscriberCountHidden ? null : ch.subscriberCount),
      avgViews: stat?.avg_views_recent_10,
      uploadGapDays: stat?.upload_gap_days,
      statsError: stat?.status === 'error' ? stat?.error_message : null,
    }
  })

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Tv size={18} className="text-accent-cyan" />
          <h3 className="font-semibold text-slate-900">벤치마크 채널 현황</h3>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 hidden sm:inline">Real-time YouTube Data</span>
          <button
            type="button"
            onClick={() => setAddOpen(value => !value)}
            className="inline-flex items-center gap-1 rounded-lg border border-cyan-300 bg-cyan-50 px-2 py-1 text-xs font-bold text-cyan-800 hover:bg-cyan-100"
          >
            <Plus size={13} /> 채널 추가
          </button>
        </div>
      </div>

      {addOpen && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2">
          <div className="flex gap-2">
            <input
              value={addRef}
              onChange={e => { setAddRef(e.target.value); setAddPreview(null); setAddError('') }}
              placeholder="채널 ID(UC...), @handle, 또는 채널 URL"
              className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
            />
            <button
              type="button"
              onClick={() => previewMutation.mutate(addRef.trim())}
              disabled={!addRef.trim() || previewMutation.isPending}
              className="inline-flex items-center gap-1 rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50 shrink-0"
            >
              {previewMutation.isPending ? <Loader2 className="animate-spin" size={13} /> : <Search size={13} />}
              검색
            </button>
          </div>
          {addError && <div className="text-xs font-semibold text-rose-600">{addError}</div>}
          {addPreview && (
            <div className="flex items-center justify-between gap-2 rounded-lg border border-cyan-200 bg-white p-2.5">
              <div className="flex items-center gap-2 min-w-0">
                {addPreview.thumbnail_url ? (
                  <img src={addPreview.thumbnail_url} alt="" className="h-8 w-8 rounded-full border border-slate-200 object-cover" />
                ) : <div className="h-8 w-8 rounded-full bg-slate-100" />}
                <div className="min-w-0">
                  <div className="truncate text-xs font-bold text-slate-900">{addPreview.title}</div>
                  <div className="text-[10px] text-slate-500">{formatSubscribers(addPreview.subscriber_count)}</div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => createMutation.mutate({ displayName: addName.trim() || addPreview.title, channelRef: addPreview.channel_id, displayOrder: 0 })}
                disabled={createMutation.isPending}
                className="shrink-0 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50"
              >
                {createMutation.isPending ? '추가 중...' : '추가'}
              </button>
            </div>
          )}
        </div>
      )}

      {adminListQuery.isLoading ? (
        <div className="text-center py-6 text-sm text-slate-400">채널 목록을 불러오는 중...</div>
      ) : adminListQuery.isError ? (
        <div className="text-center py-6 text-sm text-amber-500">채널 목록 불러오기 실패</div>
      ) : channels.length === 0 ? (
        <div className="text-center py-6 text-sm text-slate-400">등록된 벤치마크 채널이 없습니다. 위 "채널 추가"로 등록하세요.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {channels.map(ch => (
            <div
              key={ch.id}
              className="group relative flex items-start justify-between gap-1 p-3.5 rounded-lg bg-slate-50 border border-slate-100 transition hover:border-cyan-400 hover:bg-cyan-50/40"
            >
              <button
                type="button"
                onClick={() => navigate('/longform/new', { state: { openBenchmarkChannelId: ch.channelId } })}
                className="min-w-0 flex-1 text-left"
              >
                <div className="font-semibold text-sm text-slate-800 truncate">{ch.title}</div>
                <div className="text-xs text-slate-500 flex items-center gap-1 mt-1">
                  <Users size={12} className="text-slate-400" />
                  <span>구독자 {formatSubscribers(ch.subscriberCount)}</span>
                </div>
                {ch.statsError ? (
                  <div className="text-[10px] text-amber-500 mt-1">{ch.statsError}</div>
                ) : statsQuery.isLoading ? (
                  <div className="text-[10px] text-slate-400 mt-1">최근 성과 불러오는 중...</div>
                ) : (
                  <div className="text-[10px] text-slate-500 mt-1.5 space-y-0.5">
                    <div>최근 10개 평균 조회수 {formatViews(ch.avgViews)}</div>
                    <div>업로드 주기 {ch.uploadGapDays != null ? `약 ${ch.uploadGapDays}일` : '—'}</div>
                  </div>
                )}
              </button>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <TrendingUp size={16} className="text-accent-cyan opacity-80" />
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (window.confirm(`"${ch.title}" 채널을 벤치마크 목록에서 제거할까요?`)) removeMutation.mutate(ch.id)
                  }}
                  disabled={removeMutation.isPending}
                  className="opacity-0 group-hover:opacity-100 transition rounded-md p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                  title="벤치마크 채널에서 제거"
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
