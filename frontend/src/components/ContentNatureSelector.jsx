import { AlertTriangle, Lightbulb } from 'lucide-react'
import { NATURE_OPTIONS, natureLabel } from '../lib/contentNature'

// 채널 기본값으로 채워진 등급을 작업별로 확정한다. 추천은 참고용이다.
export default function ContentNatureSelector({ value, onChange, suggested, channelDefault }) {
  const differsFromSuggestion = suggested && suggested !== value
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="block text-sm font-bold text-slate-900">콘텐츠 성격</label>
        <span className="text-xs font-semibold text-slate-500">
          채널 기본값: {natureLabel(channelDefault)}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3">
        {NATURE_OPTIONS.map(opt => {
          const selected = value === opt.value
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => onChange(opt.value)}
              className={`text-left p-4 rounded-xl border-2 transition-all ${
                selected ? 'border-cyan-600 bg-cyan-50/80 shadow-md' : 'border-slate-200 bg-slate-50/60 hover:border-slate-300 hover:bg-white'
              }`}
            >
              <p className={`text-xs font-bold ${selected ? 'text-cyan-900' : 'text-slate-900'}`}>{opt.label}</p>
              <p className="text-[11px] font-semibold text-slate-600 mt-1 leading-snug">{opt.desc}</p>
            </button>
          )
        })}
      </div>
      {differsFromSuggestion && (
        <div className="mt-2 flex items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-900">
          <span className="flex items-center gap-1.5">
            {value === 'FACTUAL' ? <AlertTriangle size={13} /> : <Lightbulb size={13} />}
            이 소재는 <b>{natureLabel(suggested)}</b>로 보입니다.
            {value === 'FACTUAL' && ' 사실형으로 만들면 지어낸 내용이 사실처럼 나갈 수 있으니 확인해 주세요.'}
          </span>
          <button type="button" onClick={() => onChange(suggested)} className="ml-3 shrink-0 font-bold underline">
            {natureLabel(suggested)}로 바꾸기
          </button>
        </div>
      )}
    </div>
  )
}
