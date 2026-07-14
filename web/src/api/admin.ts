import { get, patch, post } from './client'
import type { UserRole } from './auth'

export interface AdminUser {
  id: string
  email: string
  name: string
  role: UserRole
  active: boolean
  must_change_password: boolean
  client_ids: string[]
  created_at?: string | null
  updated_at?: string | null
  last_login_at?: string | null
}

export interface AdminClient {
  id: string
  name: string
  machine_count: number
}

export interface UserWrite {
  name?: string
  role?: UserRole
  active?: boolean
  client_ids?: string[]
}

export const listUsers = () => get<AdminUser[]>('/admin/users')
export const listClients = () => get<AdminClient[]>('/admin/clients')

export const createUser = (body: {
  email: string
  name: string
  role: UserRole
  client_ids: string[]
}) => post<{ user: AdminUser; temporary_password: string }>('/admin/users', body)

export const updateUser = (id: string, body: UserWrite) =>
  patch<AdminUser>(`/admin/users/${encodeURIComponent(id)}`, body)

export const resetUserPassword = (id: string) =>
  post<{ temporary_password: string }>(
    `/admin/users/${encodeURIComponent(id)}/reset-password`,
    {},
  )
