import { useTranslation } from 'react-i18next'
import type { MachineDetail } from '../api/mission'

export function MachineInfoCard({ machine }: { machine: MachineDetail }) {
  const { t } = useTranslation()
  return (
    <div className="card machine-card">
      <h2 className="card-title">{machine.title}</h2>
      <div className="machine-meta">
        <div className="meta-row">
          <span className="meta-label">{t('briefing.customer')}</span>
          <span>{machine.customer} {machine.city && `(${machine.city})`}</span>
        </div>
        <div className="meta-row">
          <span className="meta-label">{t('briefing.type')}</span>
          <span className="badge badge-type">{machine.machine_type || '—'}</span>
        </div>
        <div className="meta-row">
          <span className="meta-label">{t('briefing.brand')}</span>
          <span className="badge badge-brand">{machine.brand || '—'}</span>
        </div>
        <div className="meta-row">
          <span className="meta-label">{t('briefing.serial')}</span>
          <span className="mono">{machine.serial_number || '—'}</span>
        </div>
      </div>
    </div>
  )
}
