import { useState, useEffect, type ReactNode } from 'react'
import {
  changeInitialPassword as apiChangeInitialPassword,
  login as apiLogin,
  fetchMe,
  logout as apiLogout,
  type User,
} from '../api/auth'
import { AuthContext } from './authContextValue'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('access_token')
    if (token) {
      fetchMe()
        .then(setUser)
        .catch(() => {
          apiLogout()
          setUser(null)
        })
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  const login = async (email: string, password: string) => {
    const result = await apiLogin(email, password)
    if (result.user) setUser(result.user)
    return { passwordChangeToken: result.passwordChangeToken }
  }

  const changeInitialPassword = async (token: string, password: string) => {
    const u = await apiChangeInitialPassword(token, password)
    setUser(u)
  }

  const logout = () => {
    apiLogout()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, changeInitialPassword, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
