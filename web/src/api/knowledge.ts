import { del, get, patch, post, postForm } from './client'

export interface KnowledgeClient {
  id: string
  name: string
  machine_count: number
  active: boolean
  selected_machine_count: number
}

export interface KnowledgeMachine {
  erp_id: string
  name: string
  serial?: string | null
  number?: string | null
  selected: boolean
  slug?: string | null
  document_count: number
  ready_document_count: number
  legacy?: boolean
}

export type DocumentStatus = 'queued' | 'processing' | 'ready' | 'failed' | 'deleting' | 'purging'

export interface ManagedDocument {
  id: string
  name: string
  kind: 'pdf' | 'text' | 'image'
  category: string
  size: number
  source: 'upload' | 'sharepoint_legacy'
  status: DocumentStatus
  phase: string
  progress_current: number
  progress_total: number
  retry_count: number
  queue_position?: number | null
  error_message?: string | null
  uploaded_by_id?: string | null
  uploaded_at?: string | null
  updated_at?: string | null
  machine_erp_id: string
  machine_slug: string
  client_id: string
}

export const listKnowledgeClients = (includeInactive = false, q = '') => {
  const params = new URLSearchParams()
  if (includeInactive) params.set('include_inactive', 'true')
  if (q) params.set('q', q)
  return get<KnowledgeClient[]>(`/knowledge/clients${params.size ? `?${params}` : ''}`)
}

export const setKnowledgeClientActive = (id: string, active: boolean) =>
  patch<KnowledgeClient>(`/knowledge/clients/${encodeURIComponent(id)}`, { active })

export const listKnowledgeMachines = (clientId: string, q = '') => {
  const suffix = q ? `?q=${encodeURIComponent(q)}` : ''
  return get<KnowledgeMachine[]>(`/knowledge/clients/${encodeURIComponent(clientId)}/machines${suffix}`)
}

export const setKnowledgeMachineSelected = (clientId: string, machineId: string, selected: boolean) =>
  patch<KnowledgeMachine>(
    `/knowledge/clients/${encodeURIComponent(clientId)}/machines/${encodeURIComponent(machineId)}`,
    { selected },
  )

export const listManagedDocuments = (machineId: string) =>
  get<ManagedDocument[]>(`/knowledge/machines/${encodeURIComponent(machineId)}/documents`)

export const getManagedDocument = (id: string) =>
  get<ManagedDocument>(`/knowledge/documents/${encodeURIComponent(id)}`)

export const uploadManagedDocument = (machineId: string, category: string, file: File) => {
  const body = new FormData()
  body.set('category', category)
  body.set('file', file)
  return postForm<ManagedDocument>(
    `/knowledge/machines/${encodeURIComponent(machineId)}/documents`,
    body,
  )
}

export const retryManagedDocument = (id: string) =>
  post<ManagedDocument>(`/knowledge/documents/${encodeURIComponent(id)}/retry`, {})

export const deleteManagedDocument = (id: string) =>
  del(`/knowledge/documents/${encodeURIComponent(id)}`)

export const bulkDeleteManagedDocuments = (machineId: string, documentIds: string[]) =>
  post<{ accepted: number; document_ids: string[]; undo_seconds: number; deletion_not_before: string }>(
    `/knowledge/machines/${encodeURIComponent(machineId)}/documents/bulk-delete`,
    { document_ids: documentIds },
  )

export const cancelBulkDeleteManagedDocuments = (machineId: string) =>
  post<{ restored: number }>(
    `/knowledge/machines/${encodeURIComponent(machineId)}/documents/bulk-delete/cancel`,
    {},
  )
