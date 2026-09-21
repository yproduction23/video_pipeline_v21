import apiClient from './client'

export const discoveryApi = {
  hotKeywords: (category = 'ALL', window = '48h') =>
    apiClient.get('/trending/hot-keywords', { params: { category, window } }).then(r => r.data),
  recentUploads: (ownerChannelId, days = 7) =>
    apiClient.get('/reference-channels/recent-uploads', { params: { ownerChannelId: ownerChannelId || undefined, days } }).then(r => r.data),
  createFromBenchmark: (videoId, job) =>
    apiClient.post('/jobs/from-benchmark', { videoId, job }).then(r => r.data),
}
