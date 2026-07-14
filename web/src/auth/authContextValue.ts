import { createContext } from 'react'
import type { User } from '../api/auth'

interface AuthCtx {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<{ passwordChangeToken?: string }>
  changeInitialPassword: (token: string, password: string) => Promise<void>
  logout: () => void
}

export const AuthContext = createContext<AuthCtx>({
  user: null,
  loading: true,
  login: async () => ({}),
  changeInitialPassword: async () => {},
  logout: () => {},
})
