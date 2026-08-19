import { Navigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export function KnowledgeManagerRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user || (user.role !== 'superadmin' && user.role !== 'all_clients')) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}
