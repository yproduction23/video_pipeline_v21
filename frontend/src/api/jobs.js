import apiClient from './client'

export const jobsApi = {
  list: () => apiClient.get('/jobs').then(r => r.data),
  myList: () => apiClient.get('/jobs/my').then(r => r.data),
  get: (id) => apiClient.get(`/jobs/${id}`).then(r => r.data),
  create: (data) => apiClient.post('/jobs', data).then(r => r.data),
  retryFromScript: (id) => apiClient.post(`/jobs/${id}/retry/script`).then(r => r.data),

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
  confirmScript: (id, script, sections = []) =>
    apiClient.post(`/jobs/${id}/script/confirm`, { finalScript: script, sections }).then(r => r.data),

  // TTS
  generateTts: (id, voiceId = 'default_ko') =>
    apiClient.post(`/jobs/${id}/tts/generate`, { voiceId }).then(r => r.data),
  selectTtsVoice: (id, voiceId) =>
    apiClient.post(`/jobs/${id}/tts/select-voice`, { voiceId }).then(r => r.data),
  confirmTts: (id) => apiClient.post(`/jobs/${id}/tts/confirm`, {}).then(r => r.data),

  // 이미지
  generateImages: (id) => apiClient.post(`/jobs/${id}/images/generate`).then(r => r.data),
  confirmImages: (id) => apiClient.post(`/jobs/${id}/images/confirm`, {}).then(r => r.data),
  updateSceneImage: (id, index, payload) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}`, payload).then(r => r.data),
  splitScene: (id, index, part1, part2) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}/split`, { part1, part2 }).then(r => r.data),
  setSceneKling: (id, index, enabled) =>
    apiClient.post(`/jobs/${id}/images/scenes/${index}/kling`, { enabled }).then(r => r.data),

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
