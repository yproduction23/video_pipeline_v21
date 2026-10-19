import { useState } from 'react'
import MotifLibrary from './MotifLibrary'
import IconLibrary from './IconLibrary'

// 연상 소재·아이콘 라이브러리 — 2026-10-19 운영자 요청: 소재와 아이콘 모두 화면에서 늘린다.
const SECTIONS = [
  { id: 'motifs', label: '연상 소재' },
  { id: 'icons', label: '아이콘' },
]

export default function VisualLibrary() {
  const [section, setSection] = useState('motifs')
  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1 text-xs font-bold">
        {SECTIONS.map(s => (
          <button key={s.id} type="button" onClick={() => setSection(s.id)}
            className={`rounded-md px-4 py-1.5 ${section === s.id ? 'bg-slate-800 text-white' : 'text-slate-600'}`}>{s.label}</button>
        ))}
      </div>
      {section === 'motifs' ? <MotifLibrary /> : <IconLibrary />}
    </div>
  )
}
