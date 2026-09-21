import { useNavigate } from 'react-router-dom'
import { ArrowRight, ChevronLeft, Sparkles } from 'lucide-react'
import Layout from '../components/Layout'
import BenchmarkDiscovery from '../components/dashboard/BenchmarkDiscovery'

export default function LongformStart() {
  const navigate = useNavigate()
  return (
    <Layout>
      <div className="w-full max-w-none space-y-6">
        <div className="flex items-center justify-between pb-4 border-b border-slate-200">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/longform')}
              className="p-2 rounded-xl bg-white border border-slate-300 text-slate-700 hover:bg-slate-100 transition-all shadow-sm"
              title="목록으로 돌아가기"
            >
              <ChevronLeft size={20} />
            </button>
            <div>
              <h1 className="text-2xl font-bold text-slate-900 tracking-tight">새 영상 콘텐츠 생성</h1>
              <p className="text-xs font-medium text-slate-600 mt-0.5">
                잘 되는 영상을 고르면, 그 영상을 기준으로 바로 제작을 시작합니다.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-cyan-50 border border-cyan-200 text-cyan-800 text-xs font-bold shadow-sm">
            <Sparkles size={14} className="text-cyan-600" /> 벤치마크로 시작
          </div>
        </div>

        <BenchmarkDiscovery />

        <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-5 py-4">
          <p className="text-xs font-semibold text-slate-600">참고할 영상 없이 이미 정한 주제로 만들려면 직접 입력으로 시작하세요.</p>
          <button
            type="button"
            onClick={() => navigate('/longform/new?manual=1')}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-300 px-4 py-2 text-xs font-bold text-slate-800 hover:bg-slate-50"
          >
            키워드를 이미 정했다면 직접 입력 <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </Layout>
  )
}
