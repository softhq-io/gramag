import { post, get } from './client'

export type UserRole = 'superadmin' | 'all_clients' | 'user'

export interface User {
  id: string
  email: string
  role: UserRole
  name: string
  active: boolean
  must_change_password: boolean
}

interface LoginResponse {
  access_token?: string
  refresh_token?: string
  password_change_required: boolean
  password_change_token?: string
  user: User
}

export interface LoginResult {
  user?: User
  passwordChangeToken?: string
}

function persistTokens(data: LoginResponse) {
  if (data.access_token) localStorage.setItem('access_token', data.access_token)
  if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token)
}

export async function login(email: string, password: string): Promise<LoginResult> {
  const data = await post<LoginResponse>('/auth/login', { email, password })
  if (data.password_change_required) {
    return { passwordChangeToken: data.password_change_token }
  }
  persistTokens(data)
  return { user: data.user }
}

export async function changeInitialPassword(
  passwordChangeToken: string,
  newPassword: string,
): Promise<User> {
  const data = await post<LoginResponse>('/auth/change-password', {
    password_change_token: passwordChangeToken,
    new_password: newPassword,
  })
  persistTokens(data)
  return data.user
}

export async function fetchMe(): Promise<User> {
  return get<User>('/auth/me')
}

export function logout() {
  void post('/auth/logout', {}).catch(() => {})
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
}
