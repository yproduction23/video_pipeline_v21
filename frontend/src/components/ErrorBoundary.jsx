import React from 'react'

// 화면 그리는 중 예외가 나면 흰 화면 대신 원인을 보여 준다(2026-10-18: 새 후편집 종류를 계획서 화면이 처리하지 못해 흰 화면이 됐다).
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('화면 오류', error, info?.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="m-6 rounded-xl border border-rose-300 bg-rose-50 p-5 text-sm text-slate-900">
        <p className="mb-2 text-base font-bold text-rose-700">화면을 그리는 중 오류가 났습니다.</p>
        <pre className="mb-3 whitespace-pre-wrap rounded bg-white p-3 text-xs text-rose-800">{String(this.state.error?.stack || this.state.error)}</pre>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-lg bg-rose-600 px-3 py-1.5 font-bold text-white"
        >
          새로고침
        </button>
        <span className="ml-3 text-slate-600">위 문구를 개발자에게 알려 주세요.</span>
      </div>
    )
  }
}
