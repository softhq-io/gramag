import { useContext } from 'react'
import { AuthContext } from '../auth/authContextValue'

export function useAuth() {
  return useContext(AuthContext)
}
