import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Check, Edit3, Eye, EyeOff, Loader2, Plus,
  Search, ShieldCheck, X,
} from 'lucide-react'
import apiClient from '../../api/client'

const TIERS = ['MEGA', 'LARGE', 'MEDIUM', 'SMALL']

const BULK_CHANNELS = [
  '슈카월드', '삼프로TV 3PROTV', '김작가 TV', '삼성증권 Samsung POP', '신사임당',
  '열급쟁이부자들TV', '미래에셋 스마트머니', '채널K by 키움증권', '달란트투자', '한국경제TV',
  '재테크읽어주는파일럿', '슈카월드 코믹스', '전인구경제연구소', '언더스탠딩 세상의모든지식', '815머니톡',
  '웅달책방', '소수몽키', '박곰희TV', 'OCR 불확실 19번', '와이스트릿 Ystreet',
  '한경글로벌마켓', '박종훈의 지식한방', '돈깡', '내일은 투자왕 김단테', '기럿의 주식노트',
  '오션의 미국증시 라이브', '힐링여행자', '창읽개미TV', '선대인 TV', '할 수 있다! 알고 투자',
  'OCR 불확실 31번', '부자회사원', '시운주식', '홍춘욱의 경제강의노트', '이효석아카데미',
  '미국주식으로 은퇴하기', '미국주식으로 부자되기', '부자티비', '경제 읽어주는 남자 김광석TV', '김영익의 경제스쿨',
  '뉴욕주민', '부자아빠주식학교', '설명왕_테이버', '슈퍼개미 이세무사TV', '미국주식에 미치다 TV',
  '압권', '유동원의 성공투자', '박세익 체슬리TV',
]

const DEFAULT_BULK_TEXT = BULK_CHANNELS.join('\n')

function subscriberText(value, hidden) {
  if (hidden) return '구독자 비공개'
  if (value == null) return '—'
  const number = Number(value)
  if (number >= 10000) return `~${(number / 10000).toFixed(1)}만`
  if (number >= 1000) return `~${(number / 1000).toFixed(1)}천`
  return `~${number.toLocaleString('ko-KR')}`
}

function normalizedName(value) {
  return String(value || '').toLowerCase().replace(/[^0-9a-z가-힣]/g, '')
}

function nameMismatch(query, title) {
  const left = normalizedName(query)
  const right = normalizedName(title)
  if (!left || !right) return true
  return !left.includes(right) && !right.includes(left)
}

function apiError(error, fallback) {
  return error?.response?.data?.message
    || error?.response?.data?.detail
    || error?.message
    || fallback
}

function parseBulkEntries(value) {
  return value.split('\n')
    .map((line, index) => ({
      query: line.replace(/^\s*\d+[.)]\s*/, '').trim(),
      displayOrder: (index + 1) * 10,
    }))
    .filter(entry => entry.query)
    .map(entry => ({
      ...entry,
      uncertain: /OCR\s*불확실/i.test(entry.query),
    }))
}

function CandidateCard({ candidate, query, selected, onSelect, disabled = false }) {
  const channelId = candidate.channel_id
  const mismatch = nameMismatch(query, candidate.title)
  return (
    <label className={`block rounded-xl border p-3 transition ${selected ? 'border-cyan-500 bg-cyan-50 ring-2 ring-cyan-500/20' : 'border-slate-200 bg-white hover:border-slate-300'} ${disabled ? 'cursor-default' : 'cursor-pointer'}`}>
      <div className="flex items-start gap-3">
        {!disabled && (
          <input
            type="radio"
            checked={selected}
            onChange={onSelect}
            className="mt-1 h-4 w-4 accent-cyan-600"
          />
        )}
        {candidate.thumbnail_url ? (
          <img src={candidate.thumbnail_url} alt="" className="h-12 w-12 shrink-0 rounded-full border border-slate-200 object-cover" />
        ) : (
          <div className="h-12 w-12 shrink-0 rounded-full bg-slate-100" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-bold text-slate-900">{candidate.title || '제목 없음'}</span>
            {mismatch && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-900">⚠️ 이름 불일치</span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs font-semibold text-slate-600">
            <span>{candidate.handle || 'handle 없음'}</span>
            <span>{subscriberText(candidate.subscriber_count, candidate.hidden_subscriber_count)}</span>
          </div>
          <div className="mt-1 break-all text-[10px] font-medium text-slate-400">{channelId || 'channel_id 없음'}</div>
        </div>
      </div>
    </label>
  )
}

export default function ReferenceChannelManager() {
  const queryClient = useQueryClient()
  const [showInactive, setShowInactive] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState(null)
  const [singleRef, setSingleRef] = useState('')
  const [singlePreview, setSinglePreview] = useState(null)
  const [singleDisplayName, setSingleDisplayName] = useState('')
  const [singleOrder, setSingleOrder] = useState(0)
  const [singleError, setSingleError] = useState('')
  const [bulkText, setBulkText] = useState(DEFAULT_BULK_TEXT)
  const [bulkRows, setBulkRows] = useState([])
  const [bulkSelections, setBulkSelections] = useState({})
  const [bulkResult, setBulkResult] = useState(null)
  const [bulkError, setBulkError] = useState('')

  const channelsQuery = useQuery({
    queryKey: ['admin-reference-channels'],
    queryFn: () => apiClient.get('/admin/reference-channels', { params: { activeOnly: false } }).then(response => response.data),
  })

  const invalidateLists = () => {
    queryClient.invalidateQueries({ queryKey: ['admin-reference-channels'] })
    queryClient.invalidateQueries({ queryKey: ['youtube-channel-benchmark'] })
  }

  const channels = Array.isArray(channelsQuery.data) ? channelsQuery.data : []
  const visibleChannels = useMemo(
    () => showInactive ? channels : channels.filter(channel => channel.active),
    [channels, showInactive],
  )

  const singlePreviewMutation = useMutation({
    mutationFn: (channelRef) => apiClient
      .post('/admin/reference-channels/bulk-preview', [channelRef])
      .then(response => response.data),
    onSuccess: (data) => {
      const row = Array.isArray(data) ? data[0] : null
      const candidate = row?.candidates?.[0]
      setSinglePreview(candidate || null)
      setSingleDisplayName(candidate?.title || '')
      setSingleError(row?.errorMessage || (!candidate ? '존재하는 YouTube 채널을 확인할 수 없습니다.' : ''))
    },
    onError: error => {
      setSinglePreview(null)
      setSingleError(apiError(error, '채널 정보를 불러오지 못했습니다.'))
    },
  })

  const createMutation = useMutation({
    mutationFn: payload => apiClient.post('/admin/reference-channels', payload).then(response => response.data),
    onSuccess: () => {
      setSingleRef('')
      setSinglePreview(null)
      setSingleDisplayName('')
      setSingleOrder(0)
      setSingleError('')
      invalidateLists()
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }) => apiClient.put(`/admin/reference-channels/${id}`, payload).then(response => response.data),
    onSuccess: () => {
      setEditingId(null)
      setEditForm(null)
      invalidateLists()
    },
  })

  const deactivateMutation = useMutation({
    mutationFn: id => apiClient.delete(`/admin/reference-channels/${id}`).then(response => response.data),
    onSuccess: invalidateLists,
  })

  const revalidateMutation = useMutation({
    mutationFn: id => apiClient.post(`/admin/reference-channels/${id}/revalidate`).then(response => response.data),
    onSuccess: invalidateLists,
  })

  const bulkPreviewMutation = useMutation({
    mutationKey: ['reference-channel-bulk-preview'],
    mutationFn: entries => apiClient
      .post('/admin/reference-channels/bulk-preview', entries.filter(entry => !entry.uncertain).map(entry => entry.query))
      .then(response => ({ entries, previews: response.data })),
    onSuccess: ({ entries, previews }) => {
      const previewByQuery = new Map((Array.isArray(previews) ? previews : []).map(row => [row.query, row]))
      const rows = entries.map(entry => entry.uncertain
        ? { ...entry, candidates: [], errorMessage: 'OCR 불확실 항목 — 사람이 이름을 확인하기 전에는 선택할 수 없습니다.' }
        : { ...entry, ...(previewByQuery.get(entry.query) || { candidates: [], errorMessage: '검색 응답이 없습니다.' }) })
      setBulkRows(rows)
      setBulkSelections({})
      setBulkResult(null)
      setBulkError('')
      queryClient.setQueryData(['reference-channel-bulk-preview', entries.map(entry => entry.query)], rows)
    },
    onError: error => {
      setBulkRows([])
      setBulkSelections({})
      setBulkError(apiError(error, '채널 후보 검색에 실패했습니다.'))
    },
  })

  const bulkConfirmMutation = useMutation({
    mutationFn: payload => apiClient.post('/admin/reference-channels/bulk-confirm', payload).then(response => response.data),
    onSuccess: data => {
      setBulkResult(data)
      setBulkSelections({})
      invalidateLists()
    },
  })

  const productionChannelsQuery = useQuery({
    queryKey: ['production-channels'],
    queryFn: () => apiClient.get('/channels').then(response => response.data),
    staleTime: 1000 * 60 * 10,
  })
  const productionChannels = productionChannelsQuery.data || []
  const ownerLabel = ownerId => (ownerId ? (productionChannels.find(ch => ch.channelId === ownerId)?.channelName || ownerId) : '공용')

  const selectedItems = bulkRows.flatMap(row => {
    const candidate = bulkSelections[row.query]
    if (!candidate?.channel_id) return []
    return [{
      displayName: row.query,
      channelId: candidate.channel_id,
      displayOrder: row.displayOrder,
    }]
  })

  const beginEdit = channel => {
    setEditingId(channel.id)
    setEditForm({
      displayName: channel.displayName,
      tier: channel.tier,
      displayOrder: channel.displayOrder,
      active: channel.active,
      ownerChannelId: channel.ownerChannelId || '',
    })
  }

  return (
    <div className="space-y-6" data-testid="reference-channel-manager">
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-4">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck className="text-cyan-700" size={21} />
              <h2 className="text-lg font-bold text-slate-900">레퍼런스 채널 관리</h2>
            </div>
            <p className="mt-1 text-xs font-semibold text-slate-500">검증된 YouTube 채널만 벤치마크에 사용합니다.</p>
          </div>
          <button
            type="button"
            onClick={() => setShowInactive(value => !value)}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-100"
          >
            {showInactive ? <EyeOff size={15} /> : <Eye size={15} />}
            {showInactive ? '활성 채널만 보기' : '비활성 채널도 보기'}
          </button>
        </div>

        {channelsQuery.isLoading && <div className="py-10 text-center text-sm font-semibold text-slate-500">채널 목록을 불러오는 중입니다.</div>}
        {channelsQuery.isError && <ErrorBox message={apiError(channelsQuery.error, '채널 목록을 불러오지 못했습니다.')} />}
        {!channelsQuery.isLoading && !channelsQuery.isError && visibleChannels.length === 0 && (
          <div className="py-10 text-center text-sm font-bold text-slate-500">등록된 활성 레퍼런스 채널이 없습니다.</div>
        )}
        {visibleChannels.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-xs">
              <thead className="border-b-2 border-slate-200 bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-3 py-3">표시명 / YouTube 채널</th>
                  <th className="px-3 py-3">구독자</th>
                  <th className="px-3 py-3">Tier</th>
                  <th className="px-3 py-3">순서</th>
                  <th className="px-3 py-3">검증 상태</th>
                  <th className="px-3 py-3 text-right">관리</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {visibleChannels.map(channel => {
                  const editing = editingId === channel.id
                  return (
                    <tr key={channel.id} className={channel.active ? 'bg-white' : 'bg-slate-50 opacity-75'}>
                      <td className="px-3 py-3">
                        {editing ? (
                          <div className="space-y-1.5">
                            <input value={editForm.displayName} onChange={event => setEditForm({ ...editForm, displayName: event.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5 font-bold" />
                            <select value={editForm.ownerChannelId} onChange={event => setEditForm({ ...editForm, ownerChannelId: event.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5 font-bold">
                              <option value="">공용 (모든 제작 채널)</option>
                              {productionChannels.map(ch => <option key={ch.channelId} value={ch.channelId}>{ch.channelName}</option>)}
                            </select>
                          </div>
                        ) : (
                          <>
                            <div className="font-bold text-slate-900">{channel.displayName}</div>
                            <div className="mt-0.5 text-slate-600">{channel.youtubeTitle || 'YouTube 제목 없음'} · {channel.youtubeHandle || 'handle 없음'}</div>
                            <div className="mt-0.5 break-all text-[10px] text-slate-400">{channel.channelId}</div>
                            <div className="mt-0.5 text-[10px] font-bold text-cyan-700">적용 채널: {ownerLabel(channel.ownerChannelId)}</div>
                          </>
                        )}
                      </td>
                      <td className="px-3 py-3 font-bold text-slate-700">{subscriberText(channel.subscriberCount, channel.subscriberCountHidden)}</td>
                      <td className="px-3 py-3">
                        {editing ? (
                          <select value={editForm.tier} onChange={event => setEditForm({ ...editForm, tier: event.target.value })} className="rounded-lg border border-slate-300 px-2 py-1.5 font-bold">
                            {TIERS.map(tier => <option key={tier}>{tier}</option>)}
                          </select>
                        ) : <span className="rounded-full bg-cyan-50 px-2 py-1 font-bold text-cyan-800">{channel.tier}</span>}
                      </td>
                      <td className="px-3 py-3">
                        {editing ? (
                          <input type="number" value={editForm.displayOrder} onChange={event => setEditForm({ ...editForm, displayOrder: Number(event.target.value) })} className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 font-bold" />
                        ) : channel.displayOrder}
                      </td>
                      <td className="px-3 py-3">
                        <span className={`rounded-full px-2 py-1 font-bold ${channel.validationStatus === 'VALID' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>{channel.validationStatus}</span>
                        {!channel.active && <span className="ml-1 rounded-full bg-slate-200 px-2 py-1 font-bold text-slate-700">비활성</span>}
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex justify-end gap-1.5">
                          {editing ? (
                            <>
                              <button type="button" onClick={() => updateMutation.mutate({ id: channel.id, payload: editForm })} disabled={!editForm.displayName.trim() || updateMutation.isPending} className="rounded-lg bg-emerald-600 px-2.5 py-1.5 font-bold text-white disabled:opacity-50"><Check size={13} /></button>
                              <button type="button" onClick={() => { setEditingId(null); setEditForm(null) }} className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-slate-600"><X size={13} /></button>
                            </>
                          ) : (
                            <>
                              <button type="button" onClick={() => beginEdit(channel)} className="rounded-lg border border-slate-300 px-2.5 py-1.5 font-bold text-slate-700"><Edit3 size={13} /></button>
                              {channel.active ? (
                                <button type="button" onClick={() => deactivateMutation.mutate(channel.id)} disabled={deactivateMutation.isPending} className="rounded-lg border border-rose-300 bg-rose-50 px-2.5 py-1.5 font-bold text-rose-700">비활성화</button>
                              ) : (
                                <button type="button" onClick={() => updateMutation.mutate({ id: channel.id, payload: { displayName: channel.displayName, tier: channel.tier, displayOrder: channel.displayOrder, active: true } })} className="rounded-lg border border-emerald-300 bg-emerald-50 px-2.5 py-1.5 font-bold text-emerald-700">재활성화</button>
                              )}
                              <button type="button" onClick={() => revalidateMutation.mutate(channel.id)} disabled={revalidateMutation.isPending} className="rounded-lg border border-cyan-300 bg-cyan-50 px-2.5 py-1.5 font-bold text-cyan-800">재검증</button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <Plus className="text-cyan-700" size={19} />
          <div>
            <h3 className="text-base font-bold text-slate-900">단일 채널 추가</h3>
            <p className="text-xs font-semibold text-slate-500">일반 채널명은 아래 일괄 등록 검색을 이용하세요.</p>
          </div>
        </div>
        <div className="flex flex-col gap-2 lg:flex-row">
          <input
            value={singleRef}
            onChange={event => { setSingleRef(event.target.value); setSinglePreview(null); setSingleError('') }}
            placeholder="채널 ID (UC...), @handle, 또는 YouTube 채널 URL을 입력하세요"
            className="min-w-0 flex-1 rounded-xl border border-slate-300 bg-slate-50 px-3.5 py-2.5 text-xs font-semibold text-slate-900"
          />
          <button
            type="button"
            onClick={() => singlePreviewMutation.mutate(singleRef.trim())}
            disabled={!singleRef.trim() || singlePreviewMutation.isPending}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-600 px-4 py-2.5 text-xs font-bold text-white disabled:opacity-50"
          >
            {singlePreviewMutation.isPending ? <Loader2 className="animate-spin" size={15} /> : <Search size={15} />}
            실제 채널 확인
          </button>
        </div>
        {singleError && <ErrorBox message={singleError} />}
        {singlePreview && (
          <div className="mt-4 rounded-xl border border-cyan-200 bg-cyan-50/40 p-4">
            <p className="mb-3 text-xs font-bold text-cyan-900">아래 실제 YouTube 정보를 확인한 뒤에만 추가하세요.</p>
            <CandidateCard candidate={singlePreview} query={singleDisplayName} selected disabled />
            <div className="mt-3 grid gap-2 md:grid-cols-[1fr_120px_auto]">
              <input value={singleDisplayName} onChange={event => setSingleDisplayName(event.target.value)} placeholder="관리 화면 표시명" className="rounded-xl border border-slate-300 px-3 py-2 text-xs font-bold" />
              <input type="number" value={singleOrder} onChange={event => setSingleOrder(Number(event.target.value))} placeholder="순서" className="rounded-xl border border-slate-300 px-3 py-2 text-xs font-bold" />
              <button
                type="button"
                onClick={() => createMutation.mutate({ displayName: singleDisplayName.trim(), channelRef: singlePreview.channel_id, displayOrder: singleOrder })}
                disabled={!singleDisplayName.trim() || createMutation.isPending}
                className="rounded-xl bg-emerald-600 px-4 py-2 text-xs font-bold text-white disabled:opacity-50"
              >
                {createMutation.isPending ? '추가 중...' : '추가 확정'}
              </button>
            </div>
            {createMutation.isError && <ErrorBox message={apiError(createMutation.error, '채널 추가에 실패했습니다.')} />}
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm" data-testid="bulk-reference-channel-panel">
        <div className="mb-4">
          <h3 className="text-base font-bold text-slate-900">48개 레퍼런스 채널 일괄 등록</h3>
          <p className="mt-1 text-xs font-semibold text-slate-500">검색 결과는 자동 저장되지 않습니다. 제목·handle·구독자 수를 비교해 사람이 후보를 선택해야 합니다.</p>
        </div>
        <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4 text-xs font-bold leading-6 text-amber-950" data-testid="bulk-quota-warning">
          ⚠️ 48개 채널 이름 검색은 YouTube 검색 쿼터(100회/일)의 48%를 소모합니다.<br />
          하루 1회, 업무 외 시간(자정~오전 9시 권장)에 실행하세요. 같은 날 재실행이 필요하면 관리자에게 문의하세요.
        </div>
        <textarea
          rows={10}
          value={bulkText}
          onChange={event => setBulkText(event.target.value)}
          className="w-full rounded-xl border border-slate-300 bg-slate-50 p-3 text-xs font-semibold leading-6 text-slate-800"
          aria-label="일괄 검색 채널 이름 목록"
        />
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-semibold text-slate-500">OCR 불확실 19번·31번은 검색 및 선택에서 자동 제외됩니다.</span>
          <button
            type="button"
            onClick={() => bulkPreviewMutation.mutate(parseBulkEntries(bulkText))}
            disabled={bulkPreviewMutation.isPending || parseBulkEntries(bulkText).filter(entry => !entry.uncertain).length === 0}
            className="inline-flex items-center gap-2 rounded-xl bg-cyan-600 px-4 py-2.5 text-xs font-bold text-white disabled:opacity-50"
          >
            {bulkPreviewMutation.isPending ? <Loader2 className="animate-spin" size={15} /> : <Search size={15} />}
            {bulkPreviewMutation.isPending ? '후보 검색 중...' : '이름별 후보 검색'}
          </button>
        </div>
        {bulkError && <ErrorBox message={bulkError} />}

        {bulkRows.length > 0 && (
          <div className="mt-5 space-y-3" data-testid="bulk-preview-results">
            {bulkRows.map((row, index) => (
              <div key={`${row.query}-${index}`} className={`rounded-xl border p-4 ${row.uncertain ? 'border-amber-300 bg-amber-50/60' : 'border-slate-200 bg-slate-50/50'}`}>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-slate-900 px-2 py-0.5 text-[10px] font-bold text-white">#{index + 1}</span>
                    <span className="text-sm font-bold text-slate-900">{row.query}</span>
                    {row.uncertain && <span className="rounded-full bg-amber-200 px-2 py-0.5 text-[10px] font-bold text-amber-900">기본 미선택</span>}
                  </div>
                  <span className="text-[10px] font-semibold text-slate-500">후보 {row.candidates?.length || 0}개</span>
                </div>
                {row.errorMessage && <ErrorBox message={row.errorMessage} compact />}
                {row.candidates?.length > 0 && (
                  <div className="grid gap-2 xl:grid-cols-3">
                    {row.candidates.map(candidate => (
                      <CandidateCard
                        key={candidate.channel_id}
                        candidate={candidate}
                        query={row.query}
                        selected={bulkSelections[row.query]?.channel_id === candidate.channel_id}
                        onSelect={() => setBulkSelections(current => ({ ...current, [row.query]: candidate }))}
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
            <div className="sticky bottom-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-cyan-300 bg-white/95 p-4 shadow-lg backdrop-blur">
              <span className="text-xs font-bold text-slate-700">선택된 후보 {selectedItems.length}개 · 선택되지 않은 항목은 저장되지 않습니다.</span>
              <button
                type="button"
                onClick={() => bulkConfirmMutation.mutate(selectedItems)}
                disabled={selectedItems.length === 0 || bulkConfirmMutation.isPending}
                className="rounded-xl bg-emerald-600 px-5 py-2.5 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                {bulkConfirmMutation.isPending ? '검증 후 저장 중...' : '선택 후보 일괄 확정'}
              </button>
            </div>
          </div>
        )}
        {bulkConfirmMutation.isError && <ErrorBox message={apiError(bulkConfirmMutation.error, '일괄 등록에 실패했습니다.')} />}
        {bulkResult && (
          <div className="mt-4 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-xs font-bold text-emerald-900">
            저장 성공 {bulkResult.succeeded?.length || 0}개 · 실패 {bulkResult.failed?.length || 0}개
          </div>
        )}
      </section>
    </div>
  )
}

function ErrorBox({ message, compact = false }) {
  return (
    <div className={`${compact ? 'mt-2 p-2' : 'mt-3 p-3'} flex items-start gap-2 rounded-xl border border-rose-300 bg-rose-50 text-xs font-bold text-rose-800`}>
      <AlertTriangle className="mt-0.5 shrink-0" size={14} />
      <span>{message}</span>
    </div>
  )
}
