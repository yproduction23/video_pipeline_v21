// 콘텐츠 성격(사실성 등급). 백엔드 ContentNature enum과 값이 같다.
export const NATURE_OPTIONS = [
  { value: 'FACTUAL', label: '사실형', desc: '경제·시사·사회 이슈. 뉴스로 검증되는 내용' },
  { value: 'EXPLAINER', label: '해설형', desc: '화제 콘텐츠·밈·인물. 벤치마크 분석과 웹 자료를 근거로, 불확실한 내용은 완화 표현' },
  { value: 'STORY', label: '창작형', desc: '야담·설화·옛날이야기. 전해 내려오는 이야기임을 영상에서 밝힘' },
]

export const natureLabel = value => NATURE_OPTIONS.find(o => o.value === value)?.label || '사실형'

const STORY_TERMS = ['야담', '설화', '전설', '옛날이야기', '옛날 이야기', '민담', '괴담', '동화', '구전']
const FACTUAL_TERMS = [
  '주식', '증시', '코스피', '코스닥', '나스닥', '금리', '환율', '부동산', '경제', '물가', '연준',
  '정치', '선거', '정책', '국회', '대통령', '세금', '연금', '뉴스', '속보',
]

// 제목·채널명으로 등급을 추천한다. 참고용일 뿐 확정은 사용자가 한다.
export function suggestNature(...texts) {
  const text = texts.filter(Boolean).join(' ').toLowerCase()
  if (!text) return null
  if (STORY_TERMS.some(term => text.includes(term))) return 'STORY'
  if (FACTUAL_TERMS.some(term => text.includes(term))) return 'FACTUAL'
  return 'EXPLAINER'
}
