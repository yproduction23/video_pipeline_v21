import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Shield, DollarSign, Video, Settings, Save, RefreshCw, Check, Sparkles,
  Users, ImagePlus, UserPlus, UploadCloud, AlertCircle, PlayCircle, ExternalLink,
  Volume2, Trash2, CheckCircle2, XCircle
} from 'lucide-react'
import Layout from '../components/Layout'
import JobFilterBar from '../components/JobFilterBar'
import Pagination from '../components/Pagination'
import StatusBadge from '../components/StatusBadge'
import ReferenceChannelManager from '../components/admin/ReferenceChannelManager'
import apiClient from '../api/client'
import { formatAutonomy, formatCategory, isCompleted } from '../constants/jobStatus'

const PERSON_LICENSES = [
  ['OWNED_EXPLICIT', '직접 소지 (자사 수집·계약)'],
  ['COMMERCIAL_FREE', '상업용 무료 (Unsplash, Pixabay 등)'],
  ['EDITORIAL_RIGHTS_CLEARED', '에디토리얼/보도 권리 확보'],
  ['PUBLIC_DOMAIN', '퍼블릭 도메인'],
]

/** 채널 성격 필터의 기준이 되는 주요 분야 프리셋. STEP 01 트렌드 추천 필터가 이 값을 사용한다. */
const GENRE_OPTIONS = [
  '경제/역사/시사',
  '노인/생활정보',
  '건강/제품판매',
  '기타',
]

export default function Admin() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState('jobs') // 'jobs' | 'policy' | 'channels' | 'people' | 'reference-channels'
  const [adminFilter, setAdminFilter] = useState('ALL')
  const [currentPage, setCurrentPage] = useState(1)

  const [searchQuery, setSearchQuery] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('ALL')
  const [selectedMode, setSelectedMode] = useState('ALL')
  const [selectedStatus, setSelectedStatus] = useState('ALL')

  // 동적 예산 정책 상태
  const [policyForm, setPolicyForm] = useState({
    shortformBudgetCap: 40000,
    longformBudgetCap: 80000,
  })
  const [policySaving, setPolicySaving] = useState(false)
  const [policySuccess, setPolicySuccess] = useState(false)

  // 채널 프로필 편집 상태
  const [editedVoices, setEditedVoices] = useState({})
  const [characterDescriptions, setCharacterDescriptions] = useState({})
  const [editedGenrePrimary, setEditedGenrePrimary] = useState({})
  const [editedGenreExcluded, setEditedGenreExcluded] = useState({})
  const [channelPreviewText, setChannelPreviewText] = useState({})
  const [channelPreviewUrls, setChannelPreviewUrls] = useState({})
  const [channelPreviewLoading, setChannelPreviewLoading] = useState({})
  const [newChannel, setNewChannel] = useState({
    channelId: '', channelName: '', characterKey: '', characterStylePrompt: '', referenceStyleProfile: 'black_han_sans_v1', voiceId: ''
  })

  // 실사 인물 에셋 상태
  const [personForm, setPersonForm] = useState({ personId: '', nameKo: '', nameEn: '', aliasesJson: '' })
  const [selectedPersonId, setSelectedPersonId] = useState('')
  const [photoForm, setPhotoForm] = useState({
    file: null, licenseType: 'OWNED_EXPLICIT', licenseRef: '', creditText: '', authorName: '', emotionTag: 'neutral', pose: 'portrait'
  })

  // 1. 데이터 Queries
  const { data: jobs = [] } = useQuery({
    queryKey: ['admin-jobs'],
    queryFn: () => apiClient.get('/jobs').then(r => r.data),
  })

  const { data: channels = [], refetch: refetchChannels } = useQuery({
    queryKey: ['admin-channels'],
    queryFn: () => apiClient.get('/channels').then(r => r.data),
  })

  const { data: voices = [] } = useQuery({
    queryKey: ['voices'],
    queryFn: () => apiClient.get('/channels/voices').then(r => r.data),
    staleTime: Infinity,
  })

  const { data: integrations = {} } = useQuery({
    queryKey: ['integration-status'],
    queryFn: () => apiClient.get('/integrations/status').then(r => r.data),
    retry: false,
  })

  const { data: pricingPolicy } = useQuery({
    queryKey: ['admin-pricing-policy'],
    queryFn: () => apiClient.get('/admin/pricing-policy').then(r => r.data),
  })

  const { data: people = [] } = useQuery({
    queryKey: ['admin-people'],
    queryFn: () => apiClient.get('/assets/person').then(r => r.data).catch(() => []),
  })

  const { data: photos = [], refetch: refetchPhotos } = useQuery({
    queryKey: ['admin-person-photos', selectedPersonId],
    queryFn: () => apiClient.get(`/assets/person/${selectedPersonId}/photos`).then(r => r.data).catch(() => []),
    enabled: !!selectedPersonId,
  })

  const { data: characterLibraries = {} } = useQuery({
    queryKey: ['character-libraries', channels.map(c => c.channelId)],
    queryFn: async () => {
      const entries = await Promise.all(channels.map(async channel => {
        try {
          const response = await apiClient.get(`/channels/${channel.channelId}/character-library`)
          return [channel.channelId, response.data]
        } catch (_) {
          return [channel.channelId, { exists: false, poses: [], poseCount: 0 }]
        }
      }))
      return Object.fromEntries(entries)
    },
    enabled: channels.length > 0,
  })

  useEffect(() => {
    if (pricingPolicy) {
      setPolicyForm({
        shortformBudgetCap: pricingPolicy.shortformBudgetCap || 40000,
        longformBudgetCap: pricingPolicy.longformBudgetCap || 80000,
      })
    }
  }, [pricingPolicy])

  // 2. Mutations
  const savePolicyMutation = useMutation({
    mutationFn: (newPolicy) => apiClient.post('/admin/pricing-policy', newPolicy).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries(['admin-pricing-policy'])
      setPolicySuccess(true)
      setTimeout(() => setPolicySuccess(false), 3000)
      alert('영상 길이별 예산 정책이 성공적으로 저장되었습니다!')
    },
    onError: (err) => {
      alert('예산 정책 저장 실패: ' + (err.response?.data?.message || err.message))
    }
  })

  const saveChannelMutation = useMutation({
    mutationFn: (profile) => apiClient.post('/channels', profile).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries(['admin-channels'])
      alert('채널 프로필 설정이 성공적으로 저장되었습니다!')
    },
    onError: (err) => alert('채널 저장 실패: ' + (err.response?.data?.message || err.message))
  })

  const createChannelMutation = useMutation({
    mutationFn: () => apiClient.post('/channels', newChannel).then(r => r.data),
    onSuccess: () => {
      setNewChannel({ channelId: '', channelName: '', characterKey: '', characterStylePrompt: '', referenceStyleProfile: 'black_han_sans_v1', voiceId: '' })
      qc.invalidateQueries({ queryKey: ['admin-channels'] })
      alert('신규 채널이 생성되었습니다.')
    },
    onError: (err) => alert('채널 생성 실패: ' + (err.response?.data?.message || err.message)),
  })

  const characterLibraryMutation = useMutation({
    mutationFn: ({ channelId, characterDescription, regenerate, includeRoleCostumes = false }) =>
      apiClient.post(`/channels/${channelId}/character-library`, { characterDescription, regenerate, includeRoleCostumes }).then(r => r.data),
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ['character-libraries'] })
      qc.invalidateQueries({ queryKey: ['admin-channels'] })
      refetchChannels()
      alert(variables.includeRoleCostumes ? '역할별 의상 15종을 성공적으로 생성했습니다.' : (variables.regenerate ? '캐릭터 포즈를 전체 재생성했습니다.' : '포즈 에셋을 생성했습니다.'))
    },
    onError: (err) => alert('캐릭터 포즈 생성 실패: ' + (err.response?.data?.message || err.message)),
  })

  const createPerson = useMutation({
    mutationFn: () => apiClient.post('/assets/person', {
      personId: personForm.personId,
      nameKo: personForm.nameKo,
      nameEn: personForm.nameEn,
      aliasesJson: personForm.aliasesJson || null,
    }).then(r => r.data),
    onSuccess: () => {
      setPersonForm({ personId: '', nameKo: '', nameEn: '', aliasesJson: '' })
      qc.invalidateQueries(['admin-people'])
      alert('인물이 성공적으로 등록되었습니다.')
    },
    onError: (err) => alert('인물 등록 실패: ' + (err.response?.data?.message || err.message))
  })

  const uploadPhoto = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', photoForm.file)
      fd.append('licenseType', photoForm.licenseType)
      if (photoForm.licenseRef) fd.append('licenseRef', photoForm.licenseRef)
      if (photoForm.creditText) fd.append('creditText', photoForm.creditText)
      if (photoForm.authorName) fd.append('authorName', photoForm.authorName)
      fd.append('emotionTag', photoForm.emotionTag)
      fd.append('pose', photoForm.pose)
      return apiClient.post(`/assets/person/${selectedPersonId}/photos`, fd).then(r => r.data)
    },
    onSuccess: () => {
      setPhotoForm({ file: null, licenseType: 'OWNED_EXPLICIT', licenseRef: '', creditText: '', authorName: '', emotionTag: 'neutral', pose: 'portrait' })
      refetchPhotos()
      alert('권리 검토 대기 사진이 등록되었습니다.')
    },
    onError: (err) => alert('사진 등록 실패: ' + (err.response?.data?.message || err.message))
  })

  const reviewPhoto = useMutation({
    mutationFn: ({ photoId, action }) => apiClient
      .post(`/assets/person/${selectedPersonId}/photos/${photoId}/${action}`)
      .then(r => r.data),
    onSuccess: () => {
      refetchPhotos()
      alert('사진 검토 결과가 적용되었습니다.')
    }
  })

  const previewChannelVoice = async (channelId, voiceId) => {
    const text = (channelPreviewText[channelId] || '오늘 시장의 핵심 숫자와 수급의 방향을 확인해보겠습니다.').trim()
    if (!voiceId || !text || text.length > 100) return
    setChannelPreviewLoading(prev => ({ ...prev, [channelId]: true }))
    try {
      const response = await apiClient.post('/channels/voices/preview', { voiceId, text }, { responseType: 'blob' })
      const url = URL.createObjectURL(response.data)
      setChannelPreviewUrls(prev => {
        if (prev[channelId]) URL.revokeObjectURL(prev[channelId])
        return { ...prev, [channelId]: url }
      })
    } catch (error) {
      alert('음성 미리듣기 생성에 실패했습니다.')
    } finally {
      setChannelPreviewLoading(prev => ({ ...prev, [channelId]: false }))
    }
  }

  const handlePolicySave = () => {
    setPolicySaving(true)
    savePolicyMutation.mutate(policyForm, {
      onSettled: () => setPolicySaving(false)
    })
  }

  const totalCost = jobs.reduce((sum, j) => sum + (parseFloat(j.costAccumulated) || 0), 0)
  const completedJobs = jobs.filter(j => isCompleted(j.status))

  const sortedJobs = [...jobs].sort((a, b) => b.id - a.id)

  const filteredJobs = sortedJobs.filter(job => {
    if (adminFilter === 'COMPLETED' && !isCompleted(job.status)) return false
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      const titleMatch = job.title?.toLowerCase().includes(q)
      const creatorMatch = job.createdBy?.toLowerCase().includes(q)
      if (!titleMatch && !creatorMatch) return false
    }
    if (selectedCategory !== 'ALL' && job.category !== selectedCategory) return false
    if (selectedMode !== 'ALL' && job.autonomy !== selectedMode) return false
    if (selectedStatus !== 'ALL' && job.status !== selectedStatus) return false
    return true
  })

  const pageItems = filteredJobs.slice((currentPage - 1) * 10, currentPage * 10)

  const licenseNeedsRef = photoForm.licenseType === 'COMMERCIAL_FREE' || photoForm.licenseType === 'EDITORIAL_RIGHTS_CLEARED'
  const licenseNeedsCredit = photoForm.licenseType === 'COMMERCIAL_FREE'

  return (
    <Layout>
      <div className="w-full max-w-none space-y-6">
        {/* 상단 타이틀 헤더 */}
        <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-200">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-amber-500 text-white shadow-md">
              <Shield size={22} />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900 tracking-tight">시스템 관리자 콘솔</h1>
              <p className="text-xs font-semibold text-slate-600 mt-0.5">통합 작업 모니터링, 동적 예산 정책, 채널 프로필 및 실사 에셋을 총괄 관리합니다.</p>
            </div>
          </div>
        </div>

        {/* 요약 통계 카드 3종 */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          <button
            onClick={() => { setActiveTab('jobs'); setAdminFilter('ALL') }}
            className={`text-left bg-white rounded-2xl p-5 border transition-all shadow-sm ${
              adminFilter === 'ALL' && activeTab === 'jobs' ? 'border-cyan-600 ring-2 ring-cyan-500/20' : 'border-slate-200 hover:border-slate-300'
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">전체 영상 작업</span>
              <Video className="text-cyan-600" size={20} />
            </div>
            <div className="text-2xl font-bold text-slate-900 mt-2">{jobs.length}건</div>
          </button>

          <button
            onClick={() => { setActiveTab('jobs'); setAdminFilter('COMPLETED') }}
            className={`text-left bg-white rounded-2xl p-5 border transition-all shadow-sm ${
              adminFilter === 'COMPLETED' && activeTab === 'jobs' ? 'border-emerald-600 ring-2 ring-emerald-500/20' : 'border-slate-200 hover:border-slate-300'
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">완료된 영상</span>
              <CheckCircle2 className="text-emerald-600" size={20} />
            </div>
            <div className="text-2xl font-bold text-emerald-800 mt-2">{completedJobs.length}건</div>
          </button>

          <div className="bg-white rounded-2xl p-5 border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">누적 총 소요 비용</span>
              <DollarSign className="text-amber-600" size={20} />
            </div>
            <div className="text-2xl font-bold text-slate-900 mt-2">
              ₩{totalCost.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}
            </div>
          </div>
        </div>

        {/* 외부 API 연결 상태 파트 */}
        <div className="mb-8 bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-xs font-bold text-slate-900 uppercase tracking-wider">외부 API 서비스 연동 상태</h2>
              <p className="text-[11px] font-semibold text-slate-500 mt-0.5">서버 환경 변수(`.env`)의 API 키 설정 상태를 실시간 점검합니다.</p>
            </div>
            <span className="text-[11px] font-bold text-slate-400">서버 헬스 상태 기준</span>
          </div>
          <div className="grid grid-cols-3 gap-3 text-xs font-bold">
            <ProviderBadge label="YouTube Data API v3" configured={integrations.youtube?.configured} />
            <ProviderBadge label="ElevenLabs TTS" configured={integrations.elevenlabs?.configured} />
            <ProviderBadge label="Anthropic Claude 4.6" configured={integrations.anthropic?.configured} />
          </div>
        </div>

        {/* 메인 탭 네비게이션 */}
        <div className="flex flex-wrap items-center gap-2 mb-6 border-b border-slate-200 pb-2">
          {[
            { id: 'jobs', label: '전체 작업 모니터링', icon: Video },
            { id: 'policy', label: '영상 길이별 예산 정책 설정', icon: Settings },
            { id: 'channels', label: '채널 프로필 관리', icon: Shield },
            { id: 'people', label: '실사 인물 에셋 관리', icon: Users },
            { id: 'reference-channels', label: '레퍼런스 채널', icon: ExternalLink },
          ].map(tab => {
            const Icon = tab.icon
            const isActive = activeTab === tab.id
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-bold text-xs transition-all ${
                  isActive
                    ? 'bg-cyan-600 text-white shadow-md shadow-cyan-600/20'
                    : 'bg-white border border-slate-200 text-slate-700 hover:bg-slate-100'
                }`}
              >
                <Icon size={16} />
                {tab.label}
              </button>
            )
          })}
        </div>

        {/* TAB 1: 전체 작업 모니터링 */}
        {activeTab === 'jobs' && (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-md p-6">
            <JobFilterBar
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              selectedCategory={selectedCategory}
              onCategoryChange={setSelectedCategory}
              selectedMode={selectedMode}
              onModeChange={setSelectedMode}
              selectedStatus={selectedStatus}
              onStatusChange={setSelectedStatus}
              onResetFilters={() => {
                setSearchQuery('')
                setSelectedCategory('ALL')
                setSelectedMode('ALL')
                setSelectedStatus('ALL')
                setCurrentPage(1)
              }}
            />

            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b-2 border-slate-200 bg-slate-50 text-slate-700 font-bold uppercase">
                    <th className="py-3 px-4">Job ID</th>
                    <th className="py-3 px-4">영상 대표 주제</th>
                    <th className="py-3 px-4">카테고리</th>
                    <th className="py-3 px-4">자율성</th>
                    <th className="py-3 px-4">목표 길이</th>
                    <th className="py-3 px-4">상태</th>
                    <th className="py-3 px-4">누적 비용</th>
                    <th className="py-3 px-4 text-right">상세</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 font-semibold text-slate-900">
                  {pageItems.length > 0 ? (
                    pageItems.map(job => (
                      <tr key={job.id} className="hover:bg-slate-50/80 transition">
                        <td className="py-3.5 px-4 font-bold text-cyan-800">#{job.id}</td>
                        <td className="py-3.5 px-4 font-bold text-slate-900 max-w-xs truncate">{job.title || '(무제)'}</td>
                        <td className="py-3.5 px-4 text-slate-700">{formatCategory(job.category)}</td>
                        <td className="py-3.5 px-4 text-slate-700">{formatAutonomy(job.autonomy)}</td>
                        <td className="py-3.5 px-4 text-slate-800">{job.longformTargetMinutes || 20}분</td>
                        <td className="py-3.5 px-4"><StatusBadge status={job.status} /></td>
                        <td className="py-3.5 px-4 font-bold text-slate-900">₩{(job.costAccumulated || 0).toLocaleString()}</td>
                        <td className="py-3.5 px-4 text-right">
                          <button
                            onClick={() => navigate(`/longform/${job.id}`)}
                            className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold border border-slate-300"
                          >
                            보기
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-slate-500 font-semibold">
                        검색 조건에 일치하는 작업이 없습니다.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="mt-4">
              <Pagination total={filteredJobs.length} currentPage={currentPage} onChange={setCurrentPage} pageSize={10} />
            </div>
          </div>
        )}

        {/* TAB 2: 영상 길이별 예산 정책 설정 */}
        {activeTab === 'policy' && (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-xl p-8 space-y-6">
            <div className="flex items-center gap-3 pb-4 border-b border-slate-200">
              <div className="p-2.5 rounded-xl bg-cyan-600 text-white">
                <Settings size={20} />
              </div>
              <div>
                <h2 className="text-lg font-bold text-slate-900">영상 길이별 기본 예산 상한 정책</h2>
                <p className="text-xs font-semibold text-slate-600 mt-0.5">관리자가 설정한 예산 정책 수치는 파이프라인 전체에 실시간 반영됩니다.</p>
              </div>
            </div>

            {policySuccess && (
              <div className="p-4 rounded-xl border border-emerald-300 bg-emerald-50 text-xs font-bold text-emerald-800 flex items-center gap-2">
                <Check size={16} /> 정책이 저장되어 파이프라인 시스템에 즉시 반영되었습니다.
              </div>
            )}

            <div className="space-y-6">
              <div className="p-5 rounded-xl border border-slate-200 bg-slate-50 space-y-2">
                <label className="block text-sm font-bold text-slate-900">
                  20분 미만 (표준 숏/중형 롱폼) 기본 예산 상한
                </label>
                <div className="flex items-center gap-3">
                  <span className="text-xl font-bold text-slate-800">₩</span>
                  <input
                    type="number"
                    step="5000"
                    min="5000"
                    value={policyForm.shortformBudgetCap}
                    onChange={e => setPolicyForm({ ...policyForm, shortformBudgetCap: Number(e.target.value) })}
                    className="w-full bg-white border-2 border-slate-300 rounded-xl px-4 py-2.5 text-lg font-bold text-slate-900 focus:outline-none focus:border-cyan-600"
                  />
                </div>
                <p className="text-xs font-semibold text-slate-500">1분 ~ 15분 영상 생성 시 적용되는 기본 정책 예산 수치입니다 (기본 추천: ₩40,000).</p>
              </div>

              <div className="p-5 rounded-xl border border-slate-200 bg-slate-50 space-y-2">
                <label className="block text-sm font-bold text-slate-900">
                  20분 이상 (마스터클래스 / 다큐멘터리) 기본 예산 상한
                </label>
                <div className="flex items-center gap-3">
                  <span className="text-xl font-bold text-slate-800">₩</span>
                  <input
                    type="number"
                    step="5000"
                    min="10000"
                    value={policyForm.longformBudgetCap}
                    onChange={e => setPolicyForm({ ...policyForm, longformBudgetCap: Number(e.target.value) })}
                    className="w-full bg-white border-2 border-slate-300 rounded-xl px-4 py-2.5 text-lg font-bold text-slate-900 focus:outline-none focus:border-cyan-600"
                  />
                </div>
                <p className="text-xs font-semibold text-slate-500">20분 이상 장문 영상 생성 시 적용되는 기본 정책 예산 수치입니다 (기본 추천: ₩80,000).</p>
              </div>

              <div className="pt-4 flex justify-end">
                <button
                  type="button"
                  onClick={handlePolicySave}
                  disabled={policySaving}
                  className="flex items-center gap-2 px-6 py-3 rounded-xl bg-cyan-600 hover:bg-cyan-700 text-white font-bold text-xs shadow-md transition disabled:opacity-50"
                >
                  {policySaving ? <RefreshCw className="animate-spin" size={16} /> : <Save size={16} />}
                  {policySaving ? '정책 저장 중...' : '예산 정책 저장 및 즉시 반영'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: 채널 프로필 관리 */}
        {activeTab === 'channels' && (
          <div className="space-y-6">
            {/* 신규 채널 추가 카드 */}
            <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
              <h2 className="text-sm font-bold text-slate-900 uppercase tracking-wider mb-3">신규 채널 프로필 생성</h2>
              <div className="grid grid-cols-3 gap-3 mb-3">
                <input value={newChannel.channelId} onChange={e => setNewChannel({ ...newChannel, channelId: e.target.value })} placeholder="채널 ID (예: ch_invest)" className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
                <input value={newChannel.channelName} onChange={e => setNewChannel({ ...newChannel, channelName: e.target.value })} placeholder="채널 이름" className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
                <input value={newChannel.voiceId} onChange={e => setNewChannel({ ...newChannel, voiceId: e.target.value })} placeholder="ElevenLabs Voice ID" className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
              </div>
              <button onClick={() => createChannelMutation.mutate()} disabled={!newChannel.channelId || !newChannel.channelName || createChannelMutation.isPending} className="px-4 py-2 rounded-xl bg-cyan-600 text-white font-bold text-xs shadow-sm hover:bg-cyan-700 disabled:opacity-50">
                {createChannelMutation.isPending ? '생성 중...' : '신규 채널 등록'}
              </button>
            </div>

            {/* 기존 채널 목록 */}
            <div className="space-y-4">
              {channels.map(channel => {
                const libInfo = characterLibraries[channel.channelId] || { exists: false, poses: [], poseCount: 0 }
                const isGeneratingLibrary = characterLibraryMutation.isPending
                const characterDescription = characterDescriptions[channel.channelId] || channel.characterStylePrompt || ''

                return (
                  <div key={channel.channelId} className="bg-white rounded-2xl border border-slate-200 p-6 shadow-md space-y-4">
                    <div className="flex items-center justify-between pb-3 border-b border-slate-200">
                      <div>
                        <h3 className="text-base font-bold text-slate-900">{channel.channelName}</h3>
                        <span className="text-xs font-semibold text-slate-500">ID: {channel.channelId}</span>
                      </div>
                      <span className="text-xs font-bold px-3 py-1 rounded-full bg-cyan-50 border border-cyan-300 text-cyan-900">
                        {channel.referenceStyleProfile}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs font-bold text-slate-700 mb-1">캐릭터 스타일 프롬프트</label>
                        <textarea
                          rows={2}
                          value={characterDescription}
                          onChange={e => setCharacterDescriptions({ ...characterDescriptions, [channel.channelId]: e.target.value })}
                          className="w-full bg-slate-50 border border-slate-300 rounded-xl p-2.5 text-xs font-semibold text-slate-900"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-slate-700 mb-1">성우 Voice ID</label>
                        <input
                          value={editedVoices[channel.channelId] ?? (channel.voiceId || '')}
                          onChange={e => setEditedVoices({ ...editedVoices, [channel.channelId]: e.target.value })}
                          className="w-full bg-slate-50 border border-slate-300 rounded-xl p-2.5 text-xs font-bold text-slate-900 mb-2"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-slate-700 mb-1">주요 분야 (트렌드 추천 필터 기준)</label>
                        <select
                          value={editedGenrePrimary[channel.channelId] ?? (channel.genrePrimary || '')}
                          onChange={e => setEditedGenrePrimary({ ...editedGenrePrimary, [channel.channelId]: e.target.value })}
                          className="w-full bg-slate-50 border border-slate-300 rounded-xl p-2.5 text-xs font-bold text-slate-900"
                        >
                          <option value="">선택 안 함</option>
                          {GENRE_OPTIONS.map(g => <option key={g} value={g}>{g}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-slate-700 mb-1">제외 분야 (쉼표로 구분, 검색 결과에서 자동 제외)</label>
                        <input
                          value={editedGenreExcluded[channel.channelId] ?? (channel.genreExcluded || '')}
                          onChange={e => setEditedGenreExcluded({ ...editedGenreExcluded, [channel.channelId]: e.target.value })}
                          placeholder="예: 건강식품, 다이어트"
                          className="w-full bg-slate-50 border border-slate-300 rounded-xl p-2.5 text-xs font-bold text-slate-900"
                        />
                      </div>
                    </div>
                    <button
                      onClick={() => saveChannelMutation.mutate({
                        ...channel,
                        characterStylePrompt: characterDescription,
                        voiceId: editedVoices[channel.channelId] ?? channel.voiceId,
                        genrePrimary: editedGenrePrimary[channel.channelId] ?? channel.genrePrimary,
                        genreExcluded: editedGenreExcluded[channel.channelId] ?? channel.genreExcluded,
                      })}
                      className="px-3 py-1.5 rounded-xl bg-emerald-600 text-white font-bold text-xs shadow-sm hover:bg-emerald-700"
                    >
                      설정 저장
                    </button>

                    {/* 캐릭터 포즈 15종 및 역할 의상 생성 */}
                    <div className="pt-2 flex items-center gap-3">
                      <button
                        onClick={() => characterLibraryMutation.mutate({ channelId: channel.channelId, characterDescription, regenerate: false, includeRoleCostumes: false })}
                        disabled={!characterDescription.trim() || isGeneratingLibrary}
                        className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-cyan-50 border border-cyan-300 text-cyan-900 font-bold text-xs hover:bg-cyan-100 disabled:opacity-50"
                      >
                        <Sparkles size={14} /> 기본 포즈 15종 생성
                      </button>
                      <button
                        onClick={() => characterLibraryMutation.mutate({ channelId: channel.channelId, characterDescription, regenerate: false, includeRoleCostumes: true })}
                        disabled={!characterDescription.trim() || isGeneratingLibrary}
                        className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-amber-50 border border-amber-300 text-amber-900 font-bold text-xs hover:bg-amber-100 disabled:opacity-50"
                      >
                        <ImagePlus size={14} /> 역할 의상 15종 생성
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* TAB 4: 실사 인물 에셋 관리 */}
        {activeTab === 'people' && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-6">
              {/* 1. 인물 신규 등록 */}
              <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-3">
                <h3 className="text-sm font-bold text-slate-900 uppercase tracking-wider">1. 실사 인물 등록</h3>
                <div className="grid grid-cols-2 gap-2">
                  <input value={personForm.personId} onChange={e => setPersonForm({ ...personForm, personId: e.target.value })} placeholder="ID: sundar_pichai" className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
                  <input value={personForm.nameKo} onChange={e => setPersonForm({ ...personForm, nameKo: e.target.value })} placeholder="한글 이름 (예: 순다르 피차이)" className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
                  <input value={personForm.nameEn} onChange={e => setPersonForm({ ...personForm, nameEn: e.target.value })} placeholder="영문 이름" className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
                  <input value={personForm.aliasesJson} onChange={e => setPersonForm({ ...personForm, aliasesJson: e.target.value })} placeholder='["구글 CEO"]' className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
                </div>
                <button onClick={() => createPerson.mutate()} disabled={!personForm.personId || !personForm.nameKo || createPerson.isPending} className="px-4 py-2 rounded-xl bg-cyan-600 text-white font-bold text-xs shadow-sm hover:bg-cyan-700 disabled:opacity-50">
                  {createPerson.isPending ? '등록 중...' : '인물 신규 등록'}
                </button>
                <div className="pt-2">
                  <label className="block text-xs font-bold text-slate-700 mb-1">관리할 인물 선택</label>
                  <select value={selectedPersonId} onChange={e => setSelectedPersonId(e.target.value)} className="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900">
                    <option value="">인물을 선택하세요</option>
                    {people.map(person => <option key={person.personId} value={person.personId}>{person.nameKo} ({person.personId})</option>)}
                  </select>
                </div>
              </div>

              {/* 2. 권리확인 사진 업로드 */}
              <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-3">
                <h3 className="text-sm font-bold text-slate-900 uppercase tracking-wider">2. 권리확인 사진 업로드</h3>
                <input type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setPhotoForm({ ...photoForm, file: e.target.files?.[0] || null })} className="block w-full text-xs font-bold text-slate-700 file:mr-3 file:rounded-xl file:border-0 file:bg-cyan-600 file:px-3 file:py-2 file:text-xs file:font-bold file:text-white" />
                <div className="grid grid-cols-2 gap-2">
                  <select value={photoForm.licenseType} onChange={e => setPhotoForm({ ...photoForm, licenseType: e.target.value })} className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900">
                    {PERSON_LICENSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                  <input value={photoForm.licenseRef} onChange={e => setPhotoForm({ ...photoForm, licenseRef: e.target.value })} placeholder={licenseNeedsRef ? '출처 URL·계약 번호 (필수)' : '출처 메모 (선택)'} className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-900" />
                </div>
                <button onClick={() => uploadPhoto.mutate()} disabled={!selectedPersonId || !photoForm.file || uploadPhoto.isPending} className="px-4 py-2 rounded-xl bg-amber-600 text-white font-bold text-xs shadow-sm hover:bg-amber-700 disabled:opacity-50">
                  {uploadPhoto.isPending ? '업로드 중...' : '검토 대기 사진 등록'}
                </button>
              </div>
            </div>

            {/* 3. 사진 검토 및 승인 목록 */}
            <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold text-slate-900 uppercase tracking-wider">3. 사진 검토 및 승인 현황</h3>
                <span className="text-xs font-bold text-slate-500">{photos.length}장</span>
              </div>
              {photos.length === 0 ? (
                <div className="py-12 text-center text-xs font-bold text-slate-400">선택한 인물의 등록 사진이 존재하지 않습니다.</div>
              ) : (
                <div className="grid grid-cols-3 gap-4">
                  {photos.map(photo => (
                    <div key={photo.photoId} className="bg-slate-50 border border-slate-200 rounded-xl overflow-hidden shadow-xs">
                      <img src={`/api/assets/person/${selectedPersonId}/photos/${photo.photoId}/content`} alt="" className="w-full aspect-video object-cover bg-black" />
                      <div className="p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold text-slate-800">{photo.licenseType}</span>
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${photo.approved ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>
                            {photo.approved ? '승인됨' : '검토대기'}
                          </span>
                        </div>
                        <div className="flex gap-2 pt-1">
                          <button onClick={() => reviewPhoto.mutate({ photoId: photo.photoId, action: 'approve' })} className="flex-1 bg-emerald-600 text-white rounded py-1.5 text-xs font-bold">승인</button>
                          <button onClick={() => reviewPhoto.mutate({ photoId: photo.photoId, action: 'reject' })} className="flex-1 bg-rose-600 text-white rounded py-1.5 text-xs font-bold">반려</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB 5: YouTube 레퍼런스 채널 관리 */}
        {activeTab === 'reference-channels' && <ReferenceChannelManager />}
      </div>
    </Layout>
  )
}

function ProviderBadge({ label, configured }) {
  const unavailable = configured === undefined
  return (
    <div className="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 flex items-center justify-between shadow-2xs">
      <span className="text-slate-800 font-bold">{label}</span>
      <span className={`text-xs font-bold px-2.5 py-1 rounded-lg ${unavailable ? 'bg-rose-100 text-rose-800' : configured ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>
        {unavailable ? '확인 실패' : configured ? '연결됨' : '키 미설정'}
      </span>
    </div>
  )
}
