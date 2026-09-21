import { useLocation, useSearchParams } from 'react-router-dom'
import JobNew from './JobNew'
import LongformStart from './LongformStart'

// 벤치마크 영상이 정해졌거나 주제가 미리 채워진 진입(?topic, 일일 키워드 추천)은 기존 설정 화면으로 보낸다.
export default function NewLongform() {
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const manual = searchParams.get('manual') === '1' || searchParams.has('topic') || !!location.state?.keywordPlan
  return location.state?.benchmark || manual ? <JobNew /> : <LongformStart />
}
