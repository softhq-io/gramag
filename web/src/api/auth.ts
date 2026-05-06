import { post, get } from './client'

export interface User {
  username: string
  role: string
  name: string
}

interface LoginResponse {
  access_token: string
  refresh_token: string
  user: User
}

export async function login(username: string, password: string): Promise<User> {
  const data = await post<LoginResponse>('/auth/login', { username, password })
  localStorage.setItem('access_token', data.access_token)
  localStorage.setItem('refresh_token', data.refresh_token)
  return data.user
}

export async function fetchMe(): Promise<User> {
  return get<User>('/auth/me')
}

export function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
}
