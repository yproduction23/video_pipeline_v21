import { useState, useEffect } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ChevronLeft, ChevronRight, Sparkles, Target, Sliders, DollarSign,
  Check, Zap, Users, Loader, Search, AlertTriangle, Tv
} from 'lucide-react'
import Layout from '../components/Layout'
import Pagination from '../components/Pagination'
import { jobsApi } from '../api/jobs'
import apiClient from '../api/client'

const CATEGORY_OPTIONS = [
  { value: 'KOSPI', label: '코스피 (KOSPI)', desc: '한국 종합주가지수 및 대형주 중심', icon: '📈' },
  { value: 'KOSDAQ', label: '코스닥 (KOSDAQ)', desc: '코스닥 종목·테마주·중소형주', icon: '🚀' },
  { value: 'US_STOCKS', label: '미국 주식', desc: 'S&P 500, 나스닥, 다우 지수', icon: '🇺🇸' },
  { value: 'INDIVIDUAL_STOCK', label: '개별 종목', desc: '삼성전자, SK하이닉스, 테슬라 등', icon: '🏢' },
  { value: 'GLOBAL_MACRO', label: '글로벌 매크로', desc: 'FOMC, 환율, 국채, CPI', icon: '🌐' },
  { value: 'CRYPTO', label: '암호화폐', desc: '비트코인, 이더리움, 알트코인', icon: '🪙' },
  { value: 'CUSTOM', label: '직접 입력', desc: '위 카테고리에 안 맞는 주제', icon: '✍️' },
]

const MACRO_SIGNAL_TERMS = [
  '연준', 'FOMC', 'fomc', '금리인하', '금리인상', '기준금리',
  '환율', '달러인덱스', 'DXY', '국채', '국채금리', '10년물',
  'CPI', 'PCE', '물가지수', '소비자물가', '고용지표', '실업률',
  '비농업고용', 'PMI', 'GDP', '양적긴축', '양적완화', '테이퍼링',
  '파월', '옐런', '잭슨홀',
]

const AUTONOMY_OPTIONS = [
  {
    value: 'AUTO', label: '풀 오토매틱 (AUTO)', icon: Zap,
    tag: '가장 빠름', tagColor: 'bg-emerald-100 text-emerald-800 border border-emerald-300',
    desc: '키워드 탐색부터 대본, AI 음성, 이미지, 영상 조립까지 모든 단계를 원스톱으로 자동 생성합니다.',
    warning: '중간 검토 없이 자동 진행되므로 완성 후 최종 결과물에서 수정을 진행합니다.',
  },
  {
    value: 'GUIDED', label: '스마트 파이프라인 (GUIDED)', icon: Users,
    tag: '추천 모드', tagColor: 'bg-cyan-100 text-cyan-800 border border-cyan-300',
    desc: '대본, 성우 목소리, 이미지 씬 구성을 단계별로 직접 검토하고 승인하며 완성도를 극대화합니다.',
    warning: null,
  },
]

const DURATION_OPTIONS = [
  { value: 1, label: '1분 테스트', hint: '정책 상한 ₩40,000. 빠른 배포 검증용' },
  { value: 5, label: '5분 리포트', hint: '정책 상한 ₩40,000. 핵심 인트로 + 브리핑' },
  { value: 10, label: '10분 비디오', hint: '정책 상한 ₩40,000. 표준 유튜브 롱폼' },
  { value: 15, label: '15분 심층', hint: '정책 상한 ₩40,000. 트렌드 분석 리포트' },
  { value: 20, label: '20분 풀버전', hint: '정책 상한 ₩80,000. 다큐멘터리급 풀리포트' },
  { value: 30, label: '30분 마스터', hint: '정책 상한 ₩80,000. 마스터클래스 심층 분석' },
]

const RESEARCH_PAGE_SIZE = 8

export default function JobNew() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const [step, setStep] = useState(1)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState(null)
  const [channels, setChannels] = useState([])
  const [researchKeyword, setResearchKeyword] = useState('')
  const [researchVideos, setResearchVideos] = useState([])
  const [researchPage, setResearchPage] = useState(1)
  const [researchLoading, setResearchLoading] = useState(false)
  const [researchError, setResearchError] = useState(null)
  const [showResearch, setShowResearch] = useState(false)

  useEffect(() => {
    apiClient.get('/channels')
      .then(r => {
        setChannels(r.data)
        if (r.data.length > 0) {
          setForm(current => current.channelId ? current : { ...current, channelId: r.data[0].channelId })
        }
      })
      .catch(e => console.error('채널 조회 실패:', e))
  }, [])

  const [form, setForm] = useState({
    title: '',
    channelId: '',
    category: 'KOSPI',
    autonomy: 'GUIDED',
    longformTargetMinutes: 15,
    budgetCap: null,
    geminiImageBudgetCap: 15000,
    makeShorts: true,
    shortsCount: 3,
    dataVisualsEnabled: false,
  })

  const selectedChannel = channels.find(c => c.channelId === form.channelId) || null

  const macroSkipCategories = ['GLOBAL_MACRO', 'CUSTOM', 'CRYPTO']
  const detectedMacroTerms = macroSkipCategories.includes(form.category)
    ? []
    : MACRO_SIGNAL_TERMS.filter(term => form.title.toLowerCase().includes(term.toLowerCase()))
  const showMacroCategoryBanner = detectedMacroTerms.length > 0

  useEffect(() => {
    const topic = searchParams.get('topic')
    const planId = searchParams.get('planId')
    const keywordList = searchParams.get('keywords')?.split('|').map(item => item.trim()).filter(Boolean).slice(0, 5) || location.state?.keywordPlan?.usedKeywords?.slice(0, 5) || []
    if (topic) {
      setForm(current => current.title ? current : {
        ...current,
        title: topic,
        keyword: keywordList.length ? keywordList.join(', ') : topic,
        keywordPlanId: planId || current.keywordPlanId,
        policyJson: JSON.stringify({ source: 'daily_keyword_research', planId, selectedKeywords: keywordList }),
      })
      setResearchKeyword(topic)
    }
  }, [searchParams, location.state])

  const canProceed = () => {
    if (step === 1) return form.title.trim().length > 0 && !!form.channelId
    if (step === 2) return true
    if (step === 3) return true
    return false
  }

  const searchTopicResearch = async (query = researchKeyword.trim()) => {
    const keyword = query.trim()
    if (query === undefined || (keyword.length === 0 && query !== '')) return
    setResearchLoading(true)
    setResearchError(null)
    try {
      const result = await jobsApi.trendingYoutube(keyword, { channelId: form.channelId })
      setResearchVideos(Array.isArray(result) ? result : (result?.videos || []))
      setResearchPage(1)
    } catch (err) {
      setResearchError(err?.response?.data?.message || '주제 검색에 실패했습니다.')
      setResearchVideos([])
      setResearchPage(1)
    } finally {
      setResearchLoading(false)
    }
  }

  const researchTotalPages = Math.max(1, Math.ceil(researchVideos.length / RESEARCH_PAGE_SIZE))
  const researchPageItems = researchVideos.slice((researchPage - 1) * RESEARCH_PAGE_SIZE, researchPage * RESEARCH_PAGE_SIZE)
  useEffect(() => { if (researchPage > researchTotalPages) setResearchPage(researchTotalPages) }, [researchPage, researchTotalPages])

  const handleSubmit = async () => {
    setCreating(true)
    setError(null)
    try {
      const job = await jobsApi.create(form)
      jobsApi.searchKeyword(job.id, form.keyword || form.title, 5).catch(err => {
        console.warn('키워드 자동 탐색 백그라운드 호출:', err)
      })
      navigate(`/longform/${job.id}`)
    } catch (err) {
      setError(err?.response?.data?.message || err.message || '작업 생성 실패')
      setCreating(false)
    }
  }

  return (
    <Layout>
      <div className="w-full max-w-none space-y-6">
        {/* 상단 타이틀 헤더 */}
        <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-200">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/longform')}
              className="p-2 rounded-xl bg-white border border-slate-300 text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-all shadow-sm"
              title="목록으로 돌아가기"
            >
              <ChevronLeft size={20} />
            </button>
            <div>
              <h1 className="text-2xl font-bold text-slate-900 tracking-tight">
                새 영상 콘텐츠 생성
              </h1>
              <p className="text-xs font-medium text-slate-600 mt-0.5">
                주제 선정부터 AI 스크립트 작성, 성우 음성 및 영상 조립까지 단계별로 설정합니다.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-cyan-50 border border-cyan-200 text-cyan-800 text-xs font-bold shadow-sm">
            <Sparkles size={14} className="text-cyan-600" /> AI 파이프라인 4.0
          </div>
        </div>

        {/* 선명한 3단계 진행 표시기 (High-Contrast Stepper) */}
        <div className="mb-8 p-1.5 rounded-2xl bg-white border border-slate-200 shadow-sm">
          <div className="grid grid-cols-3 gap-2">
            {[
              { n: 1, label: '주제 & 카테고리', icon: Target },
              { n: 2, label: '자율성 & 길이', icon: Sliders },
              { n: 3, label: '예산 & 최종 확인', icon: DollarSign },
            ].map((s) => {
              const Icon = s.icon
              const isCurrent = step === s.n
              const isPast = step > s.n
              return (
                <button
                  key={s.n}
                  onClick={() => isPast && setStep(s.n)}
                  disabled={!isPast && !isCurrent}
                  className={`flex items-center gap-3 p-3.5 rounded-xl transition-all text-left ${
                    isCurrent
                      ? 'bg-cyan-600 text-white font-bold shadow-md shadow-cyan-600/30'
                      : isPast
                      ? 'bg-slate-100 text-slate-800 hover:bg-slate-200 cursor-pointer font-semibold'
                      : 'bg-transparent text-slate-400 cursor-not-allowed font-medium'
                  }`}
                >
                  <div className={`w-8 h-8 rounded-xl flex items-center justify-center text-xs font-bold ${
                    isCurrent
                      ? 'bg-white text-cyan-700'
                      : isPast
                      ? 'bg-emerald-600 text-white'
                      : 'bg-slate-200 text-slate-500'
                  }`}>
                    {isPast ? <Check size={16} strokeWidth={3} /> : <Icon size={16} />}
                  </div>
                  <div>
                    <span className={`block text-[10px] font-bold tracking-wider uppercase ${isCurrent ? 'text-cyan-100' : 'text-slate-400'}`}>
                      STEP 0{s.n}
                    </span>
                    <span className="text-xs font-bold">{s.label}</span>
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* 카드 본문 컨테이너 (Clean High-Contrast White Card) */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-xl p-8 mb-8">

          {/* STEP 1: 주제 및 카테고리 */}
          {step === 1 && (
            <div className="space-y-7">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-bold text-slate-900">
                    제작 채널 선택 <span className="text-cyan-600">*</span>
                  </label>
                  <span className="text-xs font-semibold text-slate-500">이 작업은 선택한 채널에 고정됩니다</span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  {channels.map(ch => {
                    const isSelected = form.channelId === ch.channelId
                    return (
                      <button
                        key={ch.channelId}
                        type="button"
                        onClick={() => setForm({ ...form, channelId: ch.channelId })}
                        className={`relative text-left p-4 rounded-xl border-2 transition-all ${
                          isSelected
                            ? 'border-cyan-600 bg-cyan-50/80 shadow-md'
                            : 'border-slate-200 bg-slate-50/60 hover:border-slate-300 hover:bg-white'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2.5">
                            <Tv size={16} className={isSelected ? 'text-cyan-700' : 'text-slate-500'} />
                            <span className={`text-xs font-bold ${isSelected ? 'text-cyan-900' : 'text-slate-900'}`}>
                              {ch.channelName}
                            </span>
                          </div>
                          {isSelected && (
                            <span className="w-5 h-5 rounded-full bg-cyan-600 text-white flex items-center justify-center shadow-xs">
                              <Check size={12} strokeWidth={3} />
                            </span>
                          )}
                        </div>
                        {ch.genrePrimary && (
                          <p className={`text-[11px] font-semibold mt-1.5 ${isSelected ? 'text-cyan-800' : 'text-slate-500'}`}>
                            분야: {ch.genrePrimary}
                          </p>
                        )}
                      </button>
                    )
                  })}
                  {channels.length === 0 && (
                    <p className="text-xs font-semibold text-slate-500 col-span-2">
                      등록된 채널이 없습니다. 관리자 화면에서 채널 프로필을 먼저 생성해 주세요.
                    </p>
                  )}
                </div>
                {selectedChannel?.genreExcluded && (
                  <p className="text-xs font-semibold text-amber-700 mt-2">
                    제외 분야: {selectedChannel.genreExcluded} (검색 결과에서 자동 제외됩니다)
                  </p>
                )}
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-bold text-slate-900">
                    영상 주제 입력 <span className="text-cyan-600">*</span>
                  </label>
                  <span className="text-xs font-semibold text-slate-500">구체적 명사·소재 작성 권장</span>
                </div>
                <input
                  autoFocus
                  value={form.title}
                  onChange={e => setForm({ ...form, title: e.target.value })}
                  placeholder="예: 미 연준 금리인하 발표와 증시 영향, 삼성전자 3분기 반도체 실적 분석"
                  className="w-full bg-slate-50 border-2 border-slate-300 rounded-xl px-4 py-3.5 text-slate-900 font-bold text-sm placeholder-slate-400 focus:outline-none focus:bg-white focus:border-cyan-600 focus:ring-4 focus:ring-cyan-500/10 transition-all shadow-sm"
                />
                <p className="text-xs font-semibold text-slate-500 mt-2">
                  💡 팁: 단순 "주식"보다는 "삼성전자 반도체 실적 전망"처럼 대상을 좁히면 스크립트 품질이 훨씬 올라갑니다.
                </p>
              </div>

              {/* 매크로 카테고리 전환 권장 배너 */}
              {showMacroCategoryBanner && (
                <div className="p-4 rounded-xl border border-amber-300 bg-amber-50 shadow-sm animate-fadeIn">
                  <div className="flex items-start gap-3">
                    <div className="p-2 rounded-xl bg-amber-500 text-white mt-0.5 shadow-sm">
                      <AlertTriangle size={18} />
                    </div>
                    <div className="flex-1">
                      <h4 className="text-xs font-bold text-amber-900 uppercase tracking-wider">글로벌 매크로 카테고리 전환 추천</h4>
                      <p className="text-xs font-semibold text-amber-800 mt-1 leading-relaxed">
                        입력한 주제에 글로벌 매크로 키워드(<span className="font-bold underline decoration-amber-500">{detectedMacroTerms.slice(0, 3).join(', ')}</span>)가 포함되어 있습니다. 현재 선택된 <span className="font-bold">{form.category}</span> 카테고리는 미국 지표(연준 금리, CPI 등)를 수집하지 않으므로, 정확한 팩트체크를 위해 전환을 추천합니다.
                      </p>
                      <button
                        type="button"
                        onClick={() => setForm({ ...form, category: 'GLOBAL_MACRO' })}
                        className="mt-2.5 inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold transition shadow-sm"
                      >
                        <Sparkles size={14} /> 글로벌 매크로 카테고리로 즉시 전환
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* 주제 탐색 (선택형 아코디언) */}
              <div className="rounded-xl border border-slate-200 bg-slate-50 overflow-hidden">
                <button
                  type="button"
                  onClick={() => setShowResearch(!showResearch)}
                  className="w-full flex items-center justify-between p-4 text-left hover:bg-slate-100 transition"
                >
                  <div className="flex items-center gap-2.5">
                    <Search size={16} className="text-cyan-700" />
                    <span className="text-xs font-bold text-slate-800">유튜브 트렌드 검색 (선택한 채널 성격에 맞는 인기 영상만 추천, 선택사항)</span>
                  </div>
                  <span className="text-xs font-bold text-cyan-700">
                    {showResearch ? '접기 ▲' : '열기 ▼'}
                  </span>
                </button>

                {showResearch && (
                  <div className="p-4 border-t border-slate-200 bg-white space-y-4">
                    <div className="flex gap-2">
                      <input
                        value={researchKeyword}
                        onChange={e => setResearchKeyword(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') searchTopicResearch() }}
                        placeholder="예: 반도체 수출, 금리 인하, 삼성전자"
                        className="flex-1 bg-slate-50 border border-slate-300 rounded-xl px-3.5 py-2 text-xs font-semibold text-slate-900 focus:outline-none focus:border-cyan-600"
                      />
                      <button type="button" onClick={() => searchTopicResearch()} disabled={!researchKeyword.trim() || researchLoading} className="flex items-center gap-1 rounded-xl bg-cyan-700 hover:bg-cyan-800 px-4 py-2 text-xs font-bold text-white transition disabled:opacity-50 shadow-sm">
                        {researchLoading ? <Loader size={13} className="animate-spin" /> : <Search size={13} />} 검색
                      </button>
                    </div>
                    {researchError && <p className="text-xs font-semibold text-rose-600">{researchError}</p>}
                    {researchVideos.length > 0 && (
                      <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                        {researchPageItems.map((video, index) => {
                          const views = Number(video.views || video.viewCount || 0)
                          const subscribers = Number(video.subscribers || video.subscriberCount || 0)
                          const ratio = subscribers > 0 ? (views / subscribers).toFixed(2) : '-'
                          const title = video.title || '제목 없음'
                          const videoId = video.video_id || video.videoId || ''
                          return (
                            <div key={videoId || index} className="rounded-xl border border-slate-200 bg-slate-50 p-3 flex items-center justify-between gap-3 hover:bg-white transition shadow-2xs">
                              <div className="min-w-0 flex-1">
                                <div className="text-xs font-bold text-slate-900 truncate">{title}</div>
                                <div className="text-[11px] font-semibold text-slate-500 mt-1 flex gap-3">
                                  <span>조회 {views.toLocaleString()}</span>
                                  <span>구독자 대비 {ratio}x</span>
                                </div>
                              </div>
                              <button type="button" onClick={() => setForm({ ...form, title })} className="shrink-0 px-3 py-1.5 rounded-xl bg-cyan-50 border border-cyan-300 text-xs font-bold text-cyan-800 hover:bg-cyan-100">
                                주제로 가져오기
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* 카테고리 선택 그리드 */}
              <div>
                <label className="block text-sm font-bold text-slate-900 mb-3">
                  분석 카테고리 선택 <span className="text-cyan-600">*</span>
                </label>
                <div className="grid grid-cols-2 gap-3">
                  {CATEGORY_OPTIONS.map(opt => {
                    const isSelected = form.category === opt.value
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setForm({ ...form, category: opt.value })}
                        className={`relative text-left p-4 rounded-xl border-2 transition-all ${
                          isSelected
                            ? 'border-cyan-600 bg-cyan-50/80 shadow-md'
                            : 'border-slate-200 bg-slate-50/60 hover:border-slate-300 hover:bg-white'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2.5">
                            <span className="text-lg">{opt.icon}</span>
                            <span className={`text-xs font-bold ${isSelected ? 'text-cyan-900' : 'text-slate-900'}`}>
                              {opt.label}
                            </span>
                          </div>
                          {isSelected && (
                            <span className="w-5 h-5 rounded-full bg-cyan-600 text-white flex items-center justify-center shadow-xs">
                              <Check size={12} strokeWidth={3} />
                            </span>
                          )}
                        </div>
                        <p className={`text-[11px] font-semibold mt-1.5 leading-relaxed ${isSelected ? 'text-cyan-800' : 'text-slate-500'}`}>
                          {opt.desc}
                        </p>
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          )}

          {/* STEP 2: 자율성 및 목표 길이 */}
          {step === 2 && (
            <div className="space-y-7">
              <div>
                <label className="block text-sm font-bold text-slate-900 mb-3">파이프라인 자율성 모드 선택</label>
                <div className="grid grid-cols-2 gap-4">
                  {AUTONOMY_OPTIONS.map(opt => {
                    const Icon = opt.icon
                    const selected = form.autonomy === opt.value
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setForm({ ...form, autonomy: opt.value })}
                        className={`text-left p-5 rounded-xl border-2 transition-all flex flex-col justify-between ${
                          selected
                            ? 'border-cyan-600 bg-cyan-50/80 shadow-md'
                            : 'border-slate-200 bg-slate-50/60 hover:border-slate-300 hover:bg-white'
                        }`}
                      >
                        <div>
                          <div className="flex items-center justify-between mb-3">
                            <div className={`p-2.5 rounded-xl shadow-xs ${selected ? 'bg-cyan-600 text-white' : 'bg-slate-200 text-slate-700'}`}>
                              <Icon size={20} />
                            </div>
                            <span className={`text-[10px] px-2.5 py-1 rounded-lg font-bold ${opt.tagColor}`}>
                              {opt.tag}
                            </span>
                          </div>
                          <h3 className={`text-sm font-bold ${selected ? 'text-cyan-900' : 'text-slate-900'}`}>{opt.label}</h3>
                          <p className="text-xs font-semibold text-slate-600 mt-2 leading-relaxed">{opt.desc}</p>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>

              <div>
                <label className="block text-sm font-bold text-slate-900 mb-3">목표 비디오 길이 설정</label>
                <div className="grid grid-cols-3 gap-3">
                  {DURATION_OPTIONS.map(opt => {
                    const isSelected = form.longformTargetMinutes === opt.value
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setForm({ ...form, longformTargetMinutes: opt.value })}
                        className={`p-4 rounded-xl border-2 text-center transition-all ${
                          isSelected
                            ? 'border-cyan-600 bg-cyan-50/80 text-cyan-950 font-bold shadow-md'
                            : 'border-slate-200 bg-slate-50/60 text-slate-800 hover:border-slate-300 hover:bg-white font-bold'
                        }`}
                      >
                        <div className="text-sm font-bold">{opt.label}</div>
                        <div className="text-[11px] font-semibold text-slate-500 mt-1">{opt.hint.split('.')[0]}</div>
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          )}

          {/* STEP 3: 예산 및 최종 확인 */}
          {step === 3 && (
            <div className="space-y-6">
              <div className="p-6 rounded-xl border-2 border-cyan-300 bg-cyan-50/90 shadow-sm">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold text-cyan-900 uppercase tracking-wider">영상 제작 예산 상한 설정</span>
                  <span className="text-[11px] font-bold text-cyan-700">기본 정책: {form.longformTargetMinutes >= 20 ? '₩80,000' : '₩40,000'}</span>
                </div>
                
                <div className="mt-2 flex items-center gap-3">
                  <span className="text-2xl font-bold text-cyan-900">₩</span>
                  <input
                    type="number"
                    step="5000"
                    min="10000"
                    value={form.budgetCap || (form.longformTargetMinutes >= 20 ? 80000 : 40000)}
                    onChange={e => setForm({ ...form, budgetCap: Number(e.target.value) })}
                    className="flex-1 bg-white border-2 border-cyan-400 rounded-xl px-4 py-2.5 text-2xl font-bold text-cyan-950 focus:outline-none focus:ring-4 focus:ring-cyan-500/20 shadow-inner"
                  />
                </div>
                <p className="text-xs font-semibold text-slate-600 mt-2 leading-relaxed">
                  필요 시 예산 상한을 자유롭게 직접 수정할 수 있습니다 (기본 추천 정책: {form.longformTargetMinutes >= 20 ? '20분 이상 ₩80,000' : '20분 미만 ₩40,000'}).
                </p>
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 space-y-3 shadow-inner">
                <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-3">최종 구성 요약</h3>
                <Row label="영상 대표 주제" value={form.title} highlight />
                <Row label="선택 카테고리" value={CATEGORY_OPTIONS.find(o => o.value === form.category)?.label} />
                <Row label="자율성 모드" value={AUTONOMY_OPTIONS.find(o => o.value === form.autonomy)?.label} />
                <Row label="목표 영상 길이" value={`${form.longformTargetMinutes}분`} />
                <Row label="설정된 예산 상한" value={`₩${(form.budgetCap || (form.longformTargetMinutes >= 20 ? 80000 : 40000)).toLocaleString()}`} highlight />
              </div>

              {error && (
                <div className="p-4 rounded-xl border border-rose-300 bg-rose-50 text-xs font-bold text-rose-800">
                  {error}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 하단 네비게이션 버튼 (High-Contrast Buttons) */}
        <div className="flex items-center justify-between">
          {step > 1 ? (
            <button
              onClick={() => setStep(step - 1)}
              disabled={creating}
              className="flex items-center gap-2 px-6 py-3 rounded-xl border-2 border-slate-300 bg-white hover:bg-slate-100 text-slate-800 font-bold text-sm transition shadow-sm disabled:opacity-50"
            >
              <ChevronLeft size={18} strokeWidth={2.5} /> 이전 단계
            </button>
          ) : <div />}

          {step < 3 ? (
            <button
              onClick={() => setStep(step + 1)}
              disabled={!canProceed()}
              className="flex items-center gap-2 px-7 py-3 rounded-xl bg-cyan-600 hover:bg-cyan-700 text-white font-bold text-sm shadow-md shadow-cyan-600/30 transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              다음 단계 <ChevronRight size={18} strokeWidth={2.5} />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!canProceed() || creating}
              className="flex items-center gap-2 px-8 py-3 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-sm shadow-md shadow-emerald-600/30 transition disabled:opacity-50"
            >
              {creating ? <Loader size={18} className="animate-spin" /> : <Sparkles size={18} />}
              {creating ? '영상 생성 진행 중...' : '영상 시작'}
            </button>
          )}
        </div>
      </div>
    </Layout>
  )
}

function Row({ label, value, highlight }) {
  return (
    <div className="flex items-center justify-between text-xs py-1.5 border-b border-slate-200 last:border-0">
      <span className="font-semibold text-slate-500">{label}</span>
      <span className={`font-bold ${highlight ? 'text-cyan-700 text-sm' : 'text-slate-900'}`}>{value}</span>
    </div>
  )
}
