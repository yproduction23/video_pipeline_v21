import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import Jobs from './pages/Jobs'
import NewLongform from './pages/NewLongform'
import JobDetail from './pages/JobDetail'
import Shorts from './pages/Shorts'
import ShortsLibrary from './pages/ShortsLibrary'
import Admin from './pages/Admin'
import ProtectedRoute from './components/ProtectedRoute'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />

          <Route path="/dashboard" element={
            <ProtectedRoute><Dashboard /></ProtectedRoute>
          } />
          <Route path="/longform" element={
            <ProtectedRoute><Jobs /></ProtectedRoute>
          } />
          <Route path="/longform/new" element={
            <ProtectedRoute><NewLongform /></ProtectedRoute>
          } />
          <Route path="/longform/:id" element={
            <ProtectedRoute><JobDetail /></ProtectedRoute>
          } />
          <Route path="/shorts" element={
            <ProtectedRoute><ShortsLibrary /></ProtectedRoute>
          } />
          <Route path="/shorts/new" element={
            <ProtectedRoute><Shorts /></ProtectedRoute>
          } />
          <Route path="/shorts/:shortsJobId" element={
            <ProtectedRoute><Shorts /></ProtectedRoute>
          } />
          <Route path="/longform/:id/shorts" element={
            <ProtectedRoute><Shorts /></ProtectedRoute>
          } />
          {/* 기존 공유 링크는 새 롱폼 경로로 안전하게 넘깁니다. */}
          <Route path="/jobs" element={<Navigate to="/longform" replace />} />
          <Route path="/jobs/new" element={<Navigate to="/longform/new" replace />} />
          <Route path="/jobs/:id/shorts" element={<LegacyJobRedirect suffix="/shorts" />} />
          <Route path="/jobs/:id" element={<LegacyJobRedirect />} />
          <Route path="/admin" element={
            <ProtectedRoute adminOnly><Admin /></ProtectedRoute>
          } />

          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

function LegacyJobRedirect({ suffix = '' }) {
  const id = window.location.pathname.split('/')[2]
  return <Navigate to={`/longform/${id}${suffix}`} replace />
}
