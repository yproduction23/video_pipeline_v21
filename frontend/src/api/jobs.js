import apiClient from './client'

export const jobsApi = {
  list: () => apiClient.get('/jobs').then(r => r.data),
  myList: () => apiClient.get('/jobs/my').then(r => r.data),
  get: (id) => apiClient.get(`/jobs/${id}`).then(r => r.data),
  create: (data) => apiClient.post('/jobs', data).then(r => r.data),
  retryFromScript: (id) => apiClient.post(`/jobs/${id}/retry/script`).then(r => r.data),
  // 승인한 대본·TTS·이미지 단계를 다시 연다(그 단계와 뒤 단계의 승인 해제)
  rewindGate: (id, gate) => apiClient.post(`/jobs/${id}/gates/${gate}/rewind`).then(r => r.data),

  // Assets — 페이지 재진입 시 서버 상태 복원
  assets: (id, type) => {
    const url = type ? `/jobs/${id}/assets?type=${type}` : `/jobs/${id}/assets`
    return apiClient.get(url).then(r => r.data)
  },

  // 키워드
  searchKeyword: (id, seed, limit = 5) =>
    apiClient.post(`/jobs/${id}/keyword/search`, { seedKeyword: seed, limit }).then(r => r.data),
  confirmKeyword: (id, keyword) =>
    apiClient.post(`/jobs/${id}/keyword/confirm`, { selectedKeyword: keyword }).then(r => r.data),

  // 트렌딩
  trendingYoutube: (keyword, { ranking = 'evidence', minSubscribers = 0, channelId = '' } = {}) =>
    apiClient.get(`/trending/youtube`, { params: { keyword, ranking, minSubscribers, channelId: channelId || undefined } }).then(r => r.data),

  // 스크립트
  generateScript: (id) => apiClient.post(`/jobs/${id}/script/generate`).then(r => r.data),
  revalidateScript: (id) => apiClient.post(`/jobs/${id}/script/revalidate`).then(r => r.data),
  reclassifyScript: (id) => apiClient.post(`/jobs/${id}/script/reclassify`).then(r => r.data),
  confirmScript: (id, script, sections = []) =>
    apiClient.post(`/jobs/${id}/script/confirm`, { finalScript: script, sections }).then(r => r.data),

  // TTS
  generateTts: (id, voiceId = 'default_ko') =>
    apiClient.post(`/jobs/${id}/tts/generate`, { voiceId }).then(r => r.data),
  selectTtsVoice: (id, voiceId) =>
    apiClient.post(`/jobs/${id}/tts/select-voice`, { voiceId }).then(r => r.data),
  confirmTts: (id) => apiClient.post(`/jobs/${id}/tts/confirm`, {}).then(r => r.data),
  deleteTts: (id) => apiClient.delete(`/jobs/${id}/tts`).then(r => r.data),

  // 이미지
  // sceneIndices를 지정하면 검토 필요 목록에서 고른 씬만 재시도한다.
  generateImages: (id, sceneIndices) =>
    apiClient.post(`/jobs/${id}/images/generate`, sceneIndices?.length ? { sceneIndices } : {}).then(r => r.data),
  // 슬라이드형: 편집 계획서만 만든다(이미지·영상은 만들지 않아 비용이 나가지 않는다).
  buildEditPlan: (id) => apiClient.post(`/jobs/${id}/images/plan`, {}).then(r => r.data),
  // 계획서에서 문제로 표시한 장면({index, note})만 다시 계획한다(이미지는 만들지 않는다).
  replanEditPlan: (id, items) => apiClient.post(`/jobs/${id}/images/plan/replan`, { items }).then(r => r.data),
  confirmImages: (id) => apiClient.post(`/jobs/${id}/images/confirm`, {}).then(r => r.data),
  updateSceneImage: (id, index, payload) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}`, payload).then(r => r.data),
  splitScene: (id, index, part1, part2) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}/split`, { part1, part2 }).then(r => r.data),
  setSceneKling: (id, index, enabled) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}/kling`, { enabled }).then(r => r.data),
  acceptHeldScene: (id, index) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}/accept`, {}).then(r => r.data),
  approveSceneRetry: (id, index, overrideCooldown = false) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}/approve-retry`, { overrideCooldown }).then(r => r.data),

  // 롱폼
  generateLongform: (id) => apiClient.post(`/jobs/${id}/longform/generate`).then(r => r.data),
  confirmLongform: (id) => apiClient.post(`/jobs/${id}/longform/confirm`, {}).then(r => r.data),
  rebuildLongform: (id) => apiClient.post(`/jobs/${id}/longform/rebuild`, {}).then(r => r.data),
  selectThumbnailVariant: (id, format, variant) =>
    apiClient.post(`/jobs/${id}/thumbnail/${format}/select`, null, { params: { variant } }).then(r => r.data),
  regenerateThumbnail: (id, format, preset) =>
    apiClient.post(`/jobs/${id}/thumbnail/regenerate`, null, { params: { format, ...(preset ? { preset } : {}) } }).then(r => r.data),
  publish: (id) => apiClient.post(`/jobs/${id}/publish`).then(r => r.data),

  // 게이트
  approvals: (id) => apiClient.get(`/jobs/${id}/approvals`).then(r => r.data),
  approve: (id, gate, comment = '') =>
    apiClient.post(`/jobs/${id}/gates/${gate}/approve`, { comment }).then(r => r.data),
  reject: (id, gate, comment = '') =>
    apiClient.post(`/jobs/${id}/gates/${gate}/reject`, { comment }).then(r => r.data),

  // 비용
  costs: (id) => apiClient.get(`/jobs/${id}/costs/summary`).then(r => r.data),

  // 작업 제어
  stop: (id) => apiClient.post(`/jobs/${id}/stop`).then(r => r.data),
  delete: (id) => apiClient.delete(`/jobs/${id}`).then(r => r.data),
}
