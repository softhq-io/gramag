import { Navigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export function SuperadminRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user || user.role !== 'superadmin') return <Navigate to="/" replace />
  return <>{children}</>
}
