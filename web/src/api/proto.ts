import { get, post } from './client'

export interface ProtoMachine {
  slug: string
  folder: string
  type: string | null
  model: string | null
  serial: string | null
  customer?: string | null
  docs: number
  pdfs: number
  imgs: number
  txts: number
  sections: number
}

export interface ProtoCitation {
  idx: number
  kind: 'page' | 'config' | 'image'
  machine: string
  doc: string
  page?: number
  name?: string
  section_id?: string
  score?: number
}

export interface ProtoHit {
  label: string
  id: string
  score: number
  machine_slug: string
  machine_folder: string
  doc_name: string
  doc_kind: string
  category: string
  document_id: string
  page?: number
  text?: string
  vision_desc?: string
  png_path?: string
  name?: string
  caption?: string
  summary?: string
}

export interface ProtoAnswerResponse {
  answer: string
  citations: ProtoCitation[]
  hits: ProtoHit[]
  model?: string
  chat_session_id?: string
  assistant_message_id?: string
  memory_count?: number
}

export interface ProtoChatSession {
  id: string
  machine_slug?: string | null
  customer?: string | null
  title: string
  created_at: string
  updated_at: string
  created_by: string
  message_count?: number
  last_message_at?: string | null
}

export interface ProtoChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  text: string
  created_at: string
  username?: string | null
  user_role?: string | null
  model?: string | null
  citations?: ProtoCitation[]
  hits?: ProtoHit[]
}

export interface ProtoChatDetail {
  session: ProtoChatSession
  messages: ProtoChatMessage[]
}

export interface ProtoChatMessageResponse {
  session: ProtoChatSession
  user_message: ProtoChatMessage
  assistant_message: ProtoChatMessage
  answer: string
  citations: ProtoCitation[]
  hits: ProtoHit[]
  model?: string
  memory_count?: number
}

export function listProtoMachines(): Promise<ProtoMachine[]> {
  return get('/proto/machines')
}

export interface CustomerOverview {
  customer: { name: string; tagline: string; machine_count: number }
  stats: {
    machines: number
    documents: number
    pages: number
    images: number
    configs: number
  }
  machines: (ProtoMachine & {
    hersteller: string
    sample_docs?: { name: string; kind: string; category: string; pages: number }[]
  })[]
}

export function getCustomerOverview(): Promise<CustomerOverview> {
  return get('/proto/customer')
}

export function askProto(body: {
  query: string
  machine_slug?: string | null
  customer?: string | null
  top_k?: number
  deep?: boolean
}): Promise<ProtoAnswerResponse> {
  return post('/proto/ask', body)
}

export function createProtoChat(body: {
  machine_slug?: string | null
  customer?: string | null
  title?: string | null
}): Promise<ProtoChatSession> {
  return post('/proto/chats', body)
}

export function listProtoChats(params: {
  machine_slug?: string | null
  customer?: string | null
} = {}): Promise<ProtoChatSession[]> {
  const qs = new URLSearchParams()
  if (params.machine_slug) qs.set('machine_slug', params.machine_slug)
  if (params.customer) qs.set('customer', params.customer)
  return get(`/proto/chats${qs.toString() ? `?${qs.toString()}` : ''}`)
}

export function getProtoChat(id: string): Promise<ProtoChatDetail> {
  return get(`/proto/chats/${id}`)
}

export function sendProtoChatMessage(
  id: string,
  body: { text: string; top_k?: number; deep?: boolean },
): Promise<ProtoChatMessageResponse> {
  return post(`/proto/chats/${id}/messages`, body)
}

export function getProtoSection(id: string) {
  return get<{
    id: string
    page: number
    text: string
    vision_desc: string
    merged: string
    png_path: string
    doc_name: string
    doc_id: string
    machine: string
    machine_slug: string
  }>(`/proto/section/${id}`)
}

export function protoPageImageUrl(sectionId: string): string {
  const token = localStorage.getItem('access_token')
  return `/api/proto/page-image/${sectionId}${token ? `?t=${token.slice(0, 8)}` : ''}`
}
