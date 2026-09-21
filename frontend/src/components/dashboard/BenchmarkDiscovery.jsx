import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Clock, Eye, Flame, Search, TrendingUp, Users, Youtube, Zap } from 'lucide-react'
import apiClient from '../../api/client'
import { jobsApi } from '../../api/jobs'
import { discoveryApi } from '../../api/discovery'

const FALLBACK_CATEGORIES = [{ key: 'ALL', label: '전체 인기' }]
const WINDOWS = [{ id: '48h', label: '48시간 급상승' }, { id: '7d', label: '7일 지속' }]
const TABS = [{ id: 'hot', label: '핫키워드' }, { id: 'search', label: '직접 검색' }, { id: 'channels', label: '벤치마크 채널 신작' }]

const pick = (video, camel, snake) => video?.[camel] ?? video?.[snake]
const formatNumber = (num) => {
  if (!num) return '0'
  if (num >= 10000) return `${(num / 10000).toFixed(1)}만`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}천`
  return String(Math.round(num))
}

export function normalizeVideo(video) {
  const hours = Number(pick(video, 'hoursSincePublish', 'hours_since_publish'))
  const views = Number(video.views || 0)
  const subscribers = Number(video.subscribers || 0)
  return {
    videoId: pick(video, 'videoId', 'video_id') || '',
    title: video.title || '제목 없음',
    channelTitle: pick(video, 'channelTitle', 'channel_title') || '',
    views,
    subscribers,
    hours: Number.isFinite(hours) ? hours : null,
    viewsPerHour: video.viewsPerHour ?? (Number.isFinite(hours) ? Math.round(views / Math.max(hours, 1)) : null),
    outperformance: video.outperformanceIndex ?? null,
    tags: video.tags || [],
  }
}

function PersistenceBadge({ persistence }) {
  if (persistence === 'sustained') {
    return <span className="inline-flex items-center gap-0.5 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-800"><Flame size={10} />지속 중</span>
  }
  if (persistence === 'spike') {
    return <span className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"><Zap size={10} />순간 급등</span>
  }
  return null
}

export default function BenchmarkDiscovery() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('hot')
  const [category, setCategory] = useState('ALL')
  const [windowId, setWindowId] = useState('48h')
  const [keywordFilter, setKeywordFilter] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [selected, setSelected] = useState(null)
  const [productionChannelId, setProductionChannelId] = useState('')

  const channelsQuery = useQuery({
    queryKey: ['production-channels'],
    queryFn: () => apiClient.get('/channels').then(r => r.data),
    staleTime: 1000 * 60 * 10,
  })
  const productionChannels = channelsQuery.data || []
  const effectiveChannelId = productionChannelId || productionChannels[0]?.channelId || ''

  const hotQuery = useQuery({
    queryKey: ['discovery-hot', category, windowId],
    queryFn: () => discoveryApi.hotKeywords(category, windowId),
    enabled: tab === 'hot',
    staleTime: 1000 * 60 * 30,
    retry: false,
  })
  const searchQuery = useQuery({
    queryKey: ['discovery-search', searchKeyword],
    queryFn: () => jobsApi.trendingYoutube(searchKeyword),
    enabled: tab === 'search' && !!searchKeyword,
    staleTime: 1000 * 60 * 30,
    retry: false,
  })
  const uploadsQuery = useQuery({
    queryKey: ['discovery-uploads', effectiveChannelId],
    queryFn: () => discoveryApi.recentUploads(effectiveChannelId),
    enabled: tab === 'channels' && !!effectiveChannelId,
    staleTime: 1000 * 60 * 15,
    retry: false,
  })

  const activeQuery = tab === 'hot' ? hotQuery : tab === 'search' ? searchQuery : uploadsQuery
  const rawVideos = tab === 'hot' ? hotQuery.data?.videos : tab === 'search'
    ? (Array.isArray(searchQuery.data) ? searchQuery.data : searchQuery.data?.videos)
    : uploadsQuery.data?.videos
  const videos = useMemo(() => {
    const rows = (rawVideos || []).map(normalizeVideo)
    if (tab !== 'hot' || !keywordFilter) return rows
    const needle = keywordFilter.toLowerCase()
    return rows.filter(v => v.title.toLowerCase().includes(needle) || v.tags.some(t => String(t).toLowerCase().includes(needle)))
  }, [rawVideos, tab, keywordFilter])

  const categories = hotQuery.data?.categories || FALLBACK_CATEGORIES
  const errorMessage = activeQuery.isError
    ? (activeQuery.error?.response?.data?.message || activeQuery.error?.response?.data?.detail || 'YouTube 데이터를 불러오지 못했습니다.')
    : null

  const submitSearch = () => {
    const keyword = searchInput.trim()
    if (!keyword) return
    setSearchKeyword(keyword)
    setSelected(null)
  }
  const backToHot = () => { setTab('hot'); setSearchInput(''); setSearchKeyword(''); setSelected(null) }
  const startBenchmark = () => {
    if (!selected || !effectiveChannelId) return
    navigate('/longform/new', {
      state: {
        benchmark: { videoId: selected.videoId, title: selected.title, channelTitle: selected.channelTitle },
        channelId: effectiveChannelId,
      },
    })
  }

  return (
    <section className="bg-navy-800 rounded-xl border border-slate-200 overflow-hidden">
      <div className="p-5 border-b border-slate-200 space-y-4">
        <div className="flex items-center gap-2">
          <Youtube className="text-red-500" size={20} />
          <div>
            <h2 className="font-bold text-sm text-white">지금 뜨는 핫키워드 · 벤치마크</h2>
            <p className="text-xs text-slate-500 mt-1">잘 되는 영상을 골라 그 영상을 기준으로 바로 제작을 시작하세요.</p>
          </div>
        </div>

        <div className="flex gap-2 overflow-x-auto">
          {TABS.map(item => (
            <button key={item.id} type="button" onClick={() => { setTab(item.id); setSelected(null) }}
              className={`shrink-0 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${tab === item.id ? 'border-red-500 bg-red-500 text-white' : 'border-slate-300 text-slate-500 hover:border-red-500/60'}`}>
              {item.label}
            </button>
          ))}
        </div>

        {tab === 'hot' && (
          <>
            <div className="flex gap-2 flex-wrap">
              {categories.map(item => (
                <button key={item.key} type="button" onClick={() => { setCategory(item.key); setKeywordFilter(''); setSelected(null) }}
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${category === item.key ? 'border-cyan-600 bg-cyan-50 text-cyan-800' : 'border-slate-300 text-slate-600 hover:border-cyan-500'}`}>
                  {item.label}
                </button>
              ))}
            </div>
            <div className="inline-flex overflow-hidden rounded-lg border border-slate-300">
              {WINDOWS.map(item => (
                <button key={item.id} type="button" onClick={() => { setWindowId(item.id); setKeywordFilter('') }}
                  className={`px-3 py-1.5 text-xs font-semibold ${windowId === item.id ? 'bg-cyan-50 text-cyan-800' : 'text-slate-600'}`}>
                  {item.label}
                </button>
              ))}
            </div>
            {hotQuery.data?.keywords?.length > 0 && (
              <div className="flex gap-2 flex-wrap">
                {hotQuery.data.keywords.slice(0, 16).map(item => (
                  <button key={item.keyword} type="button" onClick={() => setKeywordFilter(keywordFilter === item.keyword ? '' : item.keyword)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${keywordFilter === item.keyword ? 'border-red-500 bg-red-50 text-red-700' : 'border-slate-300 text-slate-700 hover:border-red-400'}`}>
                    {item.keyword}<PersistenceBadge persistence={item.persistence} />
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {tab === 'search' && (
          <div className="flex gap-2 items-center">
            {searchKeyword && (
              <button type="button" onClick={backToHot} className="inline-flex items-center gap-1 rounded-xl border border-slate-300 px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50">
                <ArrowLeft size={13} />핫키워드로 돌아가기
              </button>
            )}
            <input value={searchInput} onChange={e => setSearchInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && submitSearch()}
              placeholder="직접 키워드 검색" className="flex-1 rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-900" />
            <button type="button" onClick={submitSearch} className="inline-flex items-center gap-1 rounded-xl bg-cyan-700 px-4 py-2 text-xs font-bold text-white">
              <Search size={13} />검색
            </button>
          </div>
        )}

        {tab === 'channels' && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="font-bold">제작 채널 기준</span>
            <select value={effectiveChannelId} onChange={e => setProductionChannelId(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-2 py-1 font-bold text-slate-800">
              {productionChannels.map(ch => <option key={ch.channelId} value={ch.channelId}>{ch.channelName}</option>)}
            </select>
            <span>이 채널에 등록된 벤치마크 채널과 공용 채널의 최근 7일 업로드</span>
          </div>
        )}
      </div>

      {activeQuery.isFetching && <div className="flex h-32 items-center justify-center text-sm text-slate-500">수집 중...</div>}
      {errorMessage && <div className="px-5 py-8 text-center text-sm text-red-500">{errorMessage}</div>}
      {tab === 'channels' && !activeQuery.isFetching && uploadsQuery.data?.empty && (
        <div className="px-5 py-8 text-center text-sm text-slate-500">이 채널에 등록된 벤치마크 채널이 없습니다. 관리자 화면 "레퍼런스 채널"에서 적용 채널을 지정해 주세요.</div>
      )}
      {!activeQuery.isFetching && !errorMessage && videos.length === 0 && !(tab === 'channels' && uploadsQuery.data?.empty) && !(tab === 'search' && !searchKeyword) && (
        <div className="px-5 py-8 text-center text-sm text-slate-500">조건에 맞는 영상이 없습니다.</div>
      )}

      {!activeQuery.isFetching && videos.length > 0 && (
        <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 xl:grid-cols-4">
          {videos.map((video, index) => (
            <button key={video.videoId || index} type="button" onClick={() => setSelected(video)}
              className={`text-left rounded-lg border p-2 transition ${selected?.videoId === video.videoId ? 'border-2 border-cyan-600' : 'border-slate-200 hover:border-red-400'}`}>
              <div className="relative aspect-video overflow-hidden rounded bg-navy-900">
                {video.videoId && <img src={`https://i.ytimg.com/vi/${video.videoId}/hqdefault.jpg`} alt="" className="h-full w-full object-cover" />}
                <span className="absolute left-2 top-2 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-bold text-white">#{index + 1}</span>
              </div>
              <p className="mt-2 line-clamp-2 min-h-8 text-xs font-bold leading-snug text-white">{video.title}</p>
              <p className="mt-1 truncate text-[11px] text-slate-500">{video.channelTitle || '채널 정보 없음'}</p>
              <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1 text-[10px] text-slate-500">
                <span className="flex items-center gap-1"><Eye size={10} />{formatNumber(video.views)}회</span>
                <span className="flex items-center gap-1"><TrendingUp size={10} />{video.viewsPerHour == null ? '-' : `${formatNumber(video.viewsPerHour)}/시간`}</span>
                <span className="flex items-center gap-1"><Users size={10} />{formatNumber(video.subscribers)}명</span>
                <span className="flex items-center gap-1"><Clock size={10} />{video.hours == null ? '-' : video.hours < 24 ? `${Math.floor(video.hours)}시간 전` : `${Math.floor(video.hours / 24)}일 전`}</span>
              </div>
              {video.outperformance != null && <p className="mt-1 text-[10px] font-bold text-emerald-700">채널 평균 대비 {video.outperformance}배</p>}
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="m-4 space-y-3 rounded-xl bg-slate-50 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="text-sm font-bold text-slate-900">선택한 벤치마크 영상</div>
            <div className="max-w-[60%] truncate text-xs text-slate-500">{selected.title}</div>
          </div>
          <ul className="list-disc space-y-1 pl-5 text-xs text-slate-700">
            <li>조회수 {formatNumber(selected.views)}회{selected.viewsPerHour != null && ` · 시간당 ${formatNumber(selected.viewsPerHour)}회`}</li>
            {selected.subscribers > 0 && <li>구독자 대비 조회율 {((selected.views / selected.subscribers) * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%</li>}
            {selected.outperformance != null && <li>이 채널의 최근 평균보다 {selected.outperformance}배</li>}
            {selected.tags.length > 0 && <li>태그: {selected.tags.slice(0, 6).join(', ')}</li>}
          </ul>
          <p className="text-[11px] text-slate-500">뜨는 이유 분석은 제작을 시작할 때 자동으로 수행되어 대본 설계에 반영됩니다.</p>
          <div className="flex flex-wrap items-center gap-2">
            <select value={effectiveChannelId} onChange={e => setProductionChannelId(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs font-bold text-slate-800">
              {productionChannels.map(ch => <option key={ch.channelId} value={ch.channelId}>{ch.channelName}</option>)}
            </select>
            <button type="button" onClick={startBenchmark} disabled={!effectiveChannelId}
              className="ml-auto rounded-xl bg-cyan-700 px-4 py-2 text-xs font-bold text-white hover:bg-cyan-800 disabled:opacity-50">
              이 영상으로 롱폼 제작 시작
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
