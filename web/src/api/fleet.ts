import { get } from './client'

export interface RiskFactor {
  name: string
  value: number
  weight: number
  contribution: number
}

export interface MachineRisk {
  erp_id: string
  name: string
  customer: string
  customer_id: string
  risk_score: number
  risk_level: 'critical' | 'warning' | 'good'
  last_service: string | null
  next_predicted: string | null
  factors: RiskFactor[]
}

export interface FleetSummary {
  total: number
  critical: number
  warning: number
  good: number
}

export interface FleetDashboard {
  summary: FleetSummary
  pagination: {
    limit: number
    offset: number
    returned: number
    has_more: boolean
  }
  machines: MachineRisk[]
}

export interface PartMTBR {
  part_nummer: string
  part_name: string
  avg_days: number
  last_replaced: string
  next_predicted: string
  confidence: 'high' | 'medium' | 'low'
}

export interface FleetCustomer {
  erp_id: string
  name: string
  machine_count: number
}

export interface FleetDashboardParams {
  customerId?: string
  limit?: number
  offset?: number
  q?: string
}

export function fetchFleetDashboard(params: FleetDashboardParams = {}) {
  const qs = new URLSearchParams()
  if (params.customerId) qs.set('customer_id', params.customerId)
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.offset) qs.set('offset', String(params.offset))
  if (params.q) qs.set('q', params.q)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return get<FleetDashboard>(`/fleet/dashboard${suffix}`)
}

export function fetchFleetCustomers() {
  return get<FleetCustomer[]>('/fleet/customers')
}

export function fetchMachineMTBR(erpId: string) {
  return get<PartMTBR[]>(`/fleet/machine/${encodeURIComponent(erpId)}/mtbr`)
}

export function fetchMachineRisk(erpId: string) {
  return get<MachineRisk>(`/fleet/machine/${encodeURIComponent(erpId)}/risk`)
}
