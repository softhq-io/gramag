import { get, post } from './client'

export interface MachineSearchResult {
  erp_id: string
  title: string
  serial_number: string
  customer: string
  customer_erp_id: string
  machine_type: string
  brand: string
  score: number
}

export interface CustomerSearchResult {
  erp_id: string
  name: string
  city: string
  machine_count: number
  score: number
}

export interface MachineDetail {
  erp_id: string
  title: string
  serial_number: string
  customer: string
  customer_erp_id: string
  city: string
  machine_type: string
  brand: string
}

export interface Comment {
  author: string | null
  text: string | null
  date: string | null
}

export interface ServiceJob {
  erp_id: string
  title: string
  nummer: string
  date: string
  description: string
  comments: Comment[]
  parts: { nummer: string; titel: string }[]
}

export interface SimilarCase {
  job_erp_id: string
  job_title: string
  job_date: string
  job_nummer: string
  job_description: string
  machine_title: string
  machine_erp_id: string
  customer: string
  machine_type: string
  parts_used: { nummer: string; titel: string }[]
  comments: Comment[]
  symptom_match?: boolean
  llm_summary?: string
}

export interface PartEntry {
  nummer: string
  titel: string
  manufacturer_nr: string
  frequency?: number
  co_count?: number
  job_titles?: string[]
}

export interface PartsKit {
  machine_parts: PartEntry[]
  type_parts: PartEntry[]
  co_occurrence_parts: PartEntry[]
  summary?: string
}

export interface ManualRef {
  title: string
  snippet: string
  supplier: string
  score: number
  brand_match: boolean
}

export interface ReasoningStep {
  step: string
  detail: string
}

export interface Briefing {
  machine: MachineDetail
  symptom: string
  summary: string
  history: ServiceJob[]
  similar_cases: SimilarCase[]
  parts_kit: PartsKit
  manuals: ManualRef[]
  reasoning_path: ReasoningStep[]
  error?: string
}

export interface PartUsage {
  machine: string
  machine_erp_id: string
  customer: string
  job: string
  date: string
}

export interface PartDetail {
  titel: string
  nummer: string
  manufacturer_nr: string
  noise: boolean
  usage_count: number
  usage: PartUsage[]
  manual_refs: string[]
  co_parts: { titel: string; nummer: string }[]
  error?: string
}

export interface FreeAnswerSource {
  rank: number
  source: string
  type: string
  method: string
  score: number
  text: string
  pdf_url: string | null
}

export interface FreeAnswer {
  answer: string
  sources: FreeAnswerSource[]
}

export function askFreeQuestion(query: string) {
  return post<FreeAnswer>('/mission/ask', { query })
}

export function searchMachines(q: string, limit = 10) {
  return get<MachineSearchResult[]>(`/mission/search?q=${encodeURIComponent(q)}&type=machine&limit=${limit}`)
}

export function searchCustomers(q: string, limit = 10) {
  return get<CustomerSearchResult[]>(`/mission/search?q=${encodeURIComponent(q)}&type=customer&limit=${limit}`)
}

export function getMachineDetail(erpId: string) {
  return get<MachineDetail>(`/mission/machine/${encodeURIComponent(erpId)}`)
}

export function getBriefing(machineErpId: string, symptom = '') {
  return post<Briefing>('/mission/briefing', { machine_erp_id: machineErpId, symptom })
}

export function getServiceHistory(erpId: string, limit = 20) {
  return get<ServiceJob[]>(`/mission/machine/${encodeURIComponent(erpId)}/history?limit=${limit}`)
}

export function getPartsKit(erpId: string) {
  return get<PartsKit>(`/mission/machine/${encodeURIComponent(erpId)}/parts-kit`)
}

export function getSimilarCases(erpId: string, symptom = '', limit = 8) {
  return get<SimilarCase[]>(
    `/mission/machine/${encodeURIComponent(erpId)}/similar-cases?symptom=${encodeURIComponent(symptom)}&limit=${limit}`
  )
}

export function getPartDetail(nummer: string) {
  return get<PartDetail>(`/part/${encodeURIComponent(nummer)}`)
}
