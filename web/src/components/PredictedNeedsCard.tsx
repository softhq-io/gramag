import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { fetchMachineRisk, fetchMachineMTBR, type MachineRisk, type PartMTBR } from '../api/fleet'

const FACTOR_LABELS: Record<string, string> = {
  serviceInterval: 'predicted.serviceInterval',
  mtbrOverdue: 'predicted.mtbrOverdue',
  frequency: 'predicted.frequency',
  lastType: 'predicted.lastType',
  machineAge: 'predicted.machineAge',
}

const FACTOR_DESC: Record<string, string> = {
  serviceInterval: 'predicted.desc.serviceInterval',
  mtbrOverdue:     'predicted.desc.mtbrOverdue',
  frequency:       'predicted.desc.frequency',
  lastType:        'predicted.desc.lastType',
  machineAge:      'predicted.desc.machineAge',
}

export function PredictedNeedsCard({ erpId }: { erpId: string }) {
  const { t } = useTranslation()
  const [risk, setRisk] = useState<MachineRisk | null>(null)
  const [mtbr, setMtbr] = useState<PartMTBR[]>([])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    setIsLoading(true)
    Promise.all([
      fetchMachineRisk(erpId).catch(() => null),
      fetchMachineMTBR(erpId).catch(() => []),
    ]).then(([riskData, mtbrData]) => {
      setRisk(riskData)
      setMtbr(mtbrData)
      setIsLoading(false)
    })
  }, [erpId])

  if (isLoading) {
    return (
      <div className="card predicted-needs-card">
        <div className="card-header">
          <span className="card-icon">&#128302;</span>
          {t('predicted.title')}
        </div>
        <div className="empty-state">
          <div className="loading-spinner-sm" />
        </div>
      </div>
    )
  }

  if (!risk) {
    return (
      <div className="card predicted-needs-card">
        <div className="card-header">
          <span className="card-icon">&#128302;</span>
          {t('predicted.title')}
        </div>
        <div className="empty-state">{t('fleet.noData')}</div>
      </div>
    )
  }

  const levelClass = risk.risk_level === 'critical' ? 'risk-critical'
    : risk.risk_level === 'warning' ? 'risk-warning' : 'risk-good'

  const levelLabel = risk.risk_level === 'critical' ? t('fleet.critical')
    : risk.risk_level === 'warning' ? t('fleet.warning') : t('fleet.good')

  const now = new Date()

  return (
    <div className="card predicted-needs-card">
      <div className="card-header">
        <span className="card-icon">&#128302;</span>
        {t('predicted.title')}
      </div>

      <div className="predicted-risk-block">
        <div className="predicted-risk-header">
          <span className="predicted-risk-label">{t('predicted.riskScore')}</span>
          <span className={`predicted-risk-value ${levelClass}`}>{risk.risk_score}/100</span>
        </div>
        <div className="risk-bar risk-bar-lg">
          <div
            className={`risk-bar-fill ${levelClass}`}
            style={{ width: `${risk.risk_score}%` }}
          />
        </div>
        <span className={`predicted-level-badge ${levelClass}`}>{levelLabel}</span>
      </div>

      {mtbr.length > 0 && (
        <div className="predicted-mtbr-section">
          <div className="predicted-section-title">{t('predicted.nextParts')}</div>
          {mtbr.slice(0, 5).map(p => {
            const predDate = new Date(p.next_predicted)
            const daysUntil = Math.round((predDate.getTime() - now.getTime()) / 86400000)
            const isOverdue = daysUntil < 0

            const tooltip = `${t('predicted.avgInterval')}: ${p.avg_days} ${t('predicted.days')} · ${t('predicted.lastReplaced')}: ${p.last_replaced}`

            return (
              <div key={p.part_nummer} className="mtbr-item" title={tooltip}>
                <span className="mtbr-part-nummer">{p.part_nummer}</span>
                <span className="mtbr-part-name">{p.part_name}</span>
                <span className={`mtbr-days ${isOverdue ? 'mtbr-overdue' : ''}`}>
                  {isOverdue
                    ? t('predicted.overdue')
                    : `~${daysUntil} ${t('predicted.days')}`}
                </span>
                <span className={`confidence-badge confidence-${p.confidence}`}>
                  {p.confidence}
                </span>
              </div>
            )
          })}
        </div>
      )}

      <div className="predicted-factors-section">
        <div className="predicted-section-title">{t('predicted.factors')}</div>
        <div className="risk-factor-rows">
          {risk.factors.map(f => (
            <div key={f.name} className="rf-row">
              <div className="rf-label">
                <span className="rf-name">{t(FACTOR_LABELS[f.name] || f.name)}</span>
                <span className="rf-desc">{t(FACTOR_DESC[f.name] || '')}</span>
              </div>
              <span className="rf-weight">{Math.round((f.weight ?? 0) * 100)}%</span>
              <div className="rf-value">
                <div className="rf-bar-wrap">
                  <div className="rf-bar-fill" style={{ width: `${f.value * 100}%` }} />
                </div>
                <span className="rf-pct">{Math.round(f.value * 100)}%</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
