import { get, post } from './client'

export interface ProtoMachine {
  slug: string
  folder: string
  type: string | null
  model: string | null
  serial: string | null
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
  top_k?: number
  deep?: boolean
}): Promise<ProtoAnswerResponse> {
  return post('/proto/ask', body)
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
