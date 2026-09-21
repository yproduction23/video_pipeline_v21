import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, DollarSign, Film, ListFilter, Plus, Search, Video } from 'lucide-react'
import Layout from '../components/Layout'
import Pagination from '../components/Pagination'
import BenchmarkDiscovery from '../components/dashboard/BenchmarkDiscovery'
import ChannelBenchmark from '../components/dashboard/ChannelBenchmark'
import { jobsApi } from '../api/jobs'
import apiClient from '../api/client'
import { formatAutonomy, formatCategory, formatStatus } from '../constants/jobStatus'

const STATUS_CLASS = {
  READY: 'bg-accent-green/15 text-accent-green',
  PUBLISH_PENDING: 'bg-accent-amber/15 text-accent-amber',
  PUBLISHED: 'bg-accent-green/15 text-accent-green',
  FAILED: 'bg-accent-red/15 text-accent-red',
  BUDGET_BLOCKED: 'bg-accent-red/15 text-accent-red',
}

const CATEGORY_LABEL = {
  KOSPI: '코스피', KOSDAQ: '코스닥', US_STOCKS: '미국 주식',
  INDIVIDUAL_STOCK: '개별 종목', GLOBAL_MACRO: '글로벌 매크로', CRYPTO: '가상자산', CUSTOM: '직접 입력',
}

function formatDate(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium' }).format(new Date(value))
}

function isShortsSourceJob(job) {
  return job.renderProfile === 'SHORTS_9x16' || String(job.title || '').startsWith('쇼츠:')
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [type, setType] = useState('ALL')
  const [category, setCategory] = useState('ALL')
  const [mode, setMode] = useState('ALL')
  const [length, setLength] = useState('ALL')
  const [page, setPage] = useState(1)

  const jobsQuery = useQuery({ queryKey: ['jobs'], queryFn: jobsApi.list, refetchInterval: 15000 })
  const shortsQuery = useQuery({ queryKey: ['shorts'], queryFn: () => apiClient.get('/shorts').then(r => r.data), refetchInterval: 15000 })
  const jobs = jobsQuery.data || []
  const shorts = shortsQuery.data || []

  const assets = useMemo(() => {
    const longforms = jobs.filter(job => !isShortsSourceJob(job)).map(job => ({
      id: `longform-${job.id}`,
      type: 'LONGFORM',
      title: job.title || '제목 없는 롱폼 작업',
      keyword: job.keyword || '',
      category: job.category || 'CUSTOM',
      mode: job.autonomy || '-',
      status: job.status || 'DRAFT',
      minutes: Number(job.longformTargetMinutes) || 0,
      cost: Number(job.costAccumulated) || 0,
      budget: Number(job.budgetCap) || 0,
      createdAt: job.updatedAt || job.createdAt,
      href: `/longform/${job.id}`,
    }))
    const legacyShorts = jobs.filter(isShortsSourceJob).map(job => ({
      id: `legacy-shorts-${job.id}`,
      type: 'SHORTS',
      title: job.title || '제목 없는 쇼츠 작업',
      keyword: job.keyword || '',
      category: job.category || 'CUSTOM',
      mode: job.autonomy || '-',
      status: job.status || 'DRAFT',
      minutes: 0,
      cost: Number(job.costAccumulated) || 0,
      budget: Number(job.budgetCap) || 0,
      createdAt: job.updatedAt || job.createdAt,
      href: `/longform/${job.id}/shorts`,
    }))
    const shortProjects = shorts.map(project => ({
      id: `shorts-${project.id}`,
      type: 'SHORTS',
      title: project.title || '제목 없는 쇼츠 프로젝트',
      keyword: '',
      category: 'CUSTOM',
      mode: '-',
      status: project.status || 'EDITING',
      minutes: 0,
      cost: null,
      budget: null,
      createdAt: project.updatedAt || project.createdAt,
      href: `/shorts/${project.id}`,
    }))
    return [...longforms, ...legacyShorts, ...shortProjects]
      .sort((a, b) => new Date(b.createdAt || 0) - new Date(a.createdAt || 0))
  }, [jobs, shorts])

  const filtered = assets.filter(item => {
    const needle = search.trim().toLowerCase()
    if (needle && !`${item.title} ${item.keyword}`.toLowerCase().includes(needle)) return false
    if (type !== 'ALL' && item.type !== type) return false
    if (category !== 'ALL' && item.category !== category) return false
    if (mode !== 'ALL' && item.mode !== mode) return false
    if (length === 'SHORTS' && item.type !== 'SHORTS') return false
    if (length === '1_5' && !(item.type === 'LONGFORM' && item.minutes <= 5)) return false
    if (length === '6_15' && !(item.type === 'LONGFORM' && item.minutes >= 6 && item.minutes <= 15)) return false
    if (length === '16_PLUS' && !(item.type === 'LONGFORM' && item.minutes >= 16)) return false
    return true
  })
  const visible = filtered.slice((page - 1) * 10, page * 10)
  const totalPages = Math.max(1, Math.ceil(filtered.length / 10))
  const totalCost = jobs.reduce((sum, job) => sum + (Number(job.costAccumulated) || 0), 0)
  const longformCount = assets.filter(item => item.type === 'LONGFORM').length
  const shortsCount = assets.filter(item => item.type === 'SHORTS').length

  const update = (setter) => (value) => { setter(value); setPage(1) }
  useEffect(() => { if (page > totalPages) setPage(totalPages) }, [page, totalPages])

  return (
    <Layout>
      <div className="w-full max-w-none space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-accent-cyan">작업 아카이브</p>
            <h1 className="text-2xl font-bold text-slate-900 mt-1">모든 영상 작업</h1>
            <p className="text-sm text-slate-500 mt-2">롱폼과 쇼츠를 한곳에서 찾아보고, 상세 작업으로 바로 이어갑니다.</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => navigate('/shorts/new')} className="border border-slate-300 text-slate-700 rounded-xl px-4 py-2.5 text-sm font-semibold hover:bg-slate-50">쇼츠 만들기</button>
            <button onClick={() => navigate('/longform/new')} className="bg-accent-cyan text-navy-950 rounded-xl px-4 py-2.5 text-sm font-semibold flex items-center gap-2"><Plus size={16}/>롱폼 만들기</button>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <SummaryCard icon={<Video size={19}/>} label="롱폼 작업" value={longformCount} onClick={() => update(setType)('LONGFORM')} />
          <SummaryCard icon={<Film size={19}/>} label="쇼츠 프로젝트" value={shortsCount} onClick={() => update(setType)('SHORTS')} />
          <SummaryCard icon={<DollarSign size={19}/>} label="누적 사용 비용" value={`₩${Math.round(totalCost).toLocaleString('ko-KR')}`} onClick={() => { setType('ALL'); setPage(1) }} />
        </div>

        <ChannelBenchmark />

        <BenchmarkDiscovery compact />

        {(jobsQuery.isError || shortsQuery.isError) && <div className="rounded-xl border border-accent-gold/40 bg-accent-gold/10 px-4 py-3 text-sm text-accent-gold">일부 목록을 불러오지 못했습니다. 서버가 실행 중인지 확인한 뒤 새로고침해 주세요.</div>}

        <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="p-5 border-b border-slate-200">
            <div className="flex items-center gap-2 text-slate-900 font-semibold"><ListFilter size={18} className="text-accent-cyan"/>검색 및 필터</div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3 mt-4">
              <label className="relative xl:col-span-1"><Search size={16} className="absolute left-3 top-3 text-slate-500"/><input value={search} onChange={e => update(setSearch)(e.target.value)} placeholder="제목 또는 키워드" className="w-full rounded-xl bg-slate-100 border border-slate-300 pl-9 pr-3 py-2.5 text-sm text-slate-900" /></label>
              <Select value={type} onChange={update(setType)} options={[["ALL","형식: 전체"],["LONGFORM","롱폼"],["SHORTS","쇼츠"]]} />
              <Select value={length} onChange={update(setLength)} options={[["ALL","길이: 전체"],["SHORTS","쇼츠"],["1_5","롱폼 1~5분"],["6_15","롱폼 6~15분"],["16_PLUS","롱폼 16분+"]]} />
              <Select value={category} onChange={update(setCategory)} options={[["ALL","주제: 전체"], ...Object.entries(CATEGORY_LABEL)]} />
              <Select value={mode} onChange={update(setMode)} options={[["ALL","모드: 전체"],["AUTO","자동"],["GUIDED","반자동"]]} />
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[850px] text-sm">
              <thead className="text-left text-xs text-slate-500 bg-slate-100/50"><tr><th className="px-5 py-3">영상</th><th className="px-3 py-3">형식</th><th className="px-3 py-3">주제 · 길이</th><th className="px-3 py-3">모드</th><th className="px-3 py-3">상태</th><th className="px-3 py-3 text-right">비용</th><th className="px-5 py-3 text-right">최근 수정</th></tr></thead>
              <tbody className="divide-y divide-slate-200">
                {visible.map(item => <tr key={item.id} onClick={() => navigate(item.href)} className="cursor-pointer hover:bg-slate-50 transition">
                  <td className="px-5 py-4"><div className="font-semibold text-slate-900 max-w-[300px] truncate">{item.title}</div><div className="text-xs text-slate-500 mt-1 max-w-[300px] truncate">{item.keyword || '키워드 없음'}</div></td>
                  <td className="px-3 py-4"><span className={`rounded-full px-2 py-1 text-xs font-semibold ${item.type === 'LONGFORM' ? 'bg-accent-cyan/10 text-accent-cyan' : 'bg-accent-violet/15 text-accent-violet'}`}>{item.type === 'LONGFORM' ? '롱폼' : '쇼츠'}</span></td>
                  <td className="px-3 py-4 text-slate-700">{formatCategory(item.category)}<div className="text-xs text-slate-500 mt-1">{item.type === 'LONGFORM' ? `${item.minutes}분` : '세로 쇼츠'}</div></td>
                  <td className="px-3 py-4 text-slate-700">{formatAutonomy(item.mode)}</td>
                  <td className="px-3 py-4"><span className={`rounded-full px-2 py-1 text-xs font-semibold ${STATUS_CLASS[item.status] || 'bg-navy-700 text-slate-700'}`}>{formatStatus(item.status)}</span></td>
                  <td className="px-3 py-4 text-right text-slate-700">{item.cost == null ? '-' : `₩${Math.round(Number(item.cost) || 0).toLocaleString('ko-KR')}`}</td>
                  <td className="px-5 py-4 text-right text-xs text-slate-500">{formatDate(item.createdAt)}</td>
                </tr>)}
                {!jobsQuery.isLoading && !shortsQuery.isLoading && visible.length === 0 && <tr><td colSpan="7" className="px-5 py-16 text-center text-slate-500">조건에 맞는 영상 작업이 없습니다.</td></tr>}
              </tbody>
            </table>
          </div>
          <Pagination total={filtered.length} currentPage={page} onChange={setPage} />
        </section>

        <div className="grid md:grid-cols-2 gap-4">
          <FlowCard title="롱폼 제작실" text="주제·키워드 조사, YouTube 비교, 스크립트·TTS·장면 생성과 재조립을 진행합니다." action="롱폼 작업으로" onClick={() => navigate('/longform')} />
          <FlowCard title="쇼츠 제작실" text="직접 쇼츠를 만들거나, 완성된 롱폼 상세 화면에서 핵심 구간을 쇼츠로 전환합니다." action="쇼츠 작업으로" onClick={() => navigate('/shorts')} />
        </div>
      </div>
    </Layout>
  )
}

function Select({ value, onChange, options }) {
  return <select value={value} onChange={e => onChange(e.target.value)} className="w-full rounded-xl bg-slate-100 border border-slate-300 px-3 py-2.5 text-sm text-slate-900">{options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
}

function SummaryCard({ icon, label, value, onClick }) {
  return <button onClick={onClick} className="text-left bg-white border border-slate-200 hover:border-accent-cyan/60 rounded-xl p-5 transition"><div className="text-accent-cyan">{icon}</div><div className="text-2xl font-bold text-slate-900 mt-4">{value}</div><div className="text-sm text-slate-500 mt-1">{label}</div></button>
}

function FlowCard({ title, text, action, onClick }) {
  return <button onClick={onClick} className="text-left bg-white border border-slate-200 hover:border-accent-cyan/60 rounded-xl p-5 transition"><h2 className="font-semibold text-slate-900">{title}</h2><p className="text-sm text-slate-500 mt-2 leading-6">{text}</p><span className="inline-flex items-center gap-1 text-sm font-semibold text-accent-cyan mt-4">{action}<ArrowRight size={15}/></span></button>
}
