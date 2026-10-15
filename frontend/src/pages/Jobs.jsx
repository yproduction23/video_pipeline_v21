import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, BarChart3, ChevronRight, Plus, Search, Youtube } from 'lucide-react'
import Layout from '../components/Layout'
import Pagination from '../components/Pagination'
import DailyKeywordResearch from '../components/dashboard/DailyKeywordResearch'
import StatusBadge from '../components/StatusBadge'
import { jobsApi } from '../api/jobs'
import { formatAutonomy, formatCategory, isError } from '../constants/jobStatus'

const CATEGORY_LABEL = {
  KOSPI: '코스피', KOSDAQ: '코스닥', US_STOCKS: '미국 주식', INDIVIDUAL_STOCK: '개별 종목',
  GLOBAL_MACRO: '글로벌 매크로', CRYPTO: '가상자산', CUSTOM: '직접 입력',
}

function isShortsSourceJob(job) {
  return job.renderProfile === 'SHORTS_9x16' || String(job.title || '').startsWith('쇼츠:')
}

export default function Jobs() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('ALL')
  const [mode, setMode] = useState('ALL')
  const [attentionOnly, setAttentionOnly] = useState(false)
  const [page, setPage] = useState(1)
  const { data: allJobs = [], isLoading, isError: isLoadError } = useQuery({ queryKey: ['jobs'], queryFn: jobsApi.list, refetchInterval: 15000 })
  const attentionCount = allJobs.filter(job => !isShortsSourceJob(job) && isError(job.status)).length
  const jobs = allJobs.filter(job => !isShortsSourceJob(job)).filter(job => {
    const term = search.trim().toLowerCase()
    if (term && !`${job.title || ''} ${job.keyword || ''}`.toLowerCase().includes(term)) return false
    if (category !== 'ALL' && job.category !== category) return false
    if (mode !== 'ALL' && job.autonomy !== mode) return false
    if (attentionOnly && !isError(job.status)) return false
    return true
  }).sort((a, b) => {
    // 주의 필요 Job(IMAGES_RETRY_REQUIRED 등)을 목록 최상단으로 올려 전체
    // 스크롤 없이 바로 보이게 한다 — AUTO 모드에서 조용히 멈춘 Job을
    // 운영자가 리스트에서 눈으로 찾아야 했던 문제의 직접적인 보완.
    const attnDiff = Number(isError(b.status)) - Number(isError(a.status))
    if (attnDiff !== 0) return attnDiff
    return new Date(b.updatedAt || b.createdAt || 0) - new Date(a.updatedAt || a.createdAt || 0)
  })
  const totalPages = Math.max(1, Math.ceil(jobs.length / 10))
  const pageItems = jobs.slice((page - 1) * 10, page * 10)
  useEffect(() => { if (page > totalPages) setPage(totalPages) }, [page, totalPages])
  const resetPage = (setter) => (value) => { setter(value); setPage(1) }

  return (
    <Layout>
      <div className="w-full max-w-none space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><p className="text-sm font-semibold text-accent-cyan">롱폼 제작실</p><h1 className="text-2xl font-bold text-slate-900 mt-1">기획부터 재조립까지</h1><p className="text-sm text-slate-500 mt-2">키워드와 YouTube 지표를 비교한 뒤, 롱폼 작업을 생성하고 단계별로 검토합니다.</p></div>
          <button onClick={() => navigate('/longform/new')} className="bg-accent-cyan text-navy-950 rounded-xl px-4 py-2.5 text-sm font-semibold flex items-center gap-2"><Plus size={16}/>새 롱폼 작업</button>
        </div>

        <div className="grid md:grid-cols-3 gap-4">
          <ActionCard icon={<BarChart3 size={20}/>} title="키워드 · 주제 탐색" text="매일 오전 9시 갱신되는 후보와 직접 키워드를 비교합니다." action="아래 후보 보기" onClick={() => document.getElementById('keyword-research')?.scrollIntoView({ behavior: 'smooth' })} />
          <ActionCard icon={<Youtube size={20}/>} title="YouTube 비교 지표" text="조회수, 구독자 대비 조회수, 좋아요, 자체 스냅샷 기반 증가량을 확인합니다." action="후보 지표 보기" onClick={() => document.getElementById('keyword-research')?.scrollIntoView({ behavior: 'smooth' })} />
          <ActionCard icon={<Plus size={20}/>} title="롱폼 생성" text="길이·자동/반자동·채널·캐릭터·데이터 시각화를 설정하고 제작을 시작합니다." action="상세 설정 열기" onClick={() => navigate('/longform/new')} />
        </div>

        <div id="keyword-research"><DailyKeywordResearch
          onUseKeyword={(keyword) => navigate(`/longform/new?topic=${encodeURIComponent(keyword)}`)}
        /></div>

        <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="p-5 border-b border-slate-200 flex flex-wrap gap-3 items-center justify-between"><div><h2 className="font-semibold text-slate-900">롱폼 작업 목록</h2><p className="text-xs text-slate-500 mt-1">작업을 열어 스크립트 검토, TTS, 이미지 수정, 재조립 또는 쇼츠 전환을 이어서 진행하세요.</p></div><div className="flex flex-wrap gap-2">
            {attentionCount > 0 && (
              <button
                onClick={() => resetPage(setAttentionOnly)(!attentionOnly)}
                className={`flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold border transition ${attentionOnly ? 'bg-accent-amber text-navy-950 border-accent-amber' : 'bg-accent-amber/10 text-accent-amber border-accent-amber/30 hover:bg-accent-amber/20'}`}
              >
                <AlertTriangle size={14}/>주의 필요 {attentionCount}
              </button>
            )}
            <label className="relative"><Search size={15} className="absolute left-3 top-2.5 text-slate-500"/><input value={search} onChange={e => resetPage(setSearch)(e.target.value)} placeholder="제목·키워드 검색" className="bg-slate-100 border border-slate-300 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-900"/></label><select value={category} onChange={e => resetPage(setCategory)(e.target.value)} className="bg-slate-100 border border-slate-300 rounded-xl px-3 py-2 text-xs text-slate-900"><option value="ALL">주제 전체</option>{Object.entries(CATEGORY_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={mode} onChange={e => resetPage(setMode)(e.target.value)} className="bg-slate-100 border border-slate-300 rounded-xl px-3 py-2 text-xs text-slate-900"><option value="ALL">모드 전체</option><option value="AUTO">자동</option><option value="GUIDED">반자동</option></select></div></div>
          {isLoadError && <div className="px-5 py-4 text-sm text-accent-red">롱폼 목록을 불러오지 못했습니다. 백엔드 연결을 확인해 주세요.</div>}
          <div className="divide-y divide-slate-200">{isLoading && <div className="px-5 py-12 text-center text-slate-500">목록을 불러오는 중입니다.</div>}{!isLoading && jobs.length === 0 && <div className="px-5 py-12 text-center text-slate-500">조건에 맞는 롱폼 작업이 없습니다.</div>}{pageItems.map(job => <button key={job.id} onClick={() => navigate(`/longform/${job.id}`)} className="w-full px-5 py-4 flex items-center justify-between gap-4 hover:bg-slate-50 text-left transition"><div className="min-w-0"><div className="font-semibold text-slate-900 truncate">{job.title}</div><div className="text-xs text-slate-500 mt-1 truncate">{job.keyword || '키워드 미선택'} · {formatCategory(job.category)} · {job.longformTargetMinutes || 0}분 · {formatAutonomy(job.autonomy)}</div></div><div className="flex items-center gap-3 shrink-0"><span className="text-xs text-slate-500">₩{Math.round(Number(job.costAccumulated) || 0).toLocaleString('ko-KR')}</span><StatusBadge status={job.status} small/><ChevronRight size={16} className="text-slate-500"/></div></button>)}</div>
          <Pagination total={jobs.length} currentPage={page} onChange={setPage}/>
        </section>
      </div>
    </Layout>
  )
}

function ActionCard({ icon, title, text, action, onClick }) {
  return <button onClick={onClick} className="text-left rounded-xl border border-slate-200 bg-white hover:border-accent-cyan/60 p-5 transition"><div className="text-accent-cyan">{icon}</div><h2 className="font-semibold text-slate-900 mt-4">{title}</h2><p className="text-sm text-slate-500 leading-6 mt-2">{text}</p><span className="inline-block mt-4 text-sm font-semibold text-accent-cyan">{action} →</span></button>
}
