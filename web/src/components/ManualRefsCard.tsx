import { useTranslation } from 'react-i18next'
import type { ManualRef } from '../api/mission'

export function ManualRefsCard({ manuals }: { manuals: ManualRef[] }) {
  const { t } = useTranslation()

  if (!manuals.length) {
    return (
      <div className="card">
        <h3 className="card-header">
          <span className="card-icon">&#128214;</span>
          {t('briefing.manuals')}
        </h3>
        <div className="empty-state">
          <div className="empty-state-icon">&#128215;</div>
          <p>{t('briefing.manualsEmpty')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <h3 className="card-header">
        <span className="card-icon">&#128214;</span>
        {t('briefing.manuals')}
        <span className="card-count">{manuals.length}</span>
      </h3>
      <div className="manuals-list">
        {manuals.map((m, i) => (
          <div key={i} className="manual-item">
            <div className="manual-title">
              {m.title}
              {m.brand_match && (
                <span className="badge badge-match">{t('briefing.brandMatch')}</span>
              )}
            </div>
            <div className="manual-meta">
              <span className="manual-supplier">{m.supplier}</span>
              <span className="manual-score">{m.score}</span>
            </div>
            {m.snippet && <p className="manual-summary">{m.snippet}</p>}
          </div>
        ))}
      </div>
    </div>
  )
}
