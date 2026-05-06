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

export function fetchFleetDashboard(customerId?: string) {
  const qs = customerId ? `?customer_id=${encodeURIComponent(customerId)}` : ''
  return get<FleetDashboard>(`/fleet/dashboard${qs}`)
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
