import { useContext } from 'react'
import { AuthContext } from '../auth/AuthContext'

export function useAuth() {
  return useContext(AuthContext)
}
