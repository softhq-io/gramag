import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  fetchFleetDashboard,
  fetchFleetCustomers,
  type FleetDashboard,
  type FleetCustomer,
  type MachineRisk,
} from '../api/fleet'

const PAGE_SIZE = 100

export function FleetDashboardPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [dashboard, setDashboard] = useState<FleetDashboard | null>(null)
  const [customers, setCustomers] = useState<FleetCustomer[]>([])
  const [selectedCustomer, setSelectedCustomer] = useState<string>('')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchFleetCustomers().then(setCustomers).catch(() => {})
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setOffset(0)
      setDebouncedSearch(search.trim())
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    setIsLoading(true)
    setError(null)
    fetchFleetDashboard({
      customerId: selectedCustomer || undefined,
      limit: PAGE_SIZE,
      offset,
      q: debouncedSearch || undefined,
    })
      .then(setDashboard)
      .catch(e => setError(e.message))
      .finally(() => setIsLoading(false))
  }, [selectedCustomer, debouncedSearch, offset])

  if (isLoading) {
    return (
      <div className="briefing-loading">
        <div className="loading-spinner" />
        <p>{t('fleet.loading', 'Loading fleet data...')}</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="briefing-error">
        <p>{error}</p>
      </div>
    )
  }

  if (!dashboard) return null

  const { summary, machines, pagination } = dashboard
  const rangeStart = summary.total === 0 ? 0 : pagination.offset + 1
  const rangeEnd = pagination.offset + pagination.returned

  return (
    <div className="fleet-dashboard">
      <div className="fleet-header">
        <h1 className="fleet-title">{t('fleet.title')}</h1>
        <div className="fleet-filters">
          <input
            className="fleet-search-input"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('fleet.search', 'Search machines')}
          />
          <select
            className="fleet-customer-select"
            value={selectedCustomer}
            onChange={e => {
              setOffset(0)
              setSelectedCustomer(e.target.value)
            }}
          >
            <option value="">{t('fleet.allCustomers')}</option>
            {customers.map(c => (
              <option key={c.erp_id} value={c.erp_id}>
                {c.name} ({c.machine_count})
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="fleet-summary">
        <div className="fleet-summary-card">
          <div className="fleet-summary-value">{summary.total}</div>
          <div className="fleet-summary-label">{t('fleet.total')}</div>
        </div>
        <div className="fleet-summary-card fleet-summary-critical">
          <div className="fleet-summary-value">{summary.critical}</div>
          <div className="fleet-summary-label">{t('fleet.critical')}</div>
        </div>
        <div className="fleet-summary-card fleet-summary-warning">
          <div className="fleet-summary-value">{summary.warning}</div>
          <div className="fleet-summary-label">{t('fleet.warning')}</div>
        </div>
        <div className="fleet-summary-card fleet-summary-good">
          <div className="fleet-summary-value">{summary.good}</div>
          <div className="fleet-summary-label">{t('fleet.good')}</div>
        </div>
      </div>

      <div className="fleet-pagination">
        <span>
          {rangeStart}-{rangeEnd} / {summary.total}
        </span>
        <div className="fleet-pagination-buttons">
          <button
            type="button"
            className="fleet-page-button"
            disabled={pagination.offset === 0 || isLoading}
            onClick={() => setOffset(Math.max(0, pagination.offset - PAGE_SIZE))}
          >
            {t('fleet.previous', 'Previous')}
          </button>
          <button
            type="button"
            className="fleet-page-button"
            disabled={!pagination.has_more || isLoading}
            onClick={() => setOffset(pagination.offset + PAGE_SIZE)}
          >
            {t('fleet.next', 'Next')}
          </button>
        </div>
      </div>

      <div className="fleet-machine-list">
        {machines.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">&#9881;</div>
            <p>{t('fleet.noData')}</p>
          </div>
        )}
        {machines.map((m, i) => (
          <MachineRow key={m.erp_id} machine={m} index={i} navigate={navigate} t={t} />
        ))}
      </div>
    </div>
  )
}

function MachineRow({
  machine: m,
  index,
  navigate,
  t,
}: {
  machine: MachineRisk
  index: number
  navigate: (path: string) => void
  t: (key: string) => string
}) {
  const riskClass = m.risk_level === 'critical' ? 'risk-critical'
    : m.risk_level === 'warning' ? 'risk-warning' : 'risk-good'

  return (
    <div
      className="fleet-machine-row"
      style={{ animationDelay: `${index * 30}ms` }}
      onClick={() => navigate(`/mission/${m.erp_id}`)}
    >
      <span className={`fleet-machine-dot ${riskClass}`} />
      <div className="fleet-machine-info">
        <span className="fleet-machine-name">{m.name || m.erp_id}</span>
        {m.customer && <span className="fleet-machine-customer">{m.customer}</span>}
      </div>
      <div className="fleet-machine-meta">
        {m.last_service && (
          <span className="fleet-machine-date">
            {t('fleet.lastService')}: {m.last_service}
          </span>
        )}
      </div>
      <div className="fleet-machine-risk">
        <span className={`fleet-risk-score ${riskClass}`}>{m.risk_score}</span>
        <div className="risk-bar">
          <div
            className={`risk-bar-fill ${riskClass}`}
            style={{ width: `${m.risk_score}%` }}
          />
        </div>
      </div>
    </div>
  )
}
